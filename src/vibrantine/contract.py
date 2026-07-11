"""Core contract types for Vibrantine Commissions.

A "call" is one execution of `commission._run(input, ctx)`. Every type
here describes some facet of a call: its typed input/output envelope
(`CommissionResult`), its runtime conditions (`CallContext`), the origin
and trust level of the data it produces (`Provenance`, `Claim`), the cost
it incurred (`CostMetrics`), and its failure modes (`ErrorState`).
"""

from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    Literal,
    Protocol,
    cast,
    get_args,
    get_origin,
    runtime_checkable,
)

from pydantic import BaseModel, Field

from vibrantine.models import Model, UnknownModelError

if TYPE_CHECKING:
    from vibrantine._gatekeeper import Gatekeeper


class _Unset(Enum):
    """Sentinel: caller did not pass a value; fall back to the class default.

    Needed because max_output_tokens=None is a meaningful value (no
    enforcement), so None can't double as the 'unset' marker on __init__.
    """

    UNSET = "UNSET"


_UNSET: Final = _Unset.UNSET

# Shared enum vocabulary --------------------------------------------------

type ConfidenceLevel = Literal["verified", "grounded", "speculative"]
type CommissionStatus = Literal["success", "failure", "partial"]
type ErrorKind = Literal[
    "validation",
    "internal",
    "rate_limit",
    "timeout",
    "budget_exceeded",
    "cancelled",
    "output_too_large",
    # Whole-run teardown: a run fuse tripped and run_one rewrote the root.
    # Node-level allocation exhaustion stays budget_exceeded; the line
    # between the two kinds is scope, not resource type.
    "run_halted",
]
type PersistenceMode = Literal["off", "on_failure", "dev", "always"]
type OverflowPolicy = Literal[
    "reject",
    "truncate_with_reference",
    "partial",
    "flag",
]


# Payload helpers ---------------------------------------------------------


class Provenance(BaseModel):
    """Where a piece of data came from and how trustworthy it is."""

    source: str = Field(description="URI or identifier where this data originated.")
    fetched_at: datetime = Field(description="When this data entered the system.")
    confidence: ConfidenceLevel = Field(
        description="How grounded this data is in its source.",
    )


class CostMetrics(BaseModel):
    """Dollars consumed by one call. Children's costs roll up structurally.

    Token counts do NOT roll up: they cover this call's own LLM turns only,
    because a token sum across a tree that mixes models looks meaningful and
    isn't. USD is the rollup currency. None means no LLM turn ran here (a
    deterministic tool, a Python coordinator, or a failure before the first
    call); 0 means an LLM run counted nothing.
    """

    estimated_usd: float = Field(description="Estimated cost of this call, in USD.")
    in_tokens: int | None = Field(
        default=None,
        description="Input tokens across this call's own LLM turns. None if no LLM turn ran.",
    )
    out_tokens: int | None = Field(
        default=None,
        description="Output tokens across this call's own LLM turns. None if no LLM turn ran.",
    )


class ErrorState(BaseModel):
    """Structured failure value. No exception crosses the call boundary."""

    kind: ErrorKind = Field(description="Failure category for caller dispatch.")
    detail: str = Field(description="Human-readable, actionable explanation.")
    retryable: bool = Field(
        description="True if retrying with the same input might succeed.",
    )


class Claim[T](BaseModel):
    """A single load-bearing assertion plus the source provenances that support it."""

    value: T = Field(description="The asserted value.")
    sources: list[Provenance] = Field(
        description="Provenance records that support this claim.",
    )
    confidence: ConfidenceLevel = Field(
        description="How well synthesis grounded this claim in its sources.",
    )


