"""Tests for the run Gatekeeper: fuses, room, stop signal, refusals, log.

The Gatekeeper is internal (no public name); these tests exercise it the
way a caller experiences it, through `run_commission` kwargs and the root result,
plus the conftest `open_test_run` harness where the in-memory log itself is
inspected. Scripted catalog entries keep every run offline: the framework's
behavior around the provider is what is under test, never the provider.
"""

import asyncio
import math
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from pydantic import BaseModel, Field

from vibrantine.contract import (
    NEVER_CANCELLED,
    CallContext,
    CapabilitySet,
    Commission,
    CommissionResult,
    CostMetrics,
    Provenance,
)
from vibrantine.dispatch import dispatch
from vibrantine.models import Model
from vibrantine.orchestrator import run_commission
from vibrantine.testing import (
    FIXTURE_MODEL,
    AlwaysCancelled,
    ScriptedLLM,
    llm_response,
    scripted_model,
)

# FIXTURE_MODEL pricing: $0.50/M in, $3.00/M out. The default llm_response
# token counts (100 in, 50 out) therefore cost exactly $0.0002 per call.
COST_PER_DEFAULT_CALL = 0.0002


class _Q(BaseModel):
    question: str = Field(description="A question.")


class _A(BaseModel):
    answer: str = Field(description="An answer.")


class _Probe(Commission[_Q, _A]):
    """A minimal basic Commission riding the default LLM loop."""

    name: ClassVar[str] = "probe"
    description: ClassVar[str] = "Answers a question. Test double."
    input_type: ClassVar[type] = _Q
    output_type: ClassVar[type] = _A
    system_prompt: ClassVar[str | None] = "You are a probe."

    def build_user_message(self, input: _Q, ctx: CallContext) -> str:
        return input.question


class _Child(Commission[_Q, _A]):
    """A second LLM Commission, for parent/child room and fuse trees."""

    name: ClassVar[str] = "child"
    description: ClassVar[str] = "Answers a sub-question. Test double."
    input_type: ClassVar[type] = _Q
    output_type: ClassVar[type] = _A
    system_prompt: ClassVar[str | None] = "You are a child probe."

    def build_user_message(self, input: _Q, ctx: CallContext) -> str:
        return input.question


class _EchoIn(BaseModel):
    text: str = Field(description="Text to echo.")


class _EchoOut(BaseModel):
    text: str = Field(description="The echoed text.")


class _EchoTool(Commission[_EchoIn, _EchoOut]):
    """A deterministic leaf tool: no LLM, no cost."""

    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "Echoes its input text. Test double."
    input_type: ClassVar[type] = _EchoIn
    output_type: ClassVar[type] = _EchoOut
    deterministic: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def _run(self, input: _EchoIn, ctx: CallContext) -> CommissionResult[_EchoOut]:
        return self._succeed(
            _EchoOut(text=input.text),
            provenance=_prov("echo"),
            cost=CostMetrics(estimated_usd=0.0),
        )


def _prov(source: str) -> Provenance:
    return Provenance(source=source, fetched_at=datetime.now(UTC), confidence="grounded")


def _conclude(answer: str = "done", *, in_tokens: int = 100, out_tokens: int = 50) -> Any:
    return llm_response(
        tool_calls=[("c1", "conclude", {"answer": answer})],
        in_tokens=in_tokens,
        out_tokens=out_tokens,
    )


def _scripted(responses: list[Any], *, id: str = FIXTURE_MODEL.id) -> Model:
    """A catalog entry served by a fresh ScriptedLLM with these responses."""
    return scripted_model(ScriptedLLM(responses), id=id)


class _Gauge:
    """Shared concurrency meter across the paced fake providers."""

    def __init__(self) -> None:
        self.current = 0
        self.peak = 0


class _PacedCompletions:
    """One queued response per call, each held open for `delay` seconds."""

    def __init__(self, responses: list[Any], delay: float, gauge: _Gauge | None) -> None:
        self._responses = responses
        self._delay = delay
        self._gauge = gauge

    async def create(self, **kwargs: Any) -> Any:
        if self._gauge is not None:
            self._gauge.current += 1
            self._gauge.peak = max(self._gauge.peak, self._gauge.current)
        try:
            await asyncio.sleep(self._delay)
        finally:
            if self._gauge is not None:
                self._gauge.current -= 1
        return self._responses.pop(0)


def _paced_entry(
    responses: list[Any],
    *,
    delay: float,
    gauge: _Gauge | None = None,
    id: str = FIXTURE_MODEL.id,
) -> Model:
    """A catalog entry whose fake provider holds each call open for `delay`."""
    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=_PacedCompletions(responses, delay, gauge))
    )
    return scripted_model(cast(ScriptedLLM, fake), id=id)


# --- Hello world and configuration ----------------------------------------


async def test_defaults_run_in_one_line_and_succeed() -> None:
    result = await run_commission(_Probe(), _Q(question="?"), models=[_scripted([_conclude("42")])])
    assert result.status == "success"
    assert result.error is None
    assert result.output is not None and result.output.answer == "42"


