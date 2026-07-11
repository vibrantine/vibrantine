"""Tests for the run Gatekeeper: fuses, room, stop signal, refusals, log.

The Gatekeeper is internal (no public name); these tests exercise it the
way a caller experiences it, through `run_one` kwargs and the root result,
plus the conftest `open_test_run` harness where the in-memory log itself is
inspected. Fake clients keep every run offline: the framework's behavior
around the provider is what is under test, never the provider.
"""

import asyncio
import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    CostMetrics,
    Provenance,
)
from vibrantine.dispatch import dispatch
from vibrantine.orchestrator import run_one
from vibrantine.testing import FIXTURE_MODEL, AlwaysCancelled, ScriptedLLM, llm_response

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


def _probe(responses: list[Any], **kwargs: Any) -> _Probe:
    return _Probe(client=cast(AsyncOpenAI, ScriptedLLM(responses)), model=FIXTURE_MODEL, **kwargs)


def _child(responses: list[Any]) -> _Child:
    return _Child(client=cast(AsyncOpenAI, ScriptedLLM(responses)), model=FIXTURE_MODEL)


class _Gauge:
    """Shared concurrency meter across the paced fake clients."""

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


def _paced_client(responses: list[Any], *, delay: float, gauge: _Gauge | None = None) -> Any:
    return SimpleNamespace(
        chat=SimpleNamespace(completions=_PacedCompletions(responses, delay, gauge))
    )


# --- Hello world and configuration ----------------------------------------


async def test_defaults_run_one_line_and_succeed() -> None:
    result = await run_one(_probe([_conclude("42")]), _Q(question="?"))
    assert result.status == "success"
    assert result.error is None
    assert result.output is not None and result.output.answer == "42"


async def test_bad_run_configuration_is_a_validation_failure() -> None:
    probe = _probe([_conclude()])
    for kwargs, needle in (
        ({"concurrency": 0}, "concurrency"),
        ({"max_llm_calls": 0}, "max_llm_calls"),
        ({"time_limit_seconds": 0}, "time_limit_seconds"),
        ({"budget_usd": -1.0}, "budget_usd"),
    ):
        result = await run_one(probe, _Q(question="?"), **cast(dict[str, Any], kwargs))
        assert result.status == "failure"
        assert result.error is not None
        assert result.error.kind == "validation"
        assert needle in result.error.detail


async def test_caller_cancellation_is_not_relabelled_run_halted() -> None:
    # The caller's token and the breaker share one signal, but a pure caller
    # cancellation is not a fuse trip: no rewrite, the ordinary kind stands.
    result = await run_one(_probe([_conclude()]), _Q(question="?"), cancel=AlwaysCancelled())
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"


# --- Fuses -----------------------------------------------------------------


async def test_llm_call_fuse_trips_and_root_speaks_run_halted() -> None:
    # Turn one calls the echo tool; the loop's second provider call is then
    # refused at the 1-call limit. The node reports an ordinary cancellation;
    # only the root speaks run_halted, with the fuse named for a reader who
    # has no other context.
    probe = _probe(
        [llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()],
        toolbox=(_EchoTool(),),
    )
    result = await run_one(probe, _Q(question="?"), max_llm_calls=1)
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert result.error.retryable is False
    assert "llm-call fuse tripped" in result.error.detail
    assert "1-call limit" in result.error.detail
    assert "full call log under run" in result.error.detail
    # True total spend: the one completed call, reported even though the
    # run was torn down.
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


async def test_spend_fuse_trips_and_reports_true_total_spend() -> None:
    # One turn costing $0.0002 against a $0.0001 budget: the node's own
    # allocation check fails it, the fuse trips at settle, and the root is
    # rewritten to run_halted with the observed total in the cost field.
    result = await run_one(_probe([_conclude()]), _Q(question="?"), budget_usd=0.0001)
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
    probe = _Probe(
        client=cast(
            AsyncOpenAI,
            _paced_client(
                [llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()],
                delay=0.2,
            ),
        ),
        model=FIXTURE_MODEL,
        toolbox=(_EchoTool(),),
    )
    result = await run_one(probe, _Q(question="?"), time_limit_seconds=0.05)
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "time fuse tripped" in result.error.detail
    # The held-open first call completed and is fully counted.
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL)


async def test_exactly_n_calls_do_not_trip_the_count_fuse() -> None:
    # The count check reads "this call would be call N+1": a run that makes
    # exactly N calls and concludes has not tripped anything.
    result = await run_one(_probe([_conclude()]), _Q(question="?"), max_llm_calls=1)
    assert result.status == "success"


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

    coordinator = _MakesDo(_child([_conclude()]), _child([_conclude()]))
    result = await run_one(coordinator, _Q(question="?"), max_llm_calls=1)
    assert result.status == "success"
    assert result.error is None
    assert result.output is not None and result.output.answer == "made do"