class CommissionResult[OutputT](BaseModel):
    """The single value returned by one call."""

    status: CommissionStatus = Field(description="Outcome category.")
    output: OutputT | None = Field(
        default=None,
        description="Typed result; populated on success and partial.",
    )
    error: ErrorState | None = Field(
        default=None,
        description="Failure detail; populated on failure and partial.",
    )
    provenance: Provenance = Field(description="Provenance of this call's run.")
    cost: CostMetrics = Field(description="Cost incurred by this call.")
    run_id: str | None = Field(
        default=None,
        description=(
            "UUID4 stamped by dispatch on every wrapped call. None only for "
            "results built outside the framework."
        ),
    )
    parent_run_id: str | None = Field(
        default=None,
        description=(
            "run_id of the immediate caller, threaded via dispatch. None for root invocations."
        ),
    )


# Capabilities, cancellation, progress ------------------------------------


class CapabilitySet(BaseModel):
    """Tool names a Commission's LLM is permitted to call.

    None means unrestricted (the root default). A set is an explicit
    allow-list, including the empty set, which permits nothing. Enforced by
    `run_llm_loop`, which builds the LLM's tool menu from the intersection of
    the Commission's toolbox and this allow-list.
    """

    tools: frozenset[str] | None = Field(
        default=None,
        description=(
            "Allow-list of tool names. None = unrestricted; a set permits "
            "exactly those names (empty set = permit nothing)."
        ),
    )


@runtime_checkable
class CancelToken(Protocol):
    """Cooperative cancellation signal. Commissions check at natural breakpoints."""

    @property
    def is_cancelled(self) -> bool: ...


class _NeverCancelled:
    @property
    def is_cancelled(self) -> bool:
        return False


NEVER_CANCELLED: Final[CancelToken] = _NeverCancelled()


class ProgressEvent(BaseModel):
    """Observability event emitted by a Commission. Not a control surface."""

    commission_name: str = Field(description="Name of the Commission emitting this event.")
    phase: str = Field(description="Short label for the phase being entered.")
    detail: str | None = Field(default=None, description="Optional extra context.")


# Persistence -------------------------------------------------------------


class PersistedRecord(BaseModel):
    """One persisted Commission run, as written/read by a PersistenceBackend."""

    run_id: str = Field(description="UUID4 identifier for this run.")
    parent_run_id: str | None = Field(
        default=None,
        description="run_id of the immediate caller; None for root invocations.",
    )
    commission_name: str = Field(description="The Commission's class-level name.")
    mode: PersistenceMode = Field(
        description="Mode under which this run was persisted; informs retention.",
    )
    created_at: datetime = Field(description="When this record was stored.")
    input: dict[str, Any] = Field(
        description="The typed InputT, serialized via model_dump.",
    )
    result: dict[str, Any] = Field(
        description="The full CommissionResult, serialized via model_dump.",
    )
    ctx_snapshot: dict[str, Any] = Field(
        description=("Frozen subset of CallContext: budget_usd, capabilities, parent_run_id."),
    )
    llm_trace: list[dict[str, Any]] | None = Field(
        default=None,
        description="LLM loop: messages + tool_calls + tool_results sequence. Raw JSON for v1.",
    )


@runtime_checkable
class PersistenceBackend(Protocol):
    """Storage interface for persisted Commission runs.

    The library ships a default filesystem backend; apps can substitute
    any implementation that satisfies this protocol (KV store, SQLite,
    in-memory for tests, etc.).

    `delete_older_than` destroys records, so its `cutoff` must be
    timezone-aware; an implementation rejects a naive datetime with
    `ValueError` rather than guess which timezone it meant. All datetimes
    the framework writes (`PersistedRecord.created_at`) are UTC-aware.
    """

    async def store(self, record: PersistedRecord) -> None: ...
    async def load(self, run_id: str) -> PersistedRecord | None: ...
    async def list_references(self, *, parent_run_id: str | None = None) -> list[str]: ...
    async def delete(self, run_id: str) -> None: ...
    async def delete_older_than(self, cutoff: datetime) -> int: ...


# Call context ------------------------------------------------------------