async def test_bad_run_configuration_is_a_validation_failure() -> None:
    probe = _Probe()
    for kwargs, needle in (
        ({"concurrency": 0}, "concurrency"),
        ({"concurrency": 1.5}, "concurrency"),
        ({"max_llm_calls": 0}, "max_llm_calls"),
        ({"max_llm_calls": 1.5}, "max_llm_calls"),
        ({"time_limit_seconds": 0}, "time_limit_seconds"),
        ({"budget_usd": -1.0}, "budget_usd"),
        ({"time_limit_seconds": math.nan}, "time_limit_seconds"),
        ({"budget_usd": math.nan}, "budget_usd"),
        (
            {
                "models": [
                    Model(
                        id="bad/negative-price",
                        input_usd_per_million=-1.0,
                        output_usd_per_million=1.0,
                    )
                ]
            },
            "finite and non-negative",
        ),
        (
            {
                "models": [
                    Model(
                        id="bad/nan-price",
                        input_usd_per_million=math.nan,
                        output_usd_per_million=1.0,
                    )
                ]
            },
            "finite and non-negative",
        ),
        ({"models": [FIXTURE_MODEL, FIXTURE_MODEL]}, "more than once"),
        (
            {"models": [Model(id="bad/reserved-params", params={"model": "sneaky"})]},
            "params the framework owns",
        ),
        ({"default_model": "fixture/absent"}, "not in models="),
        ({"models": [FIXTURE_MODEL, Model(id="other/model")]}, "default_model"),
    ):
        result = await run_commission(probe, _Q(question="?"), **cast(dict[str, Any], kwargs))
        assert result.status == "failure"
        assert result.error is not None
        assert result.error.kind == "validation"
        assert needle in result.error.detail


async def test_unknown_model_name_fails_fast_and_loud() -> None:
    # The Commission names a model the run never registered: an immediate
    # validation failure naming the id and the catalog, before anything is
    # sent or spent. The silent bare-OpenRouter fallback is gone.
    probe = _Probe(model="fixture/not-registered")
    result = await run_commission(probe, _Q(question="?"), models=[_scripted([_conclude()])])
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "fixture/not-registered" in result.error.detail
    assert "not in this run's catalog" in result.error.detail
    assert result.cost.estimated_usd == 0.0


async def test_profile_params_ride_every_provider_call() -> None:
    # A profile's params merge verbatim into chat.completions.create,
    # alongside (never instead of) the structural keys the loop owns.
    scripted = ScriptedLLM([_conclude()])
    entry = scripted_model(scripted, params={"temperature": 0.2, "top_p": 0.9})
    result = await run_commission(_Probe(), _Q(question="?"), models=[entry])
    assert result.status == "success"
    sent = scripted.calls[0]
    assert sent["temperature"] == 0.2
    assert sent["top_p"] == 0.9
    assert sent["model"] == FIXTURE_MODEL.id


async def test_two_profiles_of_one_wire_id_are_distinct_entries() -> None:
    # The name/id split's point: one underlying model, two catalog entries
    # differing in params, each resolvable by its role name. Distinct fakes
    # prove the node's reference picked the right profile.
    cool_fake, hot_fake = ScriptedLLM([_conclude()]), ScriptedLLM([_conclude()])
    cool = scripted_model(cool_fake, name="fast-cool", params={"temperature": 0.1})
    hot = scripted_model(hot_fake, name="fast-hot", params={"temperature": 1.0})
    probe = _Probe(model="fast-hot")
    result = await run_commission(
        probe, _Q(question="?"), models=[cool, hot], default_model="fast-cool"
    )
    assert result.status == "success"
    assert cool_fake.calls == []
    assert hot_fake.calls[0]["temperature"] == 1.0
    assert hot_fake.calls[0]["model"] == FIXTURE_MODEL.id


async def test_call_rows_carry_the_profile_name_beside_the_wire_id(
    open_test_run: Any,
) -> None:
    # Forensics: "which profile made this call" and "what was on the wire"
    # are different questions exactly when a catalog defines roles.
    scripted = ScriptedLLM([_conclude()])
    entry = scripted_model(scripted, name="deep-thinker")
    async with open_test_run(models=[entry]) as ctx:
        await dispatch(_Probe(model="deep-thinker"), _Q(question="?"), ctx)
        rows = ctx._gatekeeper.calls  # pyright: ignore[reportPrivateUsage]
    assert len(rows) == 1
    assert rows[0]["model"] == FIXTURE_MODEL.id
    assert rows[0]["model_name"] == "deep-thinker"


async def test_caller_cancellation_is_not_relabelled_run_halted() -> None:
    # The caller's token and the breaker share one signal, but a pure caller
    # cancellation is not a fuse trip: no rewrite, the ordinary kind stands.
    result = await run_commission(_Probe(), _Q(question="?"), cancel=AlwaysCancelled())
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"


# --- Fuses -----------------------------------------------------------------


async def test_llm_call_fuse_trips_and_root_speaks_run_halted() -> None:
    # Turn one calls the echo tool; the loop's second provider call is then
    # refused at the 1-call limit. The node reports an ordinary cancellation;
    # only the root speaks run_halted, with the fuse named for a reader who
    # has no other context.
    probe = _Probe(toolbox=(_EchoTool(),))
    script = _scripted([llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()])
    result = await run_commission(probe, _Q(question="?"), models=[script], max_llm_calls=1)
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert result.error.retryable is False
    assert "llm-call fuse tripped" in result.error.detail
    assert "1-call limit" in result.error.detail
    # No backend and no on_llm_call: the detail must not point at a call
    # log nobody can retrieve; it says how to capture one instead.
    assert "Wire backend=" in result.error.detail
    # True total spend: the one completed call, reported even though the
    # run was torn down.
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


async def test_spend_fuse_trips_and_reports_true_total_spend() -> None:
    # One turn costing $0.0002 against a $0.0001 budget: the node's own
    # allocation check fails it, the fuse trips at settle, and the root is
    # rewritten to run_halted with the observed total in the cost field.
    result = await run_commission(
        _Probe(), _Q(question="?"), models=[_scripted([_conclude()])], budget_usd=0.0001
    )
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "spend fuse tripped" in result.error.detail
    assert "true total spend" in result.error.detail
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


async def test_time_fuse_trips_at_the_dispatch_seam() -> None:
    # The first provider call is held open well past the deadline (well past
    # Windows' coarse monotonic tick, too); the trip then fires at the next
    # dispatch (the echo child), between LLM calls, not only at the door.
    probe = _Probe(toolbox=(_EchoTool(),))
    entry = _paced_entry(
        [llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()],
        delay=0.2,
    )
    result = await run_commission(probe, _Q(question="?"), models=[entry], time_limit_seconds=0.05)
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "time fuse tripped" in result.error.detail
    # The held-open first call completed and is fully counted.
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


