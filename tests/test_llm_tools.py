"""Tests for the LLM-loop helpers.

Covers how `run_llm_loop` translates a Commission's opening message
(`str | list[ContentPart]`) into the content the provider actually receives,
how child results are rendered back to the LLM (partial keeps its output),
and the free-text nudge. A `ScriptedLLM` fake, registered as the run
catalog's only entry via `scripted_model`, records each call so the
messages it was sent can be inspected. The loop requires a live run scope,
so direct calls take their ctx from the `open_test_run` harness; fuses stay
off there, keeping the budget tests pure allocation tests.
"""

import base64
import io
import json
import math
import wave
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import httpx
import openai
import pytest
from pydantic import BaseModel, Field

from vibrantine import run_commission
from vibrantine.contract import (
    AudioPart,
    CallContext,
    Commission,
    CommissionResult,
    ContentPart,
    CostMetrics,
    ErrorState,
    ImagePart,
    Provenance,
    TextPart,
)
from vibrantine.llm_tools import run_llm_loop
from vibrantine.models import Model
from vibrantine.persistence import FilesystemBackend
from vibrantine.testing import ScriptedLLM, llm_response, scripted_model

# The `open_test_run` fixture's shape: a factory whose `async with` yields a
# CallContext inside an open run scope.
OpenTestRun = Callable[..., AbstractAsyncContextManager[CallContext]]


def _models(fake: ScriptedLLM, prices_per_million: tuple[float, float] = (0.0, 0.0)) -> list[Model]:
    """A one-entry run catalog serving `fake`, priced per million tokens.

    Stands in for the loop's retired `prices_per_million=` kwarg: the loop
    now takes its prices from the run catalog's entry, so each test
    registers its fake at the same rates its arithmetic was written
    against. The single entry is the run default, so loop calls pass
    `model=None`.
    """
    input_usd, output_usd = prices_per_million
    return [
        scripted_model(fake, input_usd_per_million=input_usd, output_usd_per_million=output_usd)
    ]


class _Out(BaseModel):
    answer: str


async def _run(open_test_run: OpenTestRun, user_message: str | list[Any]) -> dict[str, Any]:
    """Run one loop with the given opening message; return the user message sent."""
    fake = ScriptedLLM(
        [llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})], in_tokens=10, out_tokens=5)]
    )
    async with open_test_run(models=_models(fake)) as ctx:
        await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message=user_message,
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    messages = fake.calls[0]["messages"]
    return next(m for m in messages if m["role"] == "user")


async def test_bare_string_is_sent_as_plain_content(open_test_run: OpenTestRun) -> None:
    # A bare str is the single-text case; it rides through unchanged.
    user_msg = await _run(open_test_run, "just text")
    assert user_msg["content"] == "just text"


async def test_text_parts_translate_to_text_content(open_test_run: OpenTestRun) -> None:
    user_msg = await _run(open_test_run, [TextPart(text="hello"), TextPart(text="world")])
    assert user_msg["content"] == [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "world"},
    ]


