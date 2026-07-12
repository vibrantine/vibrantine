"""The run Gatekeeper: the internal control object at the provider seam.

One Gatekeeper per run, created by `run_one`, carried to every node by
reference inside `CallContext`, consulted by every governed LLM call and by
`dispatch`. It holds only what is identical for every node: the resource
fuses (LLM-call count, time, spend), the concurrency room, the combined
stop signal, and the provider-call log. Anything a node owns its own slice
of (budget grant, capability grant, model choice) stays a distributed value
on the context.

Three invariants (docs/design.md, "One run, one Gatekeeper at the provider
seam"):

- Calls report in, control flows out. Nodes never read the running totals
  here to decide anything; the log is observational only.
- Shared run state, never grants. A grant in the shared object would reopen
  the "everyone owns the same money" footgun.
- Never swapped mid-tree, enforced: `dispatch` verifies every context
  carries the very same object as the run in progress and refuses
  otherwise.

Deliberately no public name. The caller configures the object entirely
through `run_one` keyword arguments and never holds it: a user who could
construct one could share counters across unrelated runs or hand a subtree
a fresh one, the fuse escape the no-swap rule exists to close.

Trust boundary: an in-process control over the framework's own paths, not a
sandbox. Custom Python can step around it (a raw client, raw file writes,
sockets, a subprocess); those are deliberate escapes the framework does not
claim to prevent. See docs/design.md for the canonical statement.
"""

import asyncio
import contextvars
import logging
import math
import os
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from vibrantine.contract import CancelToken, ErrorState
from vibrantine.models import DEFAULT_MODEL, KNOWN_MODELS, Model, UnknownModelError

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# The run in progress for the current task tree. Set by `run_one` around the
# root dispatch, reset in its finally; ContextVars propagate through await
# and asyncio.gather, so every node in the tree sees the same run. `dispatch`
# reads it for the outside-a-run and swapped-object refusals; `run_one` reads
# it for the nested-entry refusal.
current_gatekeeper: contextvars.ContextVar["Gatekeeper | None"] = contextvars.ContextVar(
    "_vibrantine_current_gatekeeper",
    default=None,
)


class RunHaltedError(Exception):
    """A governed provider call was refused because a run fuse has tripped.

    Internal: raised by the provider door, caught by the LLM machinery and
    converted into a node-level `cancelled` failure value (mid-tree nodes
    ride the ordinary cancellation path; a custom `_run` that lets it bubble
    gets the same translation from dispatch's backstop). Only the root
    speaks `run_halted`: dispatch rewrites a trip-descended root failure
    before persistence.
    """


class RunConfigError(ValueError):
    """A run configuration the Gatekeeper cannot honor, named.

    Internal: raised by `build_catalog`, converted by `run_one` into a
    validation failure result (errors are values at that boundary too).
    """


class MissingUsageError(Exception):
    """A budgeted paid call returned no token usage to price."""


def build_catalog(
    models: Sequence[Model],
    default_model: str | None,
) -> tuple[dict[str, Model], str]:
    """The run's model catalog and its default entry name, validated.

    Define the run's models once, at the front door; every Commission
    references one by name or takes the default resolved here. An empty
    catalog auto-registers the system default so hello world configures
    nothing; a single entry is its own default (naming it twice is busywork);
    several entries need `default_model=` unless the system default is one
    of them.
    """
    catalog: dict[str, Model] = {}
    for model in models:
        for field_name, price in (
            ("input_usd_per_million", model.input_usd_per_million),
            ("output_usd_per_million", model.output_usd_per_million),
        ):
            if price is not None and (not math.isfinite(price) or price < 0):
                raise RunConfigError(
                    f"model {model.id!r} has invalid {field_name}={price!r}; "
                    f"prices must be finite and non-negative (0.0 means free)."
                )
        if model.id in catalog:
            raise RunConfigError(
                f"models= names {model.id!r} more than once; the catalog "
                f"defines each model exactly once."
            )
        catalog[model.id] = model
    if not catalog:
        catalog[DEFAULT_MODEL] = KNOWN_MODELS[DEFAULT_MODEL]
    if default_model is not None:
        if default_model not in catalog:
            raise RunConfigError(
                f"default_model {default_model!r} is not in models= "
                f"(registered: {', '.join(sorted(catalog))})."
            )
        return catalog, default_model
    if len(catalog) == 1:
        return catalog, next(iter(catalog))
    if DEFAULT_MODEL not in catalog:
        raise RunConfigError(
            f"models= has several entries and none is the system default "
            f"{DEFAULT_MODEL!r}; name one with default_model=."
        )
    return catalog, DEFAULT_MODEL