async def test_exactly_n_calls_do_not_trip_the_count_fuse() -> None:
    # The count check reads "this call would be call N+1": a run that makes
    # exactly N calls and concludes has not tripped anything.
    result = await run_commission(
        _Probe(), _Q(question="?"), models=[_scripted([_conclude()])], max_llm_calls=1
    )
    assert result.status == "success"


async def test_spend_exactly_at_the_limit_does_not_trip() -> None:
    # Spending exactly the budget is within budget: the fuse is strictly
    # past the limit, the same reading as the node-level checks.
    result = await run_commission(
        _Probe(),
        _Q(question="?"),
        models=[_scripted([_conclude()])],
        budget_usd=COST_PER_DEFAULT_CALL,
    )
    assert result.status == "success"


async def test_zero_budget_runs_a_free_model() -> None:
    # budget_usd=0.0 arms the spend fuse at $0, but a genuinely free model
    # (local Ollama shape) never spends past it: the run proceeds instead of
    # being refused before its first call.
    response = _conclude("gratis")
    response.usage = None
    free = scripted_model(
        ScriptedLLM([response]),
        input_usd_per_million=0.0,
        output_usd_per_million=0.0,
    )
    result = await run_commission(_Probe(), _Q(question="?"), models=[free], budget_usd=0.0)
    assert result.status == "success"
    assert result.output is not None and result.output.answer == "gratis"
    assert result.cost.estimated_usd == 0.0


async def test_budgeted_paid_call_without_usage_fails_instead_of_counting_as_free() -> None:
    response = _conclude()
    response.usage = None

    result = await run_commission(
        _Probe(),
        _Q(question="?"),
        models=[_scripted([response])],
        budget_usd=1.0,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "returned no token usage" in result.error.detail
    assert result.cost.estimated_usd == 0.0


async def test_unbudgeted_paid_call_may_run_without_usage() -> None:
    response = _conclude("unmetered")
    response.usage = None

    result = await run_commission(_Probe(), _Q(question="?"), models=[_scripted([response])])

    assert result.status == "success"
    assert result.output is not None and result.output.answer == "unmetered"


async def test_a_root_that_concludes_despite_a_trip_keeps_its_success() -> None:
    # Winding down and concluding with what it has is the designed response
    # to a trip, not a failure to override: the rewrite leaves successful
    # roots alone.
    class _MakesDo(Commission[_Q, _A]):
        name: ClassVar[str] = "makes_do"
        description: ClassVar[str] = "Concludes in Python after a refused child."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        def __init__(self, first: Commission[_Q, _A], second: Commission[_Q, _A]) -> None:
            super().__init__()
            self._first = first
            self._second = second

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            first = await dispatch(self._first, input, ctx)
            second = await dispatch(self._second, input, ctx)
            assert first.status == "success"
            assert second.status == "failure"  # refused at the 1-call limit
            return self._succeed(
                _A(answer="made do"),
                provenance=_prov("makes_do"),
                cost=CostMetrics(
                    estimated_usd=first.cost.estimated_usd + second.cost.estimated_usd
                ),
            )

    coordinator = _MakesDo(_Child(model="fixture/a"), _Child(model="fixture/b"))
    result = await run_commission(
        coordinator,
        _Q(question="?"),
        models=[
            _scripted([_conclude()], id="fixture/a"),
            _scripted([_conclude()], id="fixture/b"),
        ],
        default_model="fixture/a",
        max_llm_calls=1,
    )
    assert result.status == "success"
    assert result.error is None
    assert result.output is not None and result.output.answer == "made do"


async def test_in_flight_calls_settle_and_count_after_a_trip() -> None:
    # Two children in flight together; the fast one's settle reaches the
    # spend limit and trips, the slow one finishes afterwards and still
    # counts. The root detail reports the in-flight completion and the true
    # total, which is what makes "every dollar reported" checkable.
    fast = _Child(model="fixture/fast")
    slow = _Child(model="fixture/slow")

    class _FailsAfter(Commission[_Q, _A]):
        name: ClassVar[str] = "fails_after"
        description: ClassVar[str] = "Gathers two children, then reports the grant exhausted."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            first, second = await asyncio.gather(
                dispatch(fast, input, ctx), dispatch(slow, input, ctx)
            )
            # The trip flipped the shared stop signal; reporting the grant
            # exhausted is the trip-descended shape the causal rewrite
            # claims (the root grant and the spend fuse are one number
            # read two ways).
            assert ctx.cancel.is_cancelled
            return self._fail(
                "budget_exceeded",
                "children spent past the grant",
                retryable=False,
                provenance=_prov("fails_after"),
                cost=CostMetrics(
                    estimated_usd=first.cost.estimated_usd + second.cost.estimated_usd
                ),
            )

    # The fast call alone passes the limit (the fuse is strictly past, so
    # the limit sits below one call's cost), tripping at its settle while
    # the slow call is still open.
    result = await run_commission(
        _FailsAfter(),
        _Q(question="?"),
        models=[
            _paced_entry([_conclude(in_tokens=100, out_tokens=50)], delay=0.02, id="fixture/fast"),
            _paced_entry([_conclude(in_tokens=10, out_tokens=5)], delay=0.2, id="fixture/slow"),
        ],
        default_model="fixture/fast",
        budget_usd=COST_PER_DEFAULT_CALL / 2,
    )
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "spend fuse tripped" in result.error.detail
    assert "1 in-flight call(s) completed" in result.error.detail
    slow_cost = (10 * 0.50 + 5 * 3.00) / 1_000_000
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL + slow_cost)


