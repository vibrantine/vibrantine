"""Tests for SummarizeCommission.

A basic LLM-loop Commission with an *empty* toolbox: the only tool the loop
offers is the framework-injected `conclude`, so the happy path is a single
LLM turn that calls conclude with the typed summary. Tests register a
ScriptedLLM fake as the run's model catalog entry (`scripted_model`).
"""

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from vibrantine import CallContext, ProgressEvent, run_commission
from vibrantine.examples.summarize import (
    SummarizeCommission,
    SummarizeInput,
)
from vibrantine.testing import AlwaysCancelled, ScriptedLLM, llm_response, scripted_model

_SOURCE = (
    "The cat sat on the mat. It was a warm afternoon and the cat was content. "
    "Later, the cat moved to the windowsill to watch the birds."
)


def _commission(
    responses: list[SimpleNamespace],
    *,
    max_iterations: int = 10,
) -> tuple[SummarizeCommission, ScriptedLLM]:
    fake = ScriptedLLM(responses)
    commission = SummarizeCommission(max_iterations=max_iterations)
    return commission, fake


async def test_summarize_happy_path_concludes_in_one_turn() -> None:
    commission, fake = _commission(
        [llm_response(tool_calls=[("c1", "conclude", {"summary": "A content cat."})])]
    )

    result = await run_commission(
        commission,
        SummarizeInput(content=_SOURCE, length="one_sentence"),
        models=[scripted_model(fake)],
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.summary == "A content cat."
    assert len(fake.calls) == 1


def test_summarize_default_length_is_short() -> None:
    # The contract default: a caller may pass only `content`.
    input = SummarizeInput(content=_SOURCE)
    assert input.length == "short"
    assert input.focus is None


def test_summarize_rejects_empty_content() -> None:
    # Summarizing nothing is a caller bug: the input type refuses it at
    # construction, before anything runs or spends.
    with pytest.raises(ValidationError):
        SummarizeInput(content="")


def test_summarize_user_message_carries_length_and_content() -> None:
    commission, _fake = _commission([])
    message = commission.build_user_message(
        SummarizeInput(content=_SOURCE, length="medium"),
        CallContext(),
    )

    assert "Target length: medium" in message
    assert _SOURCE in message
    assert "Focus:" not in message  # no focus given → no focus line


def test_summarize_user_message_includes_focus_when_given() -> None:
    commission, _fake = _commission([])
    message = commission.build_user_message(
        SummarizeInput(content=_SOURCE, focus="the birds"),
        CallContext(),
    )

    assert "Focus: the birds" in message


def _tool_names(call_kwargs: dict[str, Any]) -> set[str]:
    """Names the LLM was offered on a given chat.completions call."""
    return {t["function"]["name"] for t in call_kwargs["tools"]}


async def test_summarize_empty_toolbox_offers_only_conclude() -> None:
    # Pure-judgment Commission: nothing to fetch, so the only tool on the
    # menu is the framework-injected conclude.
    commission, fake = _commission(
        [llm_response(tool_calls=[("c1", "conclude", {"summary": "x"})])]
    )

    await run_commission(commission, SummarizeInput(content=_SOURCE), models=[scripted_model(fake)])

    assert _tool_names(fake.calls[0]) == {"conclude"}


async def test_summarize_budget_exceeded_after_first_llm_call() -> None:
    # First call uses 10000 in + 1000 out tokens.
    # Cost = (10000*0.50 + 1000*3.00) / 1M = $0.008. Budget $0.001 → the
    # spend fuse trips at settle and the root reports run_halted.
    commission, fake = _commission(
        [
            llm_response(
                tool_calls=[("c1", "conclude", {"summary": "x"})],
                in_tokens=10000,
                out_tokens=1000,
            )
        ]
    )

    result = await run_commission(
        commission,
        SummarizeInput(content=_SOURCE),
        models=[scripted_model(fake)],
        budget_usd=0.001,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "spend fuse tripped" in result.error.detail
    assert len(fake.calls) == 1
    assert result.cost.estimated_usd > 0.001


async def test_summarize_cancellation_at_entry_makes_no_llm_call() -> None:
    commission, fake = _commission([llm_response(tool_calls=None)])

    result = await run_commission(
        commission,
        SummarizeInput(content=_SOURCE),
        models=[scripted_model(fake)],
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert len(fake.calls) == 0


async def test_summarize_free_text_is_nudged_then_fails_on_second_slip() -> None:
    # The loop disallows free-form completion: a first prose reply earns a
    # corrective nudge, a second one fails the run.
    commission, fake = _commission(
        [
            llm_response(tool_calls=None, content="Here's a summary: a cat."),
            llm_response(tool_calls=None, content="Still just prose."),
        ]
    )

    result = await run_commission(
        commission, SummarizeInput(content=_SOURCE), models=[scripted_model(fake)]
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "no tool call" in result.error.detail.lower()
    assert len(fake.calls) == 2


async def test_summarize_emits_loop_start_progress_event() -> None:
    events: list[ProgressEvent] = []
    commission, fake = _commission(
        [llm_response(tool_calls=[("c1", "conclude", {"summary": "x"})])]
    )

    await run_commission(
        commission,
        SummarizeInput(content=_SOURCE),
        models=[scripted_model(fake)],
        on_progress=events.append,
    )

    assert any(e.phase == "loop_start" and e.commission_name == "summarize" for e in events)


async def test_summarize_invalid_conclude_args_are_fed_back_then_recover() -> None:
    # First conclude omits the required `summary` field; the loop feeds the
    # validation error back and the LLM concludes correctly on retry.
    commission, fake = _commission(
        [
            llm_response(tool_calls=[("c1", "conclude", {"wrong_field": "oops"})]),
            llm_response(tool_calls=[("c2", "conclude", {"summary": "A content cat."})]),
        ]
    )

    result = await run_commission(
        commission, SummarizeInput(content=_SOURCE), models=[scripted_model(fake)]
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.summary == "A content cat."
    # The second call must have carried the validation error as a tool message.
    second_messages = fake.calls[1]["messages"]
    tool_msg = next(m for m in second_messages if m["role"] == "tool")
    assert "validate" in tool_msg["content"]