class RunCancel:
    """The run's one stop signal: the caller's token and the breaker combined.

    Nodes see a single `CancelToken`. Whether the caller cancelled or a fuse
    tripped, every existing cancellation checkpoint (the loop's per-iteration
    check, the pre-loop check, the leaf tools) reacts the same way; the
    breaker is not a second mechanism.

    Unsevarable: `dispatch` re-wraps any bare token a coordinator swapped in
    via replace(), so narrowing one branch's caller cancellation is legal
    but opting a subtree out of fuse trips is not.
    """

    def __init__(self, caller: CancelToken, gatekeeper: "Gatekeeper") -> None:
        self._caller = caller
        self._gatekeeper = gatekeeper

    @property
    def is_cancelled(self) -> bool:
        return self._caller.is_cancelled or self._gatekeeper.tripped is not None

    @property
    def caller_cancelled(self) -> bool:
        """The caller's own token, seen alone.

        Distinguishes a user stop from a fuse trip when both share the
        combined signal: the root rewrite must never relabel a cancellation
        the caller asked for as `run_halted`.
        """
        return self._caller.is_cancelled


class _ProviderTicket:
    """What the door hands its caller: the client plus a usage report slot.

    The caller makes the provider call on `client` and reports the turn's
    token counts through `record_usage`; the door settles cost from them on
    exit. A budgeted paid call without usage is refused because its cost
    cannot be enforced; an unbudgeted one settles at $0 with a warning.
    """

    def __init__(self, client: "AsyncOpenAI") -> None:
        self.client = client
        self.in_tokens: int | None = None
        self.out_tokens: int | None = None

    def record_usage(self, in_tokens: int, out_tokens: int) -> None:
        self.in_tokens = in_tokens
        self.out_tokens = out_tokens