async def test_an_unrelated_root_failure_is_not_masked_by_a_trip() -> None:
    # A fuse trips, and the root then fails for its own, unrelated reason:
    # the rewrite is causal, so the root's own error, detail, and cost
    # survive instead of being overwritten by the fuse story. The trip
    # stays visible in the call log.
    child = _Child()

    class _OwnBug(Commission[_Q, _A]):
        name: ClassVar[str] = "own_bug"
        description: ClassVar[str] = "Fails for its own reason after a trip."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            first = await dispatch(child, input, ctx)  # admitted: call 1 of 1
            second = await dispatch(child, input, ctx)  # refused: trips the fuse
            assert first.status == "success" and second.status == "failure"
            return self._fail(
                "internal",
                "a bug of my own, nothing to do with the fuse",
                retryable=False,
                provenance=_prov("own_bug"),
                cost=CostMetrics(estimated_usd=first.cost.estimated_usd),
            )

    result = await run_commission(
        _OwnBug(),
        _Q(question="?"),
        models=[_scripted([_conclude(), _conclude()])],
        max_llm_calls=1,
    )
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "a bug of my own" in result.error.detail
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


async def test_a_roots_own_cancellation_is_not_claimed_by_a_coincidental_trip() -> None:
    # The rewrite claims the breaker's own failures (the stamp), never a
    # cancellation the root authored itself: a coordinator that cancelled
    # one of its own branches keeps its story even though a fuse tripped
    # at the same time. Kind-matching would have masked it.
    child = _Child()

    class _OwnCancel(Commission[_Q, _A]):
        name: ClassVar[str] = "own_cancel"
        description: ClassVar[str] = "Reports its own scoped cancellation, mid-trip."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            first = await dispatch(child, input, ctx)  # admitted: call 1 of 1
            second = await dispatch(child, input, ctx)  # refused: trips the fuse
            assert first.status == "success" and second.status == "failure"
            return self._fail(
                "cancelled",
                "branch B cancelled by my own scoped token",
                retryable=False,
                provenance=_prov("own_cancel"),
                cost=CostMetrics(estimated_usd=first.cost.estimated_usd),
            )

    result = await run_commission(
        _OwnCancel(),
        _Q(question="?"),
        models=[_scripted([_conclude(), _conclude()])],
        max_llm_calls=1,
    )
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert "my own scoped token" in result.error.detail
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


async def test_a_propagated_refusal_keeps_its_stamp_and_is_claimed() -> None:
    # A coordinator that passes a child's breaker-refused failure up
    # unchanged keeps the stamp on the error object, so the root rewrite
    # claims it and tells the fuse story with true total spend.
    child = _Child()

    class _Propagates(Commission[_Q, _A]):
        name: ClassVar[str] = "propagates"
        description: ClassVar[str] = "Returns its child's refusal untouched."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            first = await dispatch(child, input, ctx)  # admitted: call 1 of 1
            second = await dispatch(child, input, ctx)  # refused: trips the fuse
            assert first.status == "success" and second.status == "failure"
            return second

    result = await run_commission(
        _Propagates(),
        _Q(question="?"),
        models=[_scripted([_conclude(), _conclude()])],
        max_llm_calls=1,
    )
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "llm-call fuse tripped" in result.error.detail
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


async def test_a_grant_stripped_subtree_is_still_dollar_accounted() -> None:
    # run_commission(budget_usd=...) arms the spend fuse; a coordinator may legally
    # strip a child's grant via replace(). The child's paid call that omits
    # usage must still fail structurally (the fuse cannot account what the
    # door cannot meter), not settle at a silent $0.
    child = _Child()

    class _Strips(Commission[_Q, _A]):
        name: ClassVar[str] = "strips"
        description: ClassVar[str] = "Dispatches its child without a grant."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            return await dispatch(child, input, replace(ctx, budget_usd=None))

    response = _conclude()
    response.usage = None
    result = await run_commission(
        _Strips(),
        _Q(question="?"),
        models=[_scripted([response])],
        budget_usd=1.0,
    )
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "returned no token usage" in result.error.detail


async def test_the_persisted_root_record_speaks_run_halted_too(tmp_path: Any) -> None:
    # The rewrite happens inside the root's dispatch, before persistence:
    # the stored record and the returned envelope must tell one story
    # (kind, detail, and true total spend), not two.
    import json
    import sqlite3

    from vibrantine.persistence import SqliteBackend

    backend = SqliteBackend(tmp_path / "runs.db")
    probe = _Probe(toolbox=(_EchoTool(),))
    script = _scripted([llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()])
    result = await run_commission(
        probe,
        _Q(question="?"),
        models=[script],
        max_llm_calls=1,
        backend=backend,
        record="always",
    )
    assert result.error is not None and result.error.kind == "run_halted"
    # With a call-log-capable backend wired, the pointer is real.
    assert "full call log under run" in result.error.detail

    with sqlite3.connect(tmp_path / "runs.db") as conn:
        row = conn.execute(
            "SELECT status, cost_usd, record FROM records WHERE parent_run_id IS NULL"
        ).fetchone()
    status, cost_usd, record_json = row
    stored_result = json.loads(record_json)["result"]
    assert status == "failure"
    assert stored_result["error"]["kind"] == "run_halted"
    assert stored_result["error"]["detail"] == result.error.detail
    assert math.isclose(cast(float, cost_usd), result.cost.estimated_usd)


async def test_run_halted_does_not_claim_a_call_log_that_failed_to_persist() -> None:
    class FailingCallLogBackend:
        async def store_calls(self, root_run_id: str | None, calls: list[dict[str, Any]]) -> None:
            raise OSError("disk full")

    probe = _Probe(toolbox=(_EchoTool(),))
    script = _scripted([llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()])
    result = await run_commission(
        probe,
        _Q(question="?"),
        models=[script],
        max_llm_calls=1,
        backend=cast(Any, FailingCallLogBackend()),
    )

    assert result.error is not None and result.error.kind == "run_halted"
    assert "call-log persistence failed" in result.error.detail
    assert "full call log under run" not in result.error.detail