@dataclass(frozen=True)
class CallContext:
    """Runtime conditions for one call.

    Carried alongside the typed input. Extend this for new orchestration
    concerns rather than the `_run` signature.
    """

    budget_usd: float | None = None
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    cancel: CancelToken = NEVER_CANCELLED
    on_progress: Callable[[ProgressEvent], None] | None = None
    # Populated by `dispatch` from the outer call's run_id; None at the root.
    # Commission bodies can read this for provenance/logging; they should not
    # set it manually; dispatch threads it via ContextVar across nested calls.
    parent_run_id: str | None = None
    # PersistenceBackend the dispatch helper writes through. Wired by the
    # caller (typically run_one); inherited by children automatically.
    backend: PersistenceBackend | None = None
    # Caller's recording default for every node in the call tree, including
    # children spawned mid-run. Applies only to nodes whose own
    # `persistence_mode` is None (no opinion); a node's explicit mode,
    # including "off", wins. None here too means recording stays off.
    record: PersistenceMode | None = None
    # The run's internal control object (fuses, room, stop signal, call
    # log), created by run_one and carried to every node by reference.
    # Internal plumbing, not caller surface: never set it by hand. dispatch
    # refuses a context whose run object is not the run in progress, so a
    # replace() can rebuild grants but never smuggle in a fresh room, fresh
    # fuses, or a wider ceiling.
    _gatekeeper: "Gatekeeper | None" = None


# Message content parts ---------------------------------------------------


class TextPart(BaseModel):
    """A text span in a Commission's opening user message."""

    type: Literal["text"] = "text"
    text: str = Field(description="The text content.")


class ImagePart(BaseModel):
    """An image in a Commission's opening user message.

    Provisional: defined now so the `ContentPart` union and the
    `build_user_message` return type are multimodal-ready, but its exact
    fields (URL vs base64 data, mime type) are finalized by the first
    image-bearing consumer (shape-via-consumer). Today it carries only a
    URL/data string.

    Later modalities (video, documents) join the union the same way: a new
    typed part with its own `type` tag, its fields fixed by its first
    consumer. Widening the union is additive and does not break authors who
    already return existing parts.
    """

    type: Literal["image"] = "image"
    image_url: str = Field(
        description="Image location: an http(s) URL or a data: URI.",
    )


class AudioPart(BaseModel):
    """An audio clip in a Commission's opening user message.

    Provisional, same asterisk as `ImagePart`: the fields conform to the
    OpenAI `input_audio` content part (base64 data plus an encoding tag),
    and stay open until enough consumers have confirmed the shape.
    """

    type: Literal["audio"] = "audio"
    data: str = Field(description="Base64-encoded audio bytes.")
    format: Literal["wav", "mp3"] = Field(
        description="Audio encoding; the formats the provider shape accepts.",
    )


type ContentPart = TextPart | ImagePart | AudioPart


def estimate_tokens(text: str) -> int:
    """Rough char-per-token heuristic. `target_input_fraction` absorbs the slop."""
    return len(text) // 4


def _message_text(message: "str | list[ContentPart]") -> str:
    """Text content of an opening message, for the pre-flight size gate.

    Non-text parts contribute unknown token counts; the gate measures the
    text only until a multimodal consumer refines the estimate.
    """
    if isinstance(message, str):
        return message
    return "".join(p.text for p in message if isinstance(p, TextPart))


# Commission contract -----------------------------------------------------

# The default LLM-loop iteration cap. Public so a Commission that exposes
# `max_iterations` in its own constructor can restate the framework default
# instead of forking a literal 10 that silently drifts if this changes.
DEFAULT_MAX_ITERATIONS: Final = 10


