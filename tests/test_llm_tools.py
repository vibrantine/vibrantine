"""Tests for the LLM-loop helpers.

Covers how `run_llm_loop` translates a Commission's opening message
(`str | list[ContentPart]`) into the content the provider actually receives,
how child results are rendered back to the LLM (partial keeps its output),
and the free-text nudge. A fake AsyncOpenAI-shaped client records each call
so the messages it was sent can be inspected.
"""

import json
import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    CostMetrics,
    ErrorState,
    ImagePart,
    Provenance,
    TextPart,
)
from vibrantine.llm_tools import run_llm_loop
from vibrantine.testing import FIXTURE_MODEL, ScriptedLLM, llm_response


class _Out(BaseModel):
    answer: str


async def _run(user_message: str | list[Any]) -> dict[str, Any]:
    """Run one loop with the given opening message; return the user message sent."""
    fake = ScriptedLLM(
        [llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})], in_tokens=10, out_tokens=5)]
    )
    await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message=user_message,
        toolbox=(),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
    )
    messages = fake.calls[0]["messages"]
    return next(m for m in messages if m["role"] == "user")


async def test_bare_string_is_sent_as_plain_content() -> None:
    # A bare str is the single-text case; it rides through unchanged.
    user_msg = await _run("just text")
    assert user_msg["content"] == "just text"


async def test_text_parts_translate_to_text_content() -> None:
    user_msg = await _run([TextPart(text="hello"), TextPart(text="world")])
    assert user_msg["content"] == [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "world"},
    ]


async def test_mixed_parts_preserve_order_and_image_shape() -> None:
    user_msg = await _run(
        [
            TextPart(text="describe this:"),
            ImagePart(image_url="data:image/png;base64,AAAA"),
        ]
    )
    assert user_msg["content"] == [
        {"type": "text", "text": "describe this:"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]


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


async def test_partial_child_result_renders_output_and_error() -> None:
    # A partial child's output must reach the calling LLM; partial is the
    # policy that *preserves* usable output, so rendering only the error
    # would waste the child's spend.
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "partial_probe", {"query": "q"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(_PartialTool(),),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
    )
    assert outcome.output is not None
    second_call_messages = fake.calls[1]["messages"]
    tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
    rendered = json.loads(tool_msg["content"])
    assert rendered["partial_output"] == {"text": "the usable half"}
    assert rendered["error"]["kind"] == "output_too_large"


async def test_empty_system_prompt_sends_no_system_message() -> None:
    # A promptless Commission (system_prompt None arrives here as "") must
    # not send an empty system message; some providers reject it.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="",
        user_message="go",
        toolbox=(),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
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


async def test_children_are_dispatched_with_the_remaining_budget() -> None:
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
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(probe,),
        output_type=_Out,
        ctx=CallContext(budget_usd=1.0),
        max_iterations=3,
        prices_per_million=(10.0, 20.0),
    )
    assert outcome.error is None
    # Own turn cost: (100 * 10 + 50 * 20) / 1e6 = 0.002.
    assert len(probe.seen_budgets) == 2
    assert _close(probe.seen_budgets[0], 0.998)
    assert _close(probe.seen_budgets[1], 0.748)


async def test_exhausted_grant_starves_later_children_at_zero() -> None:
    # The first child overspends the grant; the second's ceiling clamps at
    # 0.0 rather than going negative, and the loop's own post-turn check
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
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(probe,),
        output_type=_Out,
        ctx=CallContext(budget_usd=0.2),
        max_iterations=3,
        prices_per_million=(10.0, 20.0),
    )
    assert len(probe.seen_budgets) == 2
    assert _close(probe.seen_budgets[0], 0.198)
    assert probe.seen_budgets[1] == 0.0
    assert outcome.error is not None
    assert outcome.error.kind == "budget_exceeded"


async def test_no_budget_passes_none_through_to_children() -> None:
    # No grant means nothing to allocate: children see None, not 0.0.
    probe = _BudgetProbe(cost_usd=0.25)
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "budget_probe", {"query": "a"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(probe,),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
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


async def test_budget_status_line_follows_the_turns_tool_results() -> None:
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
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(probe,),
        output_type=_Out,
        ctx=CallContext(budget_usd=1.0),
        max_iterations=3,
        prices_per_million=(10.0, 20.0),
    )
    assert outcome.error is None
    # Own turn cost 0.002 + one probe child 0.25 = 0.252 spent of the 1.0 grant.
    lines = _budget_lines(fake.calls[1]["messages"])
    assert lines == ["[budget] spent $0.2520 of $1.0000 grant; $0.7480 remaining."]