async def test_a_bubbled_halt_from_a_custom_run_reports_cancelled(open_test_run: Any) -> None:
    # A custom _run that lets the provider door's refusal escape is not a
    # contract breach: dispatch translates it to the same node-level
    # cancellation the framework loop reports, not an `internal` failure.
    from vibrantine._gatekeeper import RunHaltedError

    class _Bubbles(Commission[_EchoIn, _EchoOut]):
        name: ClassVar[str] = "bubbles"
        description: ClassVar[str] = "Lets a door refusal escape. Test double."
        input_type: ClassVar[type] = _EchoIn
        output_type: ClassVar[type] = _EchoOut

        async def _run(self, input: _EchoIn, ctx: CallContext) -> CommissionResult[_EchoOut]:
            raise RunHaltedError("llm-call fuse tripped: call 2 refused at the 1-call limit")

    async with open_test_run() as ctx:
        result = await dispatch(_Bubbles(), _EchoIn(text="hi"), ctx)
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert "stop signal" in result.error.detail


async def test_a_backstop_failure_reports_the_nodes_settled_spend(open_test_run: Any) -> None:
    # Best-effort floor (ruled 2026-07-12): a _run that raised after real
    # governed calls reports its own settled door spend from the run log,
    # not $0. Children's envelopes unwound with the stack and stay
    # excluded; the run log still holds every row.
    class _SpendsThenRaises(Commission[_EchoIn, _EchoOut]):
        name: ClassVar[str] = "spends_then_raises"
        description: ClassVar[str] = "Makes one governed call, then raises. Test double."
        input_type: ClassVar[type] = _EchoIn
        output_type: ClassVar[type] = _EchoOut

        async def _run(self, input: _EchoIn, ctx: CallContext) -> CommissionResult[_EchoOut]:
            gatekeeper = ctx._gatekeeper  # pyright: ignore[reportPrivateUsage]
            assert gatekeeper is not None
            entry = gatekeeper.resolve_model(None)
            async with gatekeeper.provider_call(
                entry=entry, commission_name=self.name, grant_usd=None
            ) as ticket:
                response = await ticket.client.chat.completions.create(model=entry.id, messages=[])
                assert response.usage is not None
                ticket.record_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
            raise RuntimeError("boom after real spend")

    async with open_test_run(models=[_scripted([_conclude()])]) as ctx:
        result = await dispatch(_SpendsThenRaises(), _EchoIn(text="hi"), ctx)
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


# --- The room ---------------------------------------------------------------


async def test_room_bounds_provider_calls_across_the_tree() -> None:
    gauge = _Gauge()
    children = [_Child(model=f"fixture/c{i}") for i in range(4)]

    class _Fan(Commission[_Q, _A]):
        name: ClassVar[str] = "fan"
        description: ClassVar[str] = "Fans out to four LLM children at once."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            results = await asyncio.gather(*(dispatch(c, input, ctx) for c in children))
            assert all(r.status == "success" for r in results)
            return self._succeed(
                _A(answer="fanned"), provenance=_prov("fan"), cost=CostMetrics(estimated_usd=0.0)
            )

    result = await run_commission(
        _Fan(),
        _Q(question="?"),
        models=[
            _paced_entry([_conclude()], delay=0.05, gauge=gauge, id=f"fixture/c{i}")
            for i in range(4)
        ],
        default_model="fixture/c0",
        concurrency=2,
    )
    assert result.status == "success"
    assert gauge.peak <= 2


async def test_room_of_one_with_a_nested_llm_tree_completes() -> None:
    # The chair is released before children dispatch (count leaf work, not
    # coordinators), so even a room of 1 cannot deadlock a parent that
    # delegates to an LLM child mid-loop.
    child = _Child(model="fixture/child")
    parent = _Probe(toolbox=(child,))
    result = await asyncio.wait_for(
        run_commission(
            parent,
            _Q(question="?"),
            models=[
                _scripted(
                    [llm_response(tool_calls=[("t1", "child", {"question": "sub?"})]), _conclude()]
                ),
                _scripted([_conclude("from child")], id="fixture/child"),
            ],
            default_model=FIXTURE_MODEL.id,
            concurrency=1,
        ),
        timeout=10,
    )
    assert result.status == "success"


# --- The tool ceiling ---------------------------------------------------------


async def test_tool_ceiling_clamps_the_llm_menu() -> None:
    # The effective menu is toolbox ∩ branch grant ∩ ceiling; an empty
    # ceiling clamps everything out, while the framework-injected conclude
    # is never gated.
    fake = ScriptedLLM([_conclude()])
    probe = _Probe(toolbox=(_EchoTool(),))
    result = await run_commission(
        probe, _Q(question="?"), models=[scripted_model(fake)], tool_ceiling=[]
    )
    assert result.status == "success"
    menu = [t["function"]["name"] for t in fake.calls[0]["tools"]]
    assert menu == ["conclude"]


async def test_a_widened_grant_stays_clamped_by_the_ceiling() -> None:
    # Custom code can rebuild a branch grant via replace() (here: widened to
    # unrestricted), but not the ceiling reference, which lives on the shared
    # run object: the child's menu stays clamped.
    fake = ScriptedLLM([_conclude()])
    child = _Child(toolbox=(_EchoTool(),))

    class _Widener(Commission[_Q, _A]):
        name: ClassVar[str] = "widener"
        description: ClassVar[str] = "Widens its child's grant to unrestricted."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            wide = replace(ctx, capabilities=CapabilitySet())
            child_result = await dispatch(child, input, wide)
            assert child_result.status == "success"
            return self._succeed(
                _A(answer="widened"),
                provenance=_prov("widener"),
                cost=CostMetrics(estimated_usd=child_result.cost.estimated_usd),
            )

    result = await run_commission(
        _Widener(),
        _Q(question="?"),
        models=[scripted_model(fake)],
        tool_ceiling=[],
        capabilities=CapabilitySet(tools=frozenset()),
    )
    assert result.status == "success"
    menu = [t["function"]["name"] for t in fake.calls[0]["tools"]]
    assert menu == ["conclude"]