class Commission[InputT, OutputT](ABC):
    """The contract every Commission implements.

    One typed input, one typed result, runtime conditions in CallContext.
    A basic Commission sets the identity ClassVars, a `system_prompt`, a
    `toolbox`, and a `build_user_message` hook, then rides the default
    `_run` (the full LLM loop below). A custom Commission (one whose
    control flow is not the standard loop) overrides `_run` instead. The
    leading underscore marks ownership, not secrecy: authors implement it,
    the framework calls it (through `dispatch`), and nothing else touches
    it. Invoke a Commission through `run_one` / `invoke_sync` / `dispatch`.
    """

    # Identity
    name: ClassVar[str]
    description: ClassVar[str]
    input_type: ClassVar[type[BaseModel]]
    output_type: ClassVar[type[BaseModel]]
    # Commission-layer system prompt. None = this Commission doesn't need
    # one (typical for Tools); a string = the prompt the framework feeds
    # to the Commission's own LLM. Application and envelope layers are
    # planned but not yet shipped.
    system_prompt: ClassVar[str | None] = None

    # Toolbox: author-declared dependencies this Commission can dispatch (the
    # sub-Commissions/tools it owns). Class-attribute default: () for workers;
    # a basic Commission sets a class-level tuple, whose tools are shared
    # across instances and so must be safe to share. Stateful tools are built
    # per-instance in an author __init__ and passed up via the `toolbox=`
    # kwarg, which overrides this for DI/tests. Not a ClassVar; instance
    # override is supported.
    toolbox: "tuple[Commission[Any, Any], ...]" = ()

    # Policy: class default; override per-instance via __init__ kwargs.
    # Not ClassVar; instance assignment is a supported override path.
    # `persistence_mode` None means "no opinion": the node follows the
    # caller's `CallContext.record` default (off when that is unset too).
    # An explicit mode, including "off", is the node's own word and beats
    # the caller: silence follows the room, a spoken choice is kept.
    persistence_mode: PersistenceMode | None = None
    max_output_tokens: int | None = None
    overflow_policy: OverflowPolicy = "partial"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Enforce the standard authoring format at class-definition time.

        Three checks, so a malformed Commission fails when it's defined rather
        than at first run, part of making the contract safe to author
        against (including by non-devs and lesser-model agents):

        1. The four identity ClassVars are set (the check follows the MRO, so
           a subclass inheriting them from a parent passes).
        2. The generic parameters agree with the identity ClassVars. Both
           state the I/O contract: type checkers reason from
           `Commission[InputT, OutputT]`, the runtime reads the ClassVars.
           Without the check a mismatch defines cleanly and the two audiences
           see different contracts. Only concrete classes can disagree, so
           TypeVars (a generic intermediate, the factory's function-scoped
           type params), `Any`, and unparameterized bases are skipped.
        3. The override rule: a Commission overrides `build_user_message` (a
           basic Commission) or `_run` (a custom one). Overriding neither
           means it could never run. An abstract intermediate defers a slot
           with `@abstractmethod`, which counts as overriding it (and ABC
           still blocks instantiation); no opt-out flag needed.
        """
        super().__init_subclass__(**kwargs)
        missing = [
            attr
            for attr in ("name", "description", "input_type", "output_type")
            if getattr(cls, attr, None) is None
        ]
        if missing:
            raise TypeError(
                f"{cls.__name__} must set required Commission class "
                f"attribute(s): {', '.join(missing)}. A Commission declares "
                f"name, description, input_type, and output_type."
            )
        for base in getattr(cls, "__orig_bases__", ()):
            if get_origin(base) is not Commission:
                continue
            args = get_args(base)
            if len(args) != 2:
                continue
            for arg, attr in zip(args, ("input_type", "output_type"), strict=True):
                declared = getattr(cls, attr)
                # `Any` is a class on 3.12, so exclude it before the
                # concrete-class test or Any-parameterized bases would
                # false-positive here.
                if arg is Any or not isinstance(arg, type):
                    continue
                if arg is not declared:
                    declared_name = getattr(declared, "__name__", repr(declared))
                    raise TypeError(
                        f"{cls.__name__} declares {arg.__name__} as a generic "
                        f"parameter but sets {attr} = {declared_name}. The "
                        f"generic parameters and the identity ClassVars state "
                        f"the same contract and must name the same types."
                    )
        overrides = {
            attr
            for klass in cls.__mro__
            if klass is not Commission
            for attr in ("build_user_message", "_run")
            if attr in klass.__dict__
        }
        if not overrides:
            raise TypeError(
                f"{cls.__name__} overrides neither build_user_message nor "
                f"_run, so it could never run. A basic Commission overrides "
                f"build_user_message; a custom Commission overrides _run "
                f"(the hook formerly named invoke)."
            )

    def __init__(
        self,
        *,
        model: str | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        toolbox: "tuple[Commission[Any, Any], ...] | _Unset" = _UNSET,
        max_input_tokens: int | None | _Unset = _UNSET,
        target_input_fraction: float = 0.75,
        persistence_mode: PersistenceMode | None | _Unset = _UNSET,
        max_output_tokens: int | None | _Unset = _UNSET,
        overflow_policy: OverflowPolicy | _Unset = _UNSET,
    ) -> None:
        # `toolbox=` overrides the class-attribute default (DI/tests); when
        # unset, the class-level declaration stands.
        if not isinstance(toolbox, _Unset):
            self.toolbox = toolbox
        # The model *choice*: a pure name, looked up in the run's catalog
        # when the loop runs (None = the run default). Model objects (identity
        # + endpoint + facts) live in the catalog, defined once at run_one;
        # an unknown name fails fast there. The choice stays distributed:
        # each Commission carries which entry it uses, never the entry.
        # Non-LLM Commissions (tools, coordinators) inherit this inertly;
        # they override `_run` and never consult it.
        self._model = model
        self._max_iterations = max_iterations
        # Size gate setting: unset resolves at run time from the catalog
        # entry's context window; an explicit None disables the gate entirely
        # (the standard tool shape: no LLM, no gate); an int pins it. None
        # and unset must stay distinct, hence the sentinel.
        self._max_input_tokens_setting: int | None | _Unset = max_input_tokens
        self.target_input_fraction = target_input_fraction
        if not isinstance(persistence_mode, _Unset):
            self.persistence_mode = persistence_mode
        if not isinstance(max_output_tokens, _Unset):
            self.max_output_tokens = max_output_tokens
        if not isinstance(overflow_policy, _Unset):
            self.overflow_policy = overflow_policy

    @property
    def max_input_tokens(self) -> int | None:
        """The explicit size-gate cap; None when disabled or run-resolved.

        A cap left unset at construction resolves at run time from the run
        catalog entry's context window (which this property cannot see), so
        None here means either "explicitly no gate" or "resolved per run".

        Assignable: setting it pins an explicit cap after construction (an
        external pre-chunker sizing work against `fits()` owns its number
        this way); assigning None disables the gate, exactly like the
        constructor argument.
        """
        if isinstance(self._max_input_tokens_setting, _Unset):
            return None
        return self._max_input_tokens_setting

    @max_input_tokens.setter
    def max_input_tokens(self, value: int | None) -> None:
        self._max_input_tokens_setting = value

    def build_user_message(
        self,
        input: InputT,
        ctx: CallContext,
    ) -> "str | list[ContentPart]":
        """Turn the typed input into the LLM loop's opening user message.

        A basic Commission overrides this. A bare `str` is sugar for a single
        `TextPart`; return `list[ContentPart]` for multimodal input. It
        receives `ctx` so future per-call information (the planned envelope
        layer, which lands inside `CallContext`) reaches it without a
        signature change. A custom Commission overrides `_run` instead and
        never triggers this.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must define build_user_message(), or override _run()."
        )

    def truncate_output(self, output: OutputT, max_tokens: int) -> OutputT | None:
        """Chop an oversized output for the `truncate_with_reference` policy.

        Dispatch calls this when the output exceeds `max_output_tokens` under
        the `truncate_with_reference` overflow policy. Return a smaller,
        still-valid OutputT that fits within `max_tokens` (measured by the
        contract's `estimate_tokens` heuristic over the JSON serialization).
        Dispatch then persists the full result under this run's `run_id`,
        returns the chopped output as `partial`, and names the run_id in the
        error detail so the caller can load the full version.

        The default declines by returning None: shrinking a typed output
        without making it invalid or self-contradicting (fewer list items? a
        shortened text field?) is knowledge only the author has. On None,
        the policy degrades to `partial` with the full output preserved.
        """
        return None

    async def _run(
        self,
        input: InputT,
        ctx: CallContext,
    ) -> CommissionResult[OutputT]:
        """The default pipeline: the full LLM loop over `toolbox`.

        A custom Commission overrides this entirely. The contract invariants
        upheld here are documented in docs/design.md (§ One base class, one
        escape hatch).
        """
        # Lazy import: `run_llm_loop` lives in llm_tools, which imports this
        # module; importing it at call time breaks the contract<->llm_tools
        # cycle. After first call it's a cached sys.modules lookup.
        from vibrantine.llm_tools import run_llm_loop

        zero_cost = CostMetrics(estimated_usd=0.0)

        # Resolve the model choice against the run's catalog before anything
        # else: an unknown name is an immediate, loud authoring error, and
        # everything below (provenance, pricing, the size gate) hangs off
        # the entry. dispatch refuses a context without a run, so the
        # Gatekeeper is always present here.
        gatekeeper = ctx._gatekeeper  # pyright: ignore[reportPrivateUsage]
        assert gatekeeper is not None, "the default loop runs inside a run"
        try:
            entry = gatekeeper.resolve_model(self._model)
        except UnknownModelError as exc:
            return self._fail(
                "validation",
                str(exc),
                retryable=False,
                provenance=Provenance(
                    source=f"{self.name}:{self._model}",
                    fetched_at=datetime.now(UTC),
                    confidence="grounded",
                ),
                cost=zero_cost,
            )

        provenance = Provenance(
            source=f"{self.name}:{entry.id}",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        )

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled before the loop started.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        budget_failure = self._budget_unenforceable_failure(ctx, provenance, entry)
        if budget_failure is not None:
            return budget_failure

        system_prompt = self.system_prompt or ""
        user_message = self.build_user_message(input, ctx)

        estimated = estimate_tokens(system_prompt + _message_text(user_message))
        if not self._fits_cap(estimated, self._effective_input_cap(entry)):
            return self._fail(
                "validation",
                f"Estimated input of {estimated} tokens exceeds the size gate "
                f"for model {entry.id}.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        self._emit(ctx, "loop_start")
        outcome = await run_llm_loop(
            model=self._model,
            commission_name=self.name,
            system_prompt=system_prompt,
            user_message=user_message,
            toolbox=self.toolbox,
            output_type=self.output_type,
            ctx=ctx,
            max_iterations=self._max_iterations,
        )

        own_cost = self._cost(outcome.in_tokens, outcome.out_tokens, entry)
        # Roll dispatched children's cost into this call's cost, so the
        # "costs roll up structurally" invariant holds on the LLM-loop path
        # (not just for hand-summing Python coordinators). Token counts stay
        # own-turns only: USD is the rollup currency.
        cost = CostMetrics(
            estimated_usd=own_cost.estimated_usd + outcome.children_cost,
            in_tokens=outcome.in_tokens,
            out_tokens=outcome.out_tokens,
        )
        if outcome.error is not None:
            return self._fail(
                outcome.error.kind,
                outcome.error.detail,
                retryable=outcome.error.retryable,
                provenance=provenance,
                cost=cost,
            )
        assert outcome.output is not None
        return self._succeed(cast(OutputT, outcome.output), provenance=provenance, cost=cost)

    def _cost(self, in_tokens: int, out_tokens: int, entry: Model) -> CostMetrics:
        """Token counts priced against a run catalog entry, as CostMetrics.

        An unpriced entry prices at $0 (callers gate on `entry.is_priced`
        before trusting that for budgeting; see
        `_budget_unenforceable_failure`).
        """
        usd = entry.cost_usd(in_tokens, out_tokens)
        return CostMetrics(estimated_usd=usd, in_tokens=in_tokens, out_tokens=out_tokens)

    def _succeed(
        self,
        output: OutputT,
        *,
        provenance: Provenance,
        cost: CostMetrics,
    ) -> CommissionResult[OutputT]:
        """Build a success result: `_fail`'s counterpart for the common return.

        A custom `_run` returns success far more often than failure, and
        hand-building that envelope (a `CommissionResult` plus a cast) was
        the most boilerplate-heavy return on the custom path. Same protected
        tier as `_fail`: supported interior surface for authors, provisional
        until the authoring-surface freeze.
        """
        return CommissionResult(
            status="success",
            output=output,
            provenance=provenance,
            cost=cost,
        )

    def _fail(
        self,
        kind: ErrorKind,
        detail: str,
        *,
        retryable: bool,
        provenance: Provenance,
        cost: CostMetrics,
    ) -> CommissionResult[OutputT]:
        """Build a structured failure result (errors-as-values).

        Protected authoring tier, like `_succeed`: supported interior surface
        for a custom `_run`, provisional until the authoring-surface freeze.
        """
        return cast(
            CommissionResult[OutputT],
            CommissionResult(
                status="failure",
                error=ErrorState(kind=kind, detail=detail, retryable=retryable),
                provenance=provenance,
                cost=cost,
            ),
        )

    def _budget_unenforceable_failure(
        self,
        ctx: CallContext,
        provenance: Provenance,
        entry: Model,
    ) -> CommissionResult[OutputT] | None:
        """Refuse when a budget is set but the model can't be priced.

        `budget_usd` is the caller's spending bound (enforced per turn, so
        true spend can overshoot by up to about one turn's cost per tree
        level), and the framework can only enforce it if the run catalog
        entry carries pricing (`entry.is_priced`). An unpriced model prices
        at $0, which would make the budget silently unenforced, so rather
        than run with a budget it can't keep, the call fails fast here. A
        *free* model (priced at $0.0, e.g. local Ollama) is priced and
        passes. Returns None when enforcement is possible (a priced model,
        or no budget set), so a budget-checking `_run` calls this once up
        front and proceeds on None.
        """
        if ctx.budget_usd is None or entry.is_priced:
            return None
        return self._fail(
            "validation",
            f"budget_usd was set but model {entry.id!r} is unpriced, so its "
            f"cost can't be tracked and the budget can't be enforced. "
            f"Register the model with USD rates in run_one(models=...) "
            f"(openai_compatible(...) with prices, or ollama() for a free "
            f"local model), or invoke without a budget.",
            retryable=False,
            provenance=provenance,
            cost=CostMetrics(estimated_usd=0.0),
        )

    def _emit(self, ctx: CallContext, phase: str, detail: str | None = None) -> None:
        """Emit an observability event; no-op without a callback."""
        if ctx.on_progress is not None:
            ctx.on_progress(ProgressEvent(commission_name=self.name, phase=phase, detail=detail))

    def fits(self, estimated_tokens: int) -> bool:
        """Whether an input of the given token count would pass the size gate.

        Judged against the explicit cap only (constructor argument or a
        later `max_input_tokens` assignment). A cap left unset resolves at
        run time from the run catalog entry's context window, which this
        method cannot see: with no explicit cap this always answers True,
        and the run-time gate is the authoritative check. A caller sizing
        work outside a run (pre-chunking a corpus) should set an explicit
        cap it owns rather than lean on the default.
        """
        return self._fits_cap(estimated_tokens, self.max_input_tokens)

    def _effective_input_cap(self, entry: Model) -> int | None:
        """The size-gate cap for a run against `entry`.

        The explicit constructor setting wins (including None, no gate);
        unset takes the entry's context window.
        """
        if isinstance(self._max_input_tokens_setting, _Unset):
            return entry.context_window
        return self._max_input_tokens_setting

    def _fits_cap(self, estimated_tokens: int, cap: int | None) -> bool:
        if cap is None:
            return True
        return estimated_tokens <= cap * self.target_input_fraction