async def test_in_flight_calls_settle_and_count_after_a_trip() -> None:
    # Two children in flight together; the fast one's settle reaches the
    # spend limit and trips, the slow one finishes afterwards and still
    # counts. The root detail reports the in-flight completion and the true
    # total, which is what makes "every dollar reported" checkable.
    fast = _Child(
        client=cast(
            AsyncOpenAI, _paced_client([_conclude(in_tokens=100, out_tokens=50)], delay=0.02)
        ),
        model=FIXTURE_MODEL,
    )
    slow = _Child(
        client=cast(AsyncOpenAI, _paced_client([_conclude(in_tokens=10, out_tokens=5)], delay=0.2)),
        model=FIXTURE_MODEL,
    )

    class _FailsAfter(Commission[_Q, _A]):
        name: ClassVar[str] = "fails_after"
        description: ClassVar[str] = "Gathers two children, then fails on purpose."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            await asyncio.gather(dispatch(fast, input, ctx), dispatch(slow, input, ctx))
            return self._fail(
                "internal",
                "failing on purpose so the root rewrite can be observed",
                retryable=False,
                provenance=_prov("fails_after"),
                cost=CostMetrics(estimated_usd=0.0),
            )

    # The fast call alone reaches the limit exactly, so its own node check
    # ($0.0002 > $0.0002 is false) passes and only the fuse notices.
    result = await run_one(_FailsAfter(), _Q(question="?"), budget_usd=COST_PER_DEFAULT_CALL)
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "spend fuse tripped" in result.error.detail
    assert "1 in-flight call(s) completed" in result.error.detail
    slow_cost = (10 * 0.50 + 5 * 3.00) / 1_000_000
    assert math.isclose(result.cost.estimated_usd, COST_PER_DEFAULT_CALL + slow_cost)


# --- The room ---------------------------------------------------------------


async def test_room_bounds_provider_calls_across_the_tree() -> None:
    gauge = _Gauge()
    children = [
        _Child(
            client=cast(AsyncOpenAI, _paced_client([_conclude()], delay=0.05, gauge=gauge)),
            model=FIXTURE_MODEL,
        )
        for _ in range(4)
    ]

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

    result = await run_one(_Fan(), _Q(question="?"), concurrency=2)
    assert result.status == "success"
    assert gauge.peak <= 2


async def test_room_of_one_with_a_nested_llm_tree_completes() -> None:
    # The chair is released before children dispatch (count leaf work, not
    # coordinators), so even a room of 1 cannot deadlock a parent that
    # delegates to an LLM child mid-loop.
    child = _child([_conclude("from child")])
    parent = _probe(
        [llm_response(tool_calls=[("t1", "child", {"question": "sub?"})]), _conclude("done")],
        toolbox=(child,),
    )
    result = await asyncio.wait_for(run_one(parent, _Q(question="?"), concurrency=1), timeout=10)
    assert result.status == "success"


# --- One front door ----------------------------------------------------------


async def test_dispatch_outside_a_run_refuses() -> None:
    with pytest.raises(RuntimeError, match="outside a run; use run_one"):
        await dispatch(_EchoTool(), _EchoIn(text="hi"), CallContext())


async def test_nested_run_one_refuses_and_teaches_dispatch() -> None:
    class _Nester(Commission[_Q, _A]):
        name: ClassVar[str] = "nester"
        description: ClassVar[str] = "Calls run_one from inside a run."
        input_type: ClassVar[type] = _Q
        output_type: ClassVar[type] = _A

        async def _run(self, input: _Q, ctx: CallContext) -> CommissionResult[_A]:
            return await run_one(_EchoTool(), _EchoIn(text="hi"))  # type: ignore[return-value]

    result = await run_one(_Nester(), _Q(question="?"))
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

    result = await run_one(_Smuggler(), _Q(question="?"))
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "does not carry the run in progress" in result.error.detail


# --- The log ------------------------------------------------------------------

ROW_KEYS = {
    "run_id",
    "commission_name",
    "model",
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
    probe = _probe(
        [llm_response(tool_calls=[("t1", "echo", {"text": "hi"})]), _conclude()],
        toolbox=(_EchoTool(),),
    )
    async with open_test_run(max_llm_calls=1) as ctx:
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
    async with open_test_run(budget_usd=0.05) as ctx:
        await dispatch(_probe([_conclude()]), _Q(question="?"), ctx)
        rows = ctx._gatekeeper.calls  # pyright: ignore[reportPrivateUsage]
    assert len(rows) == 1
    assert rows[0]["grant_usd"] == 0.05
    assert rows[0]["status"] == "completed"