async def test_tool_ceiling_permits_exactly_its_names() -> None:
    fake = ScriptedLLM([_conclude()])
    probe = _Probe(toolbox=(_EchoTool(),))
    result = await run_commission(
        probe, _Q(question="?"), models=[scripted_model(fake)], tool_ceiling=["echo"]
    )
    assert result.status == "success"
    menu = [t["function"]["name"] for t in fake.calls[0]["tools"]]
    assert menu == ["echo", "conclude"]


# --- One front door ----------------------------------------------------------


async def test_dispatch_outside_a_run_refuses() -> None:
    with pytest.raises(RuntimeError, match="outside a run; use run_commission"):
        await dispatch(_EchoTool(), _EchoIn(text="hi"), CallContext())


async def test_nested_run_commission_refuses_and_teaches_dispatch() -> None:
    class _Nester(Commission[_Q, _A]):
        name: ClassVar[str] = "nester"
        description: ClassVar[str] = "Calls run_commission from inside a run."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            return await run_commission(_EchoTool(), _EchoIn(text="hi"))  # type: ignore[return-value]

    result = await run_commission(_Nester(), _Q(question="?"))
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "inside a run; use dispatch" in result.error.detail


async def test_dispatch_refuses_a_context_from_outside_the_run() -> None:
    # A coordinator that builds a fresh CallContext instead of deriving from
    # its own: the no-swap rule refuses it, and the backstop converts the
    # refusal into a failure value at the coordinator's own boundary.
    class _Smuggler(Commission[_Q, _A]):
        name: ClassVar[str] = "smuggler"
        description: ClassVar[str] = "Dispatches a child with a fresh context."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            await dispatch(_EchoTool(), _EchoIn(text="hi"), CallContext())
            return self._succeed(
                _A(answer="never"), provenance=_prov("smuggler"), cost=CostMetrics(estimated_usd=0)
            )

    result = await run_commission(_Smuggler(), _Q(question="?"))
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "does not carry the run in progress" in result.error.detail


# --- The stop signal ---------------------------------------------------------


class _PeeksAtCancel(Commission[_EchoIn, _EchoOut]):
    """A non-LLM leaf that records what its stop signal says."""

    name: ClassVar[str] = "peeker"
    description: ClassVar[str] = "Records ctx.cancel.is_cancelled. Test double."
    input_type: ClassVar[type] = _EchoIn
    output_type: ClassVar[type] = _EchoOut

    def __init__(self, seen: list[bool]) -> None:
        super().__init__(max_input_tokens=None)
        self._seen = seen

    async def _run(self, input: _EchoIn, ctx: CallContext) -> CommissionResult[_EchoOut]:
        self._seen.append(ctx.cancel.is_cancelled)
        return self._succeed(
            _EchoOut(text=input.text), provenance=_prov("peeker"), cost=CostMetrics(estimated_usd=0)
        )


async def test_a_swapped_cancel_token_cannot_sever_the_breaker() -> None:
    # A coordinator hands its whole subtree a never-cancelled token; the fuse
    # then trips. The halt is structural at the dispatch seam: the post-trip
    # child is refused before it starts, so severing the token buys the
    # subtree nothing (refuse-after-halt, ruled 2026-07-12; previously the
    # child ran and the re-wrapped token merely *showed* it the halt).
    seen: list[bool] = []
    peeker = _PeeksAtCancel(seen)
    child = _Child()

    class _Severer(Commission[_Q, _A]):
        name: ClassVar[str] = "severer"
        description: ClassVar[str] = "Swaps in its own cancel token, then keeps working."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            severed = replace(ctx, cancel=NEVER_CANCELLED)
            first = await dispatch(child, input, severed)  # admitted: call 1 of 1
            second = await dispatch(child, input, severed)  # its provider call trips the fuse
            assert first.status == "success"
            assert second.status == "failure"
            peeked = await dispatch(peeker, _EchoIn(text="after the trip"), severed)
            # The refusal is a value: this coordinator's own code keeps
            # running and still gets to conclude (the structured exit).
            assert peeked.status == "failure"
            assert peeked.error is not None
            assert "refused by the run's stop signal" in peeked.error.detail
            return self._succeed(
                _A(answer="kept working"),
                provenance=_prov("severer"),
                cost=CostMetrics(estimated_usd=0.0),
            )

    result = await run_commission(
        _Severer(),
        _Q(question="?"),
        models=[_scripted([_conclude(), _conclude()])],
        max_llm_calls=1,
    )
    assert result.status == "success"  # the root concluded; wind-down stands
    assert seen == []  # the post-trip child never started at all


async def test_a_scoped_cancel_narrows_one_branch_only() -> None:
    # The legitimate use the re-wrap preserves: a coordinator cancels one
    # branch with its own token while the sibling runs untouched.
    child = _Child()

    class _Scoper(Commission[_Q, _A]):
        name: ClassVar[str] = "scoper"
        description: ClassVar[str] = "Cancels one child by scoped token."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            scoped = replace(ctx, cancel=AlwaysCancelled())
            cancelled = await dispatch(child, input, scoped)
            alive = await dispatch(child, input, ctx)
            assert cancelled.status == "failure"
            assert cancelled.error is not None and cancelled.error.kind == "cancelled"
            assert alive.status == "success"
            return self._succeed(
                _A(answer="scoped"), provenance=_prov("scoper"), cost=CostMetrics(estimated_usd=0)
            )

    result = await run_commission(_Scoper(), _Q(question="?"), models=[_scripted([_conclude()])])
    assert result.status == "success"


# --- Teardown -----------------------------------------------------------------