async def test_mixed_parts_preserve_order_and_image_shape(open_test_run: OpenTestRun) -> None:
    user_msg = await _run(
        open_test_run,
        [
            TextPart(text="describe this:"),
            ImagePart(image_url="data:image/png;base64,AAAA"),
        ],
    )
    assert user_msg["content"] == [
        {"type": "text", "text": "describe this:"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]


def _tiny_wav_b64() -> str:
    """A base64 WAV clip built with the stdlib: 10 ms of mono silence."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b"\x00\x00" * 80)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


async def test_audio_part_translates_to_input_audio(open_test_run: OpenTestRun) -> None:
    wav = _tiny_wav_b64()
    user_msg = await _run(
        open_test_run, [TextPart(text="transcribe:"), AudioPart(data=wav, format="wav")]
    )
    assert user_msg["content"] == [
        {"type": "text", "text": "transcribe:"},
        {"type": "input_audio", "input_audio": {"data": wav, "format": "wav"}},
    ]


async def test_unknown_part_fails_structurally_before_any_llm_call(
    open_test_run: OpenTestRun,
) -> None:
    # A part the loop cannot translate must never be silently sent as some
    # other modality; the run fails as a validation error before the
    # provider is contacted, so nothing is spent.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message=cast("list[ContentPart]", [TextPart(text="hi"), object()]),
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.output is None
    assert outcome.error is not None
    assert outcome.error.kind == "validation"
    assert not outcome.error.retryable
    assert "object" in outcome.error.detail
    assert fake.calls == []


async def test_empty_parts_list_fails_structurally_before_any_llm_call(
    open_test_run: OpenTestRun,
) -> None:
    # Providers reject an empty content array with an opaque 400; the loop
    # refuses it pre-send with the same clean validation failure an unknown
    # part gets, so a conditionally-built list that filtered to nothing is
    # named as the authoring error it is.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message=[],
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.output is None
    assert outcome.error is not None
    assert outcome.error.kind == "validation"
    assert "empty parts list" in outcome.error.detail
    assert fake.calls == []


# --- Partial child results keep their output -------------------------------


class _PartialIn(BaseModel):
    query: str = Field(description="Probe input.")


class _PartialOut(BaseModel):
    text: str = Field(description="Probe output.")


class _PartialTool(Commission[_PartialIn, _PartialOut]):
    """Returns a partial result with usable output, like an overflowed child."""

    name: ClassVar[str] = "partial_probe"
    description: ClassVar[str] = "Test probe returning a partial result."
    input_type: ClassVar[type] = _PartialIn
    output_type: ClassVar[type] = _PartialOut

    async def _run(
        self,
        input: _PartialIn,
        ctx: CallContext,
    ) -> CommissionResult[_PartialOut]:
        return CommissionResult[_PartialOut](
            status="partial",
            output=_PartialOut(text="the usable half"),
            error=ErrorState(
                kind="output_too_large",
                detail="Output exceeded cap; returned as partial.",
                retryable=False,
            ),
            provenance=Provenance(
                source="partial_probe:test",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        )


async def test_partial_child_result_renders_output_and_error(
    open_test_run: OpenTestRun,
) -> None:
    # A partial child's output must reach the calling LLM; partial is the
    # policy that *preserves* usable output, so rendering only the error
    # would waste the child's spend.
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "partial_probe", {"query": "q"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(_PartialTool(),),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.output is not None
    second_call_messages = fake.calls[1]["messages"]
    tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
    rendered = json.loads(tool_msg["content"])
    assert rendered["partial_output"] == {"text": "the usable half"}
    assert rendered["error"]["kind"] == "output_too_large"


class _FailingTool(Commission[_PartialIn, _PartialOut]):
    """Fails with an oversized detail, like a provider error with a long body."""

    name: ClassVar[str] = "failing_probe"
    description: ClassVar[str] = "Test probe returning a failure with a huge detail."
    input_type: ClassVar[type] = _PartialIn
    output_type: ClassVar[type] = _PartialOut

    async def _run(
        self,
        input: _PartialIn,
        ctx: CallContext,
    ) -> CommissionResult[_PartialOut]:
        return CommissionResult[_PartialOut](
            status="failure",
            error=ErrorState(
                kind="internal",
                detail="x" * 10_000,
                retryable=False,
            ),
            provenance=Provenance(
                source="failing_probe:test",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        )


async def test_failed_child_detail_is_bounded_in_the_transcript(
    open_test_run: OpenTestRun,
) -> None:
    # The envelope keeps the full detail (errors are values), but the
    # rendered tool result is parent-context footprint: a failure envelope
    # bypasses the output cap, so the render seam bounds the detail itself,
    # with a visible truncation marker rather than a silent cut.
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "failing_probe", {"query": "q"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(_FailingTool(),),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.output is not None
    second_call_messages = fake.calls[1]["messages"]
    tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
    rendered = json.loads(tool_msg["content"])
    detail = rendered["error"]["detail"]
    assert len(detail) < 10_000
    assert detail.endswith("[truncated]")


async def test_short_child_detail_renders_untouched(
    open_test_run: OpenTestRun,
) -> None:
    # The bound only fires on oversized details; the everyday short detail
    # reaches the model verbatim, marker-free.
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "partial_probe", {"query": "q"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(models=_models(fake)) as ctx:
        await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(_PartialTool(),),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    second_call_messages = fake.calls[1]["messages"]
    tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
    rendered = json.loads(tool_msg["content"])
    assert rendered["error"]["detail"] == "Output exceeded cap; returned as partial."


async def test_empty_system_prompt_sends_no_system_message(
    open_test_run: OpenTestRun,
) -> None:
    # A promptless Commission (system_prompt None arrives here as "") must
    # not send an empty system message; some providers reject it.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    async with open_test_run(models=_models(fake)) as ctx:
        await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="",
            user_message="go",
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    roles = [m["role"] for m in fake.calls[0]["messages"]]
    assert "system" not in roles
    assert roles[0] == "user"


# --- Children receive the remaining budget, never the full grant ------------


class _BudgetProbeIn(BaseModel):
    query: str = Field(description="Probe input.")


class _BudgetProbeOut(BaseModel):
    text: str = Field(description="Probe output.")


class _BudgetProbe(Commission[_BudgetProbeIn, _BudgetProbeOut]):
    """Records the budget each dispatch hands it and reports a fixed cost."""

    name: ClassVar[str] = "budget_probe"
    description: ClassVar[str] = "Test probe recording the budget it is given."
    input_type: ClassVar[type] = _BudgetProbeIn
    output_type: ClassVar[type] = _BudgetProbeOut

    def __init__(self, *, cost_usd: float) -> None:
        super().__init__()
        self.seen_budgets: list[float | None] = []
        self._cost_usd = cost_usd

    async def _run(
        self,
        input: _BudgetProbeIn,
        ctx: CallContext,
    ) -> CommissionResult[_BudgetProbeOut]:
        self.seen_budgets.append(ctx.budget_usd)
        return CommissionResult[_BudgetProbeOut](
            status="success",
            output=_BudgetProbeOut(text="ok"),
            provenance=Provenance(
                source="budget_probe:test",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=self._cost_usd),
        )


def _close(value: float | None, expected: float) -> bool:
    """Float-tolerant equality for a recorded budget; None never matches."""
    return value is not None and math.isclose(value, expected, abs_tol=1e-9)


async def test_children_are_dispatched_with_the_remaining_budget(
    open_test_run: OpenTestRun,
) -> None:
    # Two children in one turn: the second's ceiling must reflect the first's
    # spend plus the loop's own turn cost, not a full copy of the grant.
    probe = _BudgetProbe(cost_usd=0.25)
    fake = ScriptedLLM(
        [
            llm_response(
                tool_calls=[
                    ("t1", "budget_probe", {"query": "a"}),
                    ("t2", "budget_probe", {"query": "b"}),
                ],
                in_tokens=100,
                out_tokens=50,
            ),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(budget_usd=1.0, models=_models(fake, (10.0, 20.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(probe,),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is None
    # Own turn cost: (100 * 10 + 50 * 20) / 1e6 = 0.002.
    assert len(probe.seen_budgets) == 2
    assert _close(probe.seen_budgets[0], 0.998)
    assert _close(probe.seen_budgets[1], 0.748)


async def test_exhausted_grant_starves_later_children_at_zero(
    open_test_run: OpenTestRun,
) -> None:
    # The first child overspends the grant; the second's ceiling clamps at
    # 0.0 rather than going negative, and the next turn's pre-flight gate
    # then ends the run as budget_exceeded.
    probe = _BudgetProbe(cost_usd=0.25)
    fake = ScriptedLLM(
        [
            llm_response(
                tool_calls=[
                    ("t1", "budget_probe", {"query": "a"}),
                    ("t2", "budget_probe", {"query": "b"}),
                ],
                in_tokens=100,
                out_tokens=50,
            ),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(budget_usd=0.2, models=_models(fake, (10.0, 20.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(probe,),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert len(probe.seen_budgets) == 2
    assert _close(probe.seen_budgets[0], 0.198)
    assert probe.seen_budgets[1] == 0.0
    assert outcome.error is not None
    assert outcome.error.kind == "budget_exceeded"


async def test_no_budget_passes_none_through_to_children(
    open_test_run: OpenTestRun,
) -> None:
    # No grant means nothing to allocate: children see None, not 0.0.
    probe = _BudgetProbe(cost_usd=0.25)
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "budget_probe", {"query": "a"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(probe,),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is None
    assert probe.seen_budgets == [None]


# --- Budget status line: mid-run cost visibility for the LLM ----------------


def _budget_lines(messages: list[dict[str, Any]]) -> list[str]:
    """The `[budget]` status lines in a recorded call's message list."""
    return [
        str(m["content"])
        for m in messages
        if m["role"] == "user" and str(m["content"]).startswith("[budget]")
    ]


async def test_budget_status_line_follows_the_turns_tool_results(
    open_test_run: OpenTestRun,
) -> None:
    # A budgeted loop shows its LLM the spend after each turn's tools: the
    # same own-turns-plus-children ledger the hard stop checks.
    probe = _BudgetProbe(cost_usd=0.25)
    fake = ScriptedLLM(
        [
            llm_response(
                tool_calls=[("t1", "budget_probe", {"query": "a"})],
                in_tokens=100,
                out_tokens=50,
            ),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(budget_usd=1.0, models=_models(fake, (10.0, 20.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(probe,),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is None
    # Own turn cost 0.002 + one probe child 0.25 = 0.252 spent of the 1.0 grant.
    lines = _budget_lines(fake.calls[1]["messages"])
    assert lines == ["[budget] spent $0.2520 of $1.0000 grant; $0.7480 remaining."]


async def test_overspent_grant_reports_zero_remaining_not_negative(
    open_test_run: OpenTestRun,
) -> None:
    # Children dispatched since the last check can overspend the grant; the
    # status line clamps remaining at zero and the next turn's hard stop
    # handles the overrun.
    probe = _BudgetProbe(cost_usd=0.25)
    fake = ScriptedLLM(
        [
            llm_response(
                tool_calls=[
                    ("t1", "budget_probe", {"query": "a"}),
                    ("t2", "budget_probe", {"query": "b"}),
                ],
                in_tokens=100,
                out_tokens=50,
            ),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(budget_usd=0.2, models=_models(fake, (10.0, 20.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(probe,),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is not None and outcome.error.kind == "budget_exceeded"
    # The overspend means the pre-turn gate declines a second provider call,
    # so only one call is recorded; the fake holds a live reference to the
    # loop's message list, which by then carries the clamped status line.
    assert len(fake.calls) == 1
    lines = _budget_lines(fake.calls[0]["messages"])
    assert lines == ["[budget] spent $0.5020 of $0.2000 grant; $0.0000 remaining."]


async def test_unbudgeted_loop_emits_no_budget_line(open_test_run: OpenTestRun) -> None:
    # No budget, no status line: today's unbudgeted transcripts are unchanged.
    probe = _BudgetProbe(cost_usd=0.25)
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "budget_probe", {"query": "a"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(models=_models(fake, (10.0, 20.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(probe,),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is None
    assert _budget_lines(fake.calls[1]["messages"]) == []


# --- Pre-turn gate: decline a call whose input floor breaks the grant -------


async def test_preflight_gate_declines_before_the_first_call(
    open_test_run: OpenTestRun,
) -> None:
    # The opening message alone prices above the grant: the loop must fail
    # before any provider call, with zero spend.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    async with open_test_run(budget_usd=0.005, models=_models(fake, (10.0, 20.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            # 4000 chars -> ~1000 tokens -> $0.01 input floor at $10/M.
            user_message="x" * 4000,
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert fake.calls == []
    assert outcome.error is not None
    assert outcome.error.kind == "budget_exceeded"
    assert "pre-flight" in outcome.error.detail
    assert outcome.in_tokens == 0 and outcome.out_tokens == 0
    assert outcome.children_cost == 0.0


async def test_preflight_gate_declines_a_later_turn_after_spend_accumulates(
    open_test_run: OpenTestRun,
) -> None:
    # Turn one passes the gate and spends most of the grant (own turn plus a
    # child); the second turn's input floor then projects past the grant and
    # is declined without another provider call.
    probe = _BudgetProbe(cost_usd=0.001)
    fake = ScriptedLLM(
        [
            llm_response(
                tool_calls=[("t1", "budget_probe", {"query": "a"})],
                in_tokens=100,
                out_tokens=50,
            ),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(budget_usd=0.0035, models=_models(fake, (10.0, 20.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            # 400 chars -> ~100 tokens -> $0.001 input floor: under the grant, so
            # turn one proceeds. After it, spend is 0.002 (own) + 0.001 (child)
            # = 0.003, and the grown transcript's floor pushes past 0.0035.
            user_message="x" * 400,
            toolbox=(probe,),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert len(fake.calls) == 1
    assert outcome.error is not None
    assert outcome.error.kind == "budget_exceeded"
    assert "pre-flight" in outcome.error.detail
    assert outcome.in_tokens == 100 and outcome.out_tokens == 50
    assert math.isclose(outcome.children_cost, 0.001, abs_tol=1e-12)


async def test_unbudgeted_loop_never_gates_pre_flight(open_test_run: OpenTestRun) -> None:
    # No budget means no estimation and no gate, however large the message.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    async with open_test_run(models=_models(fake, (10.0, 20.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="x" * 400_000,
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is None
    assert len(fake.calls) == 1


# --- Free-text replies: nudge once, fail on repeat --------------------------


async def test_free_text_reply_gets_one_corrective_nudge(
    open_test_run: OpenTestRun,
) -> None:
    fake = ScriptedLLM(
        [
            llm_response(content="I think the answer is x."),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})]),
        ]
    )
    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is None
    assert outcome.output is not None and outcome.output.answer == "x"
    # The corrective user message was appended after the free-text reply.
    # (The fake records a live reference to the loop's message list, so
    # assert on presence, not position.)
    second_call_messages = fake.calls[1]["messages"]
    nudges = [
        m
        for m in second_call_messages
        if m["role"] == "user" and "Respond only with tool calls" in str(m["content"])
    ]
    assert len(nudges) == 1


async def test_second_free_text_reply_fails_the_loop(open_test_run: OpenTestRun) -> None:
    fake = ScriptedLLM(
        [
            llm_response(content="prose"),
            llm_response(content="more prose"),
        ]
    )
    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.output is None
    assert outcome.error is not None
    assert outcome.error.kind == "internal"
    assert "twice" in outcome.error.detail


async def test_invalid_tool_arguments_feed_back_without_dispatch(
    open_test_run: OpenTestRun,
) -> None:
    # The loop's promise: a malformed tool call is a nudgeable slip, fed
    # back to the LLM as a validation tool result, never raised and never
    # dispatched. Turn one's arguments are not JSON at all; turn two's
    # parse but miss the required field; turn three concludes normally.
    probe = _BudgetProbe(cost_usd=0.0)
    unparseable = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="t1",
                            type="function",
                            function=SimpleNamespace(name="budget_probe", arguments="{not json"),
                        )
                    ],
                )
            )
        ],
    )
    fake = ScriptedLLM(
        [
            unparseable,
            llm_response(tool_calls=[("t2", "budget_probe", {"wrong_field": "no query"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(probe,),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is None
    assert outcome.output is not None
    # Neither bad call reached the tool.
    assert probe.seen_budgets == []
    # Both slips came back as validation tool results the LLM can act on.
    tool_msgs = [m for m in fake.calls[2]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    for msg in tool_msgs:
        rendered = json.loads(str(msg["content"]))
        assert rendered["error"]["kind"] == "validation"
        assert "Invalid input" in rendered["error"]["detail"]


class _RaisingLLM:
    """A provider double whose create always raises: the classification seam."""

    def __init__(self, exc: Exception) -> None:
        self.calls: list[dict[str, Any]] = []
        self._exc = exc

        async def create(**kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            raise self._exc

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _provider_request() -> httpx.Request:
    return httpx.Request("POST", "https://fixture.invalid/v1/chat/completions")


@pytest.mark.parametrize(
    ("make_exc", "expected_kind", "expected_retryable", "needle"),
    [
        pytest.param(
            lambda: openai.RateLimitError(
                "slow down",
                response=httpx.Response(429, request=_provider_request()),
                body=None,
            ),
            "rate_limit",
            True,
            "Rate limit",
            id="rate-limit",
        ),
        pytest.param(
            lambda: openai.APIStatusError(
                "bad request",
                response=httpx.Response(400, request=_provider_request()),
                body=None,
            ),
            "internal",
            False,
            "LLM provider error",
            id="deterministic-4xx",
        ),
        pytest.param(
            lambda: openai.APIStatusError(
                "upstream unavailable",
                response=httpx.Response(503, request=_provider_request()),
                body=None,
            ),
            "internal",
            True,
            "LLM provider error",
            id="transient-5xx",
        ),
        pytest.param(
            lambda: openai.APIConnectionError(request=_provider_request()),
            "internal",
            True,
            "LLM provider error",
            id="connection-trouble",
        ),
    ],
)
async def test_provider_errors_classify_kind_and_retryability(
    open_test_run: OpenTestRun,
    make_exc: "Callable[[], Exception]",
    expected_kind: str,
    expected_retryable: bool,
    needle: str,
) -> None:
    # The except ladder's order is load-bearing (RateLimitError is an
    # APIStatusError is an APIError): the rate-limit case fails here if a
    # broader arm moves above a narrower one. Retryability is the contract:
    # 429, 5xx, and connection trouble may clear; a plain 4xx is
    # deterministic and must not invite a retry.
    raising = _RaisingLLM(make_exc())
    async with open_test_run(models=_models(cast("ScriptedLLM", raising))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert len(raising.calls) == 1
    assert outcome.output is None
    assert outcome.error is not None
    assert outcome.error.kind == expected_kind
    assert outcome.error.retryable is expected_retryable
    assert needle in outcome.error.detail
    # The raising call recorded no usage; nothing is invented.
    assert outcome.in_tokens == 0 and outcome.out_tokens == 0


async def test_empty_provider_choices_fail_as_loop_error(open_test_run: OpenTestRun) -> None:
    fake = ScriptedLLM(
        [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
                choices=[],
            )
        ]
    )

    async with open_test_run(models=_models(fake)) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="sys",
            user_message="go",
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )

    assert outcome.output is None
    assert outcome.error is not None
    assert outcome.error.kind == "internal"
    assert "no choices" in outcome.error.detail
    assert outcome.in_tokens == 12
    assert outcome.out_tokens == 3


# --- Multimodal posture: gates count text only; the envelope is unchanged ---


async def test_pre_turn_budget_floor_counts_text_parts_only(
    open_test_run: OpenTestRun,
) -> None:
    # $1 per input token. The text floor is 2 tokens ($2), under the $10
    # grant; a counted image or audio part would put the floor near $200,000
    # and decline the turn. Proceeding proves the documented undercount
    # posture: non-text parts contribute zero to the pre-turn floor.
    fake = ScriptedLLM(
        [llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})], in_tokens=1, out_tokens=0)]
    )
    async with open_test_run(budget_usd=10.0, models=_models(fake, (1_000_000.0, 0.0))) as ctx:
        outcome = await run_llm_loop(
            model=None,
            commission_name="probe",
            system_prompt="",
            user_message=[
                TextPart(text="abcdefgh"),
                ImagePart(image_url="d" * 400_000),
                AudioPart(data="A" * 400_000, format="wav"),
            ],
            toolbox=(),
            output_type=_Out,
            ctx=ctx,
            max_iterations=3,
        )
    assert outcome.error is None
    assert len(fake.calls) == 1


class _PartsProbe(Commission[_PartialIn, _Out]):
    """Default-loop probe whose opening message is a multimodal parts list."""

    name: ClassVar[str] = "parts_probe"
    description: ClassVar[str] = "Test Commission with a parts-list opening message."
    input_type: ClassVar[type] = _PartialIn
    output_type: ClassVar[type] = _Out
    parts: ClassVar[list[ContentPart]]

    def build_user_message(self, input: _PartialIn, ctx: CallContext) -> list[ContentPart]:
        return self.parts


class _DescribeProbe(_PartsProbe):
    parts: ClassVar[list[ContentPart]] = [
        TextPart(text="describe:"),
        ImagePart(image_url="data:image/png;base64,AAAA"),
    ]


class _BadPartProbe(_PartsProbe):
    system_prompt = "sys"
    parts: ClassVar[list[ContentPart]] = cast("list[ContentPart]", [TextPart(text="hi"), object()])


class _HeavyImageProbe(_PartsProbe):
    # 10 text tokens beside an image that would estimate at 25,000.
    parts: ClassVar[list[ContentPart]] = [
        TextPart(text="x" * 40),
        ImagePart(image_url="d" * 100_000),
    ]


class _HeavyTextProbe(_PartsProbe):
    # 50 text tokens: over the same gate the heavy image passes under.
    parts: ClassVar[list[ContentPart]] = [
        TextPart(text="x" * 200),
        ImagePart(image_url="data:image/png;base64,AAAA"),
    ]


async def test_size_gate_measures_text_only_in_a_parts_list() -> None:
    # Gate ceiling: 40 tokens * 0.75 = 30. The heavy image alone would
    # estimate at 25,000 tokens; the run reaching the LLM proves the gate
    # measured only the 10 tokens of text.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    commission = _HeavyImageProbe(max_input_tokens=40)
    result = await run_commission(commission, _PartialIn(query="q"), models=[scripted_model(fake)])
    assert result.status == "success"
    assert len(fake.calls) == 1


async def test_size_gate_still_rejects_oversized_text_in_a_parts_list() -> None:
    # The control for the test above: the same gate, breached by text alone,
    # still fails the run before the provider is contacted.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    commission = _HeavyTextProbe(max_input_tokens=40)
    result = await run_commission(commission, _PartialIn(query="q"), models=[scripted_model(fake)])
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert fake.calls == []


async def test_invalid_opening_message_failure_still_deposits_trace(tmp_path: Path) -> None:
    # The trace mailbox's promise is every exit path, this new pre-send one
    # included: a recorded run that failed on an untranslatable part still
    # persists what existed at the point of failure (the system message).
    backend = FilesystemBackend(tmp_path)
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    commission = _BadPartProbe()
    result = await run_commission(
        commission,
        _PartialIn(query="q"),
        models=[scripted_model(fake)],
        backend=backend,
        record="always",
    )
    assert result.status == "failure"
    assert result.error is not None and result.error.kind == "validation"
    assert result.run_id is not None
    record = await backend.load(result.run_id)
    assert record is not None
    assert record.llm_trace == [{"role": "system", "content": "sys"}]


async def test_parts_list_run_completes_and_deposits_transcript(tmp_path: Path) -> None:
    # The whole envelope over a multimodal opening message: the default loop
    # runs, concludes normally, and the persisted trace carries the
    # translated parts exactly as the provider received them.
    backend = FilesystemBackend(tmp_path)
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "seen"})])])
    commission = _DescribeProbe()
    result = await run_commission(
        commission,
        _PartialIn(query="what is this?"),
        models=[scripted_model(fake)],
        backend=backend,
        record="always",
    )
    assert result.status == "success"
    assert result.output is not None
    assert result.run_id is not None
    record = await backend.load(result.run_id)
    assert record is not None and record.llm_trace is not None
    user_msg = next(m for m in record.llm_trace if m["role"] == "user")
    assert user_msg["content"] == [
        {"type": "text", "text": "describe:"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