async def test_overspent_grant_reports_zero_remaining_not_negative() -> None:
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
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(probe,),
        output_type=_Out,
        ctx=CallContext(budget_usd=0.2),
        max_iterations=3,
        prices_per_million=(10.0, 20.0),
    )
    assert outcome.error is not None and outcome.error.kind == "budget_exceeded"
    # The overspend means the pre-turn gate declines a second provider call,
    # so only one call is recorded; the fake holds a live reference to the
    # loop's message list, which by then carries the clamped status line.
    assert len(fake.calls) == 1
    lines = _budget_lines(fake.calls[0]["messages"])
    assert lines == ["[budget] spent $0.5020 of $0.2000 grant; $0.0000 remaining."]


async def test_unbudgeted_loop_emits_no_budget_line() -> None:
    # No budget, no status line: today's unbudgeted transcripts are unchanged.
    probe = _BudgetProbe(cost_usd=0.25)
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("t1", "budget_probe", {"query": "a"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(probe,),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(10.0, 20.0),
    )
    assert outcome.error is None
    assert _budget_lines(fake.calls[1]["messages"]) == []


# --- Pre-turn gate: decline a call whose input floor breaks the grant -------


async def test_preflight_gate_declines_before_the_first_call() -> None:
    # The opening message alone prices above the grant: the loop must fail
    # before any provider call, with zero spend.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        # 4000 chars -> ~1000 tokens -> $0.01 input floor at $10/M.
        user_message="x" * 4000,
        toolbox=(),
        output_type=_Out,
        ctx=CallContext(budget_usd=0.005),
        max_iterations=3,
        prices_per_million=(10.0, 20.0),
    )
    assert fake.calls == []
    assert outcome.error is not None
    assert outcome.error.kind == "budget_exceeded"
    assert "pre-flight" in outcome.error.detail
    assert outcome.in_tokens == 0 and outcome.out_tokens == 0
    assert outcome.children_cost == 0.0


async def test_preflight_gate_declines_a_later_turn_after_spend_accumulates() -> None:
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
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        # 400 chars -> ~100 tokens -> $0.001 input floor: under the grant, so
        # turn one proceeds. After it, spend is 0.002 (own) + 0.001 (child)
        # = 0.003, and the grown transcript's floor pushes past 0.0035.
        user_message="x" * 400,
        toolbox=(probe,),
        output_type=_Out,
        ctx=CallContext(budget_usd=0.0035),
        max_iterations=3,
        prices_per_million=(10.0, 20.0),
    )
    assert len(fake.calls) == 1
    assert outcome.error is not None
    assert outcome.error.kind == "budget_exceeded"
    assert "pre-flight" in outcome.error.detail
    assert outcome.in_tokens == 100 and outcome.out_tokens == 50
    assert math.isclose(outcome.children_cost, 0.001, abs_tol=1e-12)


async def test_unbudgeted_loop_never_gates_pre_flight() -> None:
    # No budget means no estimation and no gate, however large the message.
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})])])
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="x" * 400_000,
        toolbox=(),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(10.0, 20.0),
    )
    assert outcome.error is None
    assert len(fake.calls) == 1


# --- Free-text replies: nudge once, fail on repeat --------------------------


async def test_free_text_reply_gets_one_corrective_nudge() -> None:
    fake = ScriptedLLM(
        [
            llm_response(content="I think the answer is x."),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})]),
        ]
    )
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
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


async def test_second_free_text_reply_fails_the_loop() -> None:
    fake = ScriptedLLM(
        [
            llm_response(content="prose"),
            llm_response(content="more prose"),
        ]
    )
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
    )
    assert outcome.output is None
    assert outcome.error is not None
    assert outcome.error.kind == "internal"
    assert "twice" in outcome.error.detail


async def test_empty_provider_choices_fail_as_loop_error() -> None:
    fake = ScriptedLLM(
        [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
                choices=[],
            )
        ]
    )

    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model=FIXTURE_MODEL.id,
        system_prompt="sys",
        user_message="go",
        toolbox=(),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
    )

    assert outcome.output is None
    assert outcome.error is not None
    assert outcome.error.kind == "internal"
    assert "no choices" in outcome.error.detail
    assert outcome.in_tokens == 12
    assert outcome.out_tokens == 3