async def test_run_end_closes_vended_clients(open_test_run: Any) -> None:
    # The catalog builds one real client per endpoint per run; the run's
    # exit closes it. Keyless entry (the local-Ollama shape), so the vend
    # builds a real AsyncOpenAI without needing a key or a network call.
    from vibrantine.models import ollama

    async with open_test_run(models=[ollama("test/local")]) as ctx:
        gatekeeper = ctx._gatekeeper  # pyright: ignore[reportPrivateUsage]
        assert gatekeeper is not None
        client = gatekeeper.client_for(gatekeeper.resolve_model(None))
        assert not client.is_closed()
    assert client.is_closed()


# --- The log ------------------------------------------------------------------

ROW_KEYS = {
    "run_id",
    "commission_name",
    "model",
    "model_name",
    "started_at",
    "ended_at",
    "in_tokens",
    "out_tokens",
    "cost_usd",
    "grant_usd",
    "run_calls_before",
    "run_spend_before_usd",
    "status",
}


async def test_log_keeps_one_row_per_call_including_refusals(open_test_run: Any) -> None:
    probe = _Probe(toolbox=(_EchoTool(),))
    script = _scripted([llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()])
    async with open_test_run(models=[script], max_llm_calls=1) as ctx:
        result = await dispatch(probe, _Q(question="?"), ctx)
        rows = ctx._gatekeeper.calls  # pyright: ignore[reportPrivateUsage]
    assert result.status == "failure"
    assert [row["status"] for row in rows] == ["completed", "refused"]
    for row in rows:
        assert set(row) == ROW_KEYS
        assert row["commission_name"] == "probe"
        assert row["model"] == FIXTURE_MODEL.id
    completed, refused = rows
    assert completed["run_id"] is not None
    assert completed["in_tokens"] == 100 and completed["out_tokens"] == 50
    assert math.isclose(cast(float, completed["cost_usd"]), COST_PER_DEFAULT_CALL)
    assert completed["run_calls_before"] == 0
    assert refused["in_tokens"] is None
    assert refused["cost_usd"] == 0.0


async def test_log_row_carries_the_node_grant_at_call_time(open_test_run: Any) -> None:
    async with open_test_run(models=[_scripted([_conclude()])], budget_usd=0.05) as ctx:
        await dispatch(_Probe(), _Q(question="?"), ctx)
        rows = ctx._gatekeeper.calls  # pyright: ignore[reportPrivateUsage]
    assert len(rows) == 1
    assert rows[0]["grant_usd"] == 0.05
    assert rows[0]["status"] == "completed"


async def test_on_llm_call_receives_every_row_including_refusals() -> None:
    # The live accessor: one callback per settled or refused row, and
    # collecting them is the after-the-run retrieval story.
    rows: list[dict[str, Any]] = []
    probe = _Probe(toolbox=(_EchoTool(),))
    script = _scripted([llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()])
    result = await run_commission(
        probe, _Q(question="?"), models=[script], max_llm_calls=1, on_llm_call=rows.append
    )
    assert result.status == "failure"
    assert [row["status"] for row in rows] == ["completed", "refused"]
    assert all(set(row) == ROW_KEYS for row in rows)
    # The rows were delivered live, and the root detail says so instead of
    # pointing at a persisted log that does not exist.
    assert result.error is not None
    assert "delivered live via on_llm_call" in result.error.detail


async def test_a_raising_on_llm_call_never_breaks_the_run() -> None:
    def boom(row: dict[str, Any]) -> None:
        raise RuntimeError("observer bug")

    result = await run_commission(
        _Probe(), _Q(question="?"), models=[_scripted([_conclude()])], on_llm_call=boom
    )
    assert result.status == "success"


async def test_on_llm_call_cannot_mutate_the_persisted_row(tmp_path: Any) -> None:
    import sqlite3

    from vibrantine.persistence import SqliteBackend

    def clear_row(row: dict[str, Any]) -> None:
        row.clear()

    backend = SqliteBackend(tmp_path / "runs.db")
    result = await run_commission(
        _Probe(),
        _Q(question="?"),
        models=[_scripted([_conclude()])],
        on_llm_call=clear_row,
        backend=backend,
        record="always",
    )

    assert result.status == "success"
    with sqlite3.connect(tmp_path / "runs.db") as conn:
        row = conn.execute("SELECT commission_name, status FROM calls").fetchone()
    assert row == ("probe", "completed")


async def test_calls_land_beside_records_in_sqlite(tmp_path: Any) -> None:
    import sqlite3

    from vibrantine.persistence import SqliteBackend

    backend = SqliteBackend(tmp_path / "runs.db")
    probe = _Probe(toolbox=(_EchoTool(),))
    script = _scripted([llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()])
    result = await run_commission(
        probe, _Q(question="?"), models=[script], backend=backend, record="always"
    )
    assert result.status == "success"

    with sqlite3.connect(tmp_path / "runs.db") as conn:
        calls = conn.execute(
            "SELECT root_run_id, run_id, commission_name, model, status, cost_usd FROM calls"
        ).fetchall()
        # The join the two-table design exists for: a call row's run_id
        # names the calling node's record.
        joined = conn.execute(
            """
            SELECT calls.commission_name, records.commission_name
            FROM calls JOIN records ON calls.run_id = records.run_id
            """
        ).fetchall()
    assert len(calls) == 2
    for root_run_id, run_id, commission_name, model, status, cost_usd in calls:
        assert root_run_id == result.run_id
        assert run_id is not None
        assert commission_name == "probe"
        assert model == FIXTURE_MODEL.id
        assert status == "completed"
        assert cost_usd == pytest.approx(COST_PER_DEFAULT_CALL)  # pyright: ignore[reportUnknownMemberType]
    assert joined and all(a == b for a, b in joined)