class Gatekeeper:
    """Per-run shared state: fuses, room, stop signal, provider-call log.

    Constructed only by `run_one`; every governed LLM call passes through
    `provider_call`, and `dispatch` checks the time fuse as the second seam.
    """

    def __init__(
        self,
        *,
        catalog: dict[str, Model],
        default_model: str,
        max_llm_calls: int | None,
        time_limit_seconds: float | None,
        spend_limit_usd: float | None,
        concurrency: int,
        tool_ceiling: frozenset[str] | None = None,
        on_call: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        # The run's model catalog (see build_catalog): one registry of
        # entries per run, defined at the front door. Model *choice* stays
        # distributed (each node carries which entry it uses, a value on the
        # context); only the definitions live here.
        self.catalog = catalog
        self.default_model = default_model
        # One client per distinct endpoint, built lazily on first use.
        self._clients: dict[tuple[str, str | None], AsyncOpenAI] = {}
        # The tree-wide LLM-exposure ceiling: what a model may be *offered*,
        # never varying by branch. It lives here as a stateless bound so
        # custom code can rebuild a branch grant via replace() but never the
        # ceiling reference: even a grant widened to unrestricted stays
        # clamped. Name-based, not effect-based: it governs what a tool is
        # called, not what it does (the trust boundary's honest statement).
        self.tool_ceiling = tool_ceiling
        self.max_llm_calls = max_llm_calls
        self.spend_limit_usd = spend_limit_usd
        self._time_limit_seconds = time_limit_seconds
        self._deadline = (
            None if time_limit_seconds is None else time.monotonic() + time_limit_seconds
        )
        # The room: N chairs shared by the whole tree, held around the
        # provider call only, so nested fan-out cannot multiply past N.
        # Created here rather than lazily: run_one constructs the Gatekeeper
        # inside a running event loop (invoke_sync wraps run_one in
        # asyncio.run), the object lives exactly one run, and it is not
        # user-constructible, so cross-loop reuse is impossible by
        # construction.
        self._room = asyncio.Semaphore(concurrency)
        self.calls_admitted = 0
        # Settled spend: completed calls only. A call's cost is unknown until
        # it finishes, which is why the spend fuse is a fuse and not a hard
        # ceiling: overshoot is bounded in calls (about one concurrency
        # window), not in dollars, and every dollar is reported.
        self.observed_spend_usd = 0.0
        self.tripped: ErrorState | None = None
        # Which fuse tripped ("llm_calls" | "time" | "spend"), for the root
        # rewrite's causal test: a root budget_exceeded is the spend fuse's
        # own number read through the grant, but never the time fuse's story.
        self.tripped_fuse: str | None = None
        self._spend_at_trip = 0.0
        self._settled_after_trip = 0
        # One row per provider call, always on: the fuses read the same
        # counters, so the trust promise never depends on a config flag.
        self.calls: list[dict[str, Any]] = []
        # The caller's live view of the log (run_one's on_llm_call kwarg),
        # invoked once per row. Observational only, so a raising callback is
        # swallowed loudly rather than allowed to tear the settle path.
        self._on_call = on_call

    def _append_row(self, row: dict[str, Any]) -> None:
        self.calls.append(row)
        if self._on_call is None:
            return
        try:
            # The callback is observational: hand it a copy so mutation
            # cannot rewrite the Gatekeeper's audit row before persistence.
            self._on_call(row.copy())
        except Exception as exc:
            logger.warning("on_llm_call callback raised %s (ignored): %s", type(exc).__name__, exc)

    # --- the model catalog ---------------------------------------------------

    def resolve_model(self, name: str | None) -> Model:
        """The catalog entry for `name`; None means the run default.

        Unknown names fail fast and loud (`UnknownModelError`, converted to
        a validation failure at the Commission boundary). The pre-catalog
        silent fallback to a bare OpenRouter model retired with the catalog.
        """
        effective = name if name is not None else self.default_model
        entry = self.catalog.get(effective)
        if entry is None:
            registered = ", ".join(sorted(self.catalog))
            raise UnknownModelError(
                f"model {effective!r} is not in this run's catalog "
                f"(registered: {registered}). Register it in "
                f"run_one(models=[...])."
            )
        return entry

    def client_for(self, entry: Model) -> "AsyncOpenAI":
        """The provider client for a catalog entry, built lazily and shared.

        One client per distinct endpoint (base_url, api_key_env), so several
        models on one provider share a connection pool. Because the catalog
        vends the clients, the framework owns provider access by
        construction, which is what makes this seam structural rather than
        advisory. A keyed endpoint whose key is absent fails here, before
        any network call or spend, with the env var named; a keyless
        endpoint (api_key_env=None, e.g. local Ollama) never blocks. The
        testing seam rides the same door: a scripted entry
        (testing.scripted_model) carries its fake client and is returned
        as-is, uncached.
        """
        scripted = getattr(entry, "scripted_client", None)
        if scripted is not None:
            return cast("AsyncOpenAI", scripted)
        endpoint = (entry.base_url, entry.api_key_env)
        client = self._clients.get(endpoint)
        if client is None:
            key_env = entry.api_key_env
            # A keyless endpoint ignores the key, but the OpenAI client
            # refuses a falsy one, so send a non-empty placeholder.
            api_key = "unused" if key_env is None else os.environ.get(key_env, "")
            if not api_key:
                raise RuntimeError(
                    f"No API key: environment variable {key_env!r} is not "
                    f"set (model {entry.id!r} at {entry.base_url}). Set it, "
                    f"or register a keyless Model (api_key_env=None)."
                )
            from openai import AsyncOpenAI

            client = AsyncOpenAI(base_url=entry.base_url, api_key=api_key)
            self._clients[endpoint] = client
        return client

    async def aclose(self) -> None:
        """Close every provider client this run vended.

        The catalog builds clients per run, so the run tears them down:
        `run_one` awaits this in its finally, making the lifetime explicit
        and symmetric. Scripted entries carry their own fakes and are never
        cached here, so nothing test-owned is touched. A failing close is
        hygiene trouble, never the run's: swallowed to a warning.
        """
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            try:
                await client.close()
            except Exception as exc:
                logger.warning(
                    "provider client close failed (ignored): %s: %s", type(exc).__name__, exc
                )

    # --- fuses -------------------------------------------------------------

    def check_time(self) -> None:
        """The time fuse's dispatch seam: a halt fires between LLM calls too.

        Trips the breaker only; the node's own cancellation checkpoints
        handle the fast exit.
        """
        if self.tripped is None and self._time_due():
            self._trip(self._time_detail(), fuse="time")

    def _time_due(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def final_error(
        self,
        root_run_id: str | None,
        *,
        log_persisted: bool,
        log_persistence_failed: bool = False,
    ) -> ErrorState:
        """The root's `run_halted` failure, rebuilt with the final numbers.

        Written for an AI-agent reader with no other context: the fuse, the
        numbers, what the in-flight calls added, and where the full log
        lives, but only when it lives somewhere (`log_persisted` confirms the
        backend took the rows; a live `on_llm_call` already delivered them;
        with neither, the honest pointer is how to wire one next time).
        Composed at run end rather than trip time so calls that were
        in flight at the trip report their settled cost, which is what makes
        "every dollar reported" checkable.
        """
        assert self.tripped is not None
        detail = self.tripped.detail
        if self._settled_after_trip:
            extra = self.observed_spend_usd - self._spend_at_trip
            detail += (
                f"; {self._settled_after_trip} in-flight call(s) completed for ${extra:.4f} more"
            )
        detail += f"; true total spend ${self.observed_spend_usd:.4f}"
        if log_persisted:
            detail += f"; full call log under run {root_run_id}."
        elif self._on_call is not None:
            detail += "; the full call log was delivered live via on_llm_call."
        elif log_persistence_failed:
            detail += "; call-log persistence failed; inspect the framework warning for details."
        else:
            detail += (
                ". Wire backend= (SqliteBackend) or on_llm_call= to capture the full call log."
            )
        return ErrorState(kind="run_halted", detail=detail, retryable=False)

    def _trip(self, detail: str, *, fuse: str) -> None:
        """Flip the stop signal. First trip wins; later trips are no-ops."""
        if self.tripped is not None:
            return
        self.tripped_fuse = fuse
        self._spend_at_trip = self.observed_spend_usd
        self._settled_after_trip = 0
        self.tripped = ErrorState(kind="run_halted", detail=detail, retryable=False)

    def _time_detail(self) -> str:
        limit = self._time_limit_seconds if self._time_limit_seconds is not None else 0.0
        return f"time fuse tripped: the run passed its {limit:g}-second limit"

    def _refuse_if_due(
        self,
        *,
        commission_name: str,
        model: str,
        run_id: str | None,
        grant_usd: float | None,
    ) -> None:
        """Refuse a new provider call when a fuse has tripped or is due.

        Pre-admission only: the count check reads "this call would be call
        N+1", which must never run against a call that already happened
        (a run that makes exactly N calls and concludes has not tripped).
        """
        if self.tripped is None:
            if self.max_llm_calls is not None and self.calls_admitted >= self.max_llm_calls:
                self._trip(
                    f"llm-call fuse tripped: call {self.calls_admitted + 1} "
                    f"refused at the {self.max_llm_calls}-call limit",
                    fuse="llm_calls",
                )
            elif self._time_due():
                self._trip(self._time_detail(), fuse="time")
            elif (
                self.spend_limit_usd is not None and self.observed_spend_usd > self.spend_limit_usd
            ):
                self._trip(self._spend_detail(), fuse="spend")
        if self.tripped is not None:
            now = _utcnow_iso()
            self._append_row(
                self._row(
                    run_id=run_id,
                    commission_name=commission_name,
                    model=model,
                    started_at=now,
                    ended_at=now,
                    in_tokens=None,
                    out_tokens=None,
                    cost_usd=0.0,
                    grant_usd=grant_usd,
                    status="refused",
                    calls_before=self.calls_admitted,
                    spend_before=self.observed_spend_usd,
                )
            )
            raise RunHaltedError(self.tripped.detail)

    def _spend_detail(self) -> str:
        limit = self.spend_limit_usd if self.spend_limit_usd is not None else 0.0
        return (
            f"spend fuse tripped: observed spend ${self.observed_spend_usd:.4f} "
            f"exceeded the ${limit:.4f} limit"
        )

    # --- the door ------------------------------------------------------------

    @asynccontextmanager
    async def provider_call(
        self,
        *,
        entry: Model,
        commission_name: str,
        grant_usd: float | None,
    ) -> AsyncGenerator[_ProviderTicket]:
        """The provider door: every governed LLM call passes through here.

        Takes a catalog entry (from `resolve_model`), vends the client for
        it, and prices the settle from its per-token rates. Refuses when a
        fuse has tripped or is due (raising `RunHaltedError` after logging a
        refused row), holds the call until a room chair frees, re-checks the
        fuses after the wait, and settles on exit: cost lands in the
        observed total, the row lands in the log (completed or failed), and
        the spend fuse is checked. The chair is held around the provider
        call only: it frees at the call's own exit, before the settle
        bookkeeping (and its on_llm_call callback) and before the caller
        dispatches children. Count leaf work, not coordinators, so the room
        is deadlock-free even at a limit of 1.
        """
        model = entry.id
        run_id = _current_call_run_id()
        self._refuse_if_due(
            commission_name=commission_name, model=model, run_id=run_id, grant_usd=grant_usd
        )
        # Vend before taking a chair: a missing-key raise should not occupy
        # the room, and it is not a refusal, so no log row.
        client = self.client_for(entry)
        # Admission snapshot: set under the room lock, read at settle. A
        # non-None ticket is what marks the call as admitted (a refusal
        # raises before one exists, and settles nothing).
        ticket: _ProviderTicket | None = None
        calls_before = 0
        spend_before = 0.0
        started_at = ""
        failed = False
        try:
            async with self._room:
                # Re-check after the (possibly long) wait: a fuse may have
                # tripped while this call sat in the queue.
                self._refuse_if_due(
                    commission_name=commission_name,
                    model=model,
                    run_id=run_id,
                    grant_usd=grant_usd,
                )
                # Admission. No await between the check above and this
                # increment; single-threaded asyncio makes check-then-admit
                # atomic.
                calls_before = self.calls_admitted
                spend_before = self.observed_spend_usd
                self.calls_admitted += 1
                started_at = _utcnow_iso()
                ticket = _ProviderTicket(client)
                yield ticket
                paid_model = (
                    entry.input_usd_per_million != 0.0 or entry.output_usd_per_million != 0.0
                )
                missing_usage = ticket.in_tokens is None or ticket.out_tokens is None
                if grant_usd is not None and paid_model and missing_usage:
                    raise MissingUsageError(
                        f"model {entry.id!r} returned no token usage for a "
                        f"budgeted paid call, so its cost cannot be tracked."
                    )
                if paid_model and missing_usage:
                    logger.warning(
                        "model %s returned no token usage; this unbudgeted "
                        "call's cost is unreported",
                        entry.id,
                    )
        except BaseException:
            failed = True
            raise
        finally:
            # Settle even after a trip: an in-flight call that finishes
            # past the halt still counts, or "every dollar reported" would
            # be false exactly when it matters. Settled outside the room so
            # the callback never occupies a chair; releasing the semaphore
            # only *schedules* waiters, so this synchronous block (spend
            # total, row, the spend-fuse check) still completes before any
            # queued call re-checks the fuses.
            if ticket is not None:
                was_tripped = self.tripped is not None
                cost = 0.0
                if ticket.in_tokens is not None and ticket.out_tokens is not None:
                    cost = entry.cost_usd(ticket.in_tokens, ticket.out_tokens)
                self.observed_spend_usd += cost
                if was_tripped:
                    self._settled_after_trip += 1
                self._append_row(
                    self._row(
                        run_id=run_id,
                        commission_name=commission_name,
                        model=model,
                        started_at=started_at,
                        ended_at=_utcnow_iso(),
                        in_tokens=ticket.in_tokens,
                        out_tokens=ticket.out_tokens,
                        cost_usd=cost,
                        grant_usd=grant_usd,
                        status="failed" if failed else "completed",
                        calls_before=calls_before,
                        spend_before=spend_before,
                    )
                )
                # The spend fuse trips at settle, not only at the next
                # admission, so parallel branches start winding down the
                # moment the limit is passed instead of at their next call.
                # Strictly past, not at: spending exactly the budget is
                # within budget, the same reading as the node-level checks,
                # and what lets a $0 budget run free models at all.
                if (
                    self.tripped is None
                    and self.spend_limit_usd is not None
                    and self.observed_spend_usd > self.spend_limit_usd
                ):
                    self._trip(self._spend_detail(), fuse="spend")

    def _row(
        self,
        *,
        run_id: str | None,
        commission_name: str,
        model: str,
        started_at: str,
        ended_at: str,
        in_tokens: int | None,
        out_tokens: int | None,
        cost_usd: float,
        grant_usd: float | None,
        status: str,
        calls_before: int,
        spend_before: float,
    ) -> dict[str, Any]:
        """One log row. Keys are shared with the persisted `calls` table."""
        return {
            "run_id": run_id,
            "commission_name": commission_name,
            "model": model,
            "started_at": started_at,
            "ended_at": ended_at,
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "cost_usd": cost_usd,
            "grant_usd": grant_usd,
            "run_calls_before": calls_before,
            "run_spend_before_usd": spend_before,
            "status": status,
        }


def _current_call_run_id() -> str | None:
    """The run_id of the call currently executing, for log rows.

    Lazy import: dispatch imports this module for the run-scoping refusals,
    so importing it back at module load would be a cycle. After first call
    it's a cached sys.modules lookup.
    """
    from vibrantine.dispatch import _current_run_id  # pyright: ignore[reportPrivateUsage]

    return _current_run_id.get()


def _utcnow_iso() -> str:
    # timespec pinned so every timestamp in a SQLite file (this log's rows,
    # persistence's records) has one TEXT format and string order is time
    # order; bare isoformat() drops the fraction when microsecond == 0.
    return datetime.now(UTC).isoformat(timespec="microseconds")