async def test_a_backend_without_store_calls_is_skipped_gracefully(tmp_path: Any) -> None:
    # The Protocol is untouched: a backend that never heard of the call log
    # or the dispatch register (FilesystemBackend, any third-party
    # implementation) keeps working.
    from vibrantine.persistence import FilesystemBackend

    result = await run_commission(
        _Probe(),
        _Q(question="?"),
        models=[_scripted([_conclude()])],
        backend=FilesystemBackend(tmp_path),
        record="always",
    )
    assert result.status == "success"


# --- The dispatch register ----------------------------------------------------

DISPATCH_ROW_KEYS = {
    "run_id",
    "parent_run_id",
    "commission_name",
    "deterministic",
    "started_at",
    "ended_at",
    "status",
}


async def test_register_keeps_one_row_per_dispatch_with_lineage(open_test_run: Any) -> None:
    # Every sanctioned invocation gets a metadata row: the run's complete
    # node ledger, children settling before their parents.
    probe = _Probe(toolbox=(_EchoTool(),))
    script = _scripted([llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()])
    async with open_test_run(models=[script]) as ctx:
        result = await dispatch(probe, _Q(question="?"), ctx)
        rows = ctx._gatekeeper.dispatches  # pyright: ignore[reportPrivateUsage]
    assert result.status == "success"
    assert [row["commission_name"] for row in rows] == ["echo", "probe"]
    for row in rows:
        assert set(row) == DISPATCH_ROW_KEYS
        assert row["started_at"] <= row["ended_at"]
    echo_row, probe_row = rows
    assert probe_row["run_id"] == result.run_id
    assert probe_row["parent_run_id"] is None
    assert probe_row["deterministic"] is False
    assert probe_row["status"] == "success"
    assert echo_row["parent_run_id"] == result.run_id
    assert echo_row["deterministic"] is True
    assert echo_row["status"] == "success"


async def test_refused_dispatch_logs_a_row_and_the_root_speaks_run_halted() -> None:
    # Refuse-after-halt: once a fuse trips, a new dispatch never starts.
    # The refusal is a value the coordinator handles (or propagates, keeping
    # the breaker stamp for the root's causal rewrite), and the register
    # records what did not run and why.
    rows: list[dict[str, Any]] = []
    child = _Child()

    class _Coordinator(Commission[_Q, _A]):
        name: ClassVar[str] = "coordinator"
        description: ClassVar[str] = "Dispatches children past a fuse trip. Test double."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            first = await dispatch(child, input, ctx)  # admitted: call 1 of 1
            assert first.status == "success"
            second = await dispatch(child, input, ctx)  # its provider call trips the fuse
            assert second.status == "failure"
            refused = await dispatch(child, input, ctx)  # refused at the dispatch seam
            assert refused.status == "failure"
            assert refused.error is not None
            assert "refused by the run's stop signal" in refused.error.detail
            assert refused.cost.estimated_usd == 0.0  # it never ran
            return refused

    result = await run_commission(
        _Coordinator(),
        _Q(question="?"),
        models=[_scripted([_conclude(), _conclude()])],
        max_llm_calls=1,
        on_dispatch=rows.append,
    )
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert [row["status"] for row in rows] == ["success", "failure", "refused", "failure"]
    assert all(set(row) == DISPATCH_ROW_KEYS for row in rows)


async def test_a_raising_on_dispatch_never_breaks_the_run() -> None:
    def boom(row: dict[str, Any]) -> None:
        raise RuntimeError("observer bug")

    result = await run_commission(
        _Probe(), _Q(question="?"), models=[_scripted([_conclude()])], on_dispatch=boom
    )
    assert result.status == "success"


async def test_dispatches_land_beside_records_in_sqlite(tmp_path: Any) -> None:
    import sqlite3

    from vibrantine.persistence import SqliteBackend

    backend = SqliteBackend(tmp_path / "runs.db")
    probe = _Probe(toolbox=(_EchoTool(),))
    script = _scripted([llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()])
    result = await run_commission(
        probe, _Q(question="?"), models=[script], backend=backend, record="always"
    )
    assert result.status == "success"

    with sqlite3.connect(tmp_path / "runs.db") as conn:
        rows = conn.execute(
            "SELECT root_run_id, run_id, parent_run_id, commission_name, "
            "deterministic, status FROM dispatches ORDER BY ended_at"
        ).fetchall()
        # The register's join: a row's run_id names the node's record, where
        # the content lives (metadata here, verbatim input/output there).
        joined = conn.execute(
            """
            SELECT dispatches.commission_name, records.commission_name
            FROM dispatches JOIN records ON dispatches.run_id = records.run_id
            """
        ).fetchall()
    assert len(rows) == 2
    echo_row, probe_row = rows
    assert echo_row[0] == result.run_id and probe_row[0] == result.run_id
    # One write: the root's own row rides the same batch (ruled 2026-07-12).
    assert probe_row[1] == result.run_id and probe_row[2] is None
    assert echo_row[2] == result.run_id
    assert (echo_row[3], echo_row[4], echo_row[5]) == ("echo", 1, "success")
    assert (probe_row[3], probe_row[4], probe_row[5]) == ("probe", 0, "success")
    assert len(joined) == 2 and all(a == b for a, b in joined)


def test_the_flag_is_self_description_and_the_shipped_tools_declare_it() -> None:
    # Base default False (the safe direction of error); every shipped tool
    # declares True. Nothing verifies the claim, deliberately: it is the
    # node's own word, log metadata only.
    import vibrantine.tools as tools

    assert Commission.deterministic is False
    tool_classes: list[type[Commission[Any, Any]]] = [
        cls
        for cls in (getattr(tools, tool_name) for tool_name in tools.__all__)  # pyright: ignore[reportAny]
        if isinstance(cls, type) and issubclass(cls, Commission)
    ]
    assert len(tool_classes) == 11
    for cls in tool_classes:
        assert cls.deterministic is True, cls.__name__
