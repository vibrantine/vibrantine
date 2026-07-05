"""Tests for SummariseCommission.

A basic LLM-loop Commission with an *empty* toolbox: the only tool the loop
offers is the framework-injected `conclude`, so the happy path is a single
LLM turn that calls conclude with the typed summary. Tests inject a fake
AsyncOpenAI-shaped client that returns scripted responses.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import AlwaysCancelled, FakeClient, llm_response
from openai import AsyncOpenAI
from pydantic import ValidationError

from vibrantine.commissions.summarise import (
    SummariseCommission,
    SummariseInput,
)
from vibrantine.contract import CallContext, ProgressEvent

_SOURCE = (
    "The cat sat on the mat. It was a warm afternoon and the cat was content. "
    "Later, the cat moved to the windowsill to watch the birds."
)


def _commission(
    responses: list[SimpleNamespace],
    *,
    max_iterations: int = 10,
    model: str = "google/gemini-3-flash-preview",  # stable fixture for pricing math
) -> tuple[SummariseCommission, FakeClient]:
    fake = FakeClient(responses)
    commission = SummariseCommission(
        client=cast(AsyncOpenAI, fake),
        max_iterations=max_iterations,
        model=model,
    )
    return commission, fake


async def test_summarise_happy_path_concludes_in_one_turn() -> None:
    commission, fake = _commission(
        [llm_response(tool_calls=[("c1", "conclude", {"summary": "A content cat."})])]
    )

    result = await commission.invoke(
        SummariseInput(content=_SOURCE, length="one_sentence"),
        CallContext(),
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.summary == "A content cat."
    assert len(fake.completions.calls) == 1


def test_summarise_default_length_is_short() -> None:
    # The contract default: a caller may pass only `content`.
    input = SummariseInput(content=_SOURCE)
    assert input.length == "short"
    assert input.focus is None


def test_summarise_rejects_empty_content() -> None:
    # Summarising nothing is a caller bug: guarded at construction, like
    # Synthesize's empty `sources` and Classify's `min_length` on labels.
    with pytest.raises(ValidationError):
        SummariseInput(content="")


def test_summarise_user_message_carries_length_and_content() -> None:
    commission, _fake = _commission([])
    message = commission.build_user_message(
        SummariseInput(content=_SOURCE, length="medium"),
        CallContext(),
    )

    assert "Target length: medium" in message
    assert _SOURCE in message
    assert "Focus:" not in message  # no focus given → no focus line


def test_summarise_user_message_includes_focus_when_given() -> None:
    commission, _fake = _commission([])
    message = commission.build_user_message(
        SummariseInput(content=_SOURCE, focus="the birds"),
        CallContext(),
    )

    assert "Focus: the birds" in message


def _tool_names(call_kwargs: dict[str, Any]) -> set[str]:
    """Names the LLM was offered on a given chat.completions call."""
    return {t["function"]["name"] for t in call_kwargs["tools"]}


async def test_summarise_empty_toolbox_offers_only_conclude() -> None:
    # Pure-judgment Commission: nothing to fetch, so the only tool on the
    # menu is the framework-injected conclude.
    commission, fake = _commission(
        [llm_response(tool_calls=[("c1", "conclude", {"summary": "x"})])]
    )

    await commission.invoke(SummariseInput(content=_SOURCE), CallContext())

    assert _tool_names(fake.completions.calls[0]) == {"conclude"}


async def test_summarise_budget_exceeded_after_first_llm_call() -> None:
    # First call uses 10000 in + 1000 out tokens.
    # Cost = (10000*0.50 + 1000*3.00) / 1M = $0.008. Budget $0.001 → exceeded
    # at the post-turn check, before the conclude output is packaged.
    commission, fake = _commission(
        [
            llm_response(
                tool_calls=[("c1", "conclude", {"summary": "x"})],
                in_tokens=10000,
                out_tokens=1000,
            )
        ]
    )

    result = await commission.invoke(
        SummariseInput(content=_SOURCE),
        CallContext(budget_usd=0.001),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "budget_exceeded"
    assert len(fake.completions.calls) == 1
    assert result.cost.estimated_usd > 0.001


async def test_summarise_cancellation_at_entry_makes_no_llm_call() -> None:
    commission, fake = _commission([llm_response(tool_calls=None)])

    result = await commission.invoke(
        SummariseInput(content=_SOURCE),
        CallContext(cancel=AlwaysCancelled()),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert len(fake.completions.calls) == 0


async def test_summarise_free_text_is_nudged_then_fails_on_second_slip() -> None:
    # The loop disallows free-form completion: a first prose reply earns a
    # corrective nudge, a second one fails the run.
    commission, fake = _commission(
        [
            llm_response(tool_calls=None, content="Here's a summary: a cat."),
            llm_response(tool_calls=None, content="Still just prose."),
        ]
    )

    result = await commission.invoke(SummariseInput(content=_SOURCE), CallContext())

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "no tool call" in result.error.detail.lower()
    assert len(fake.completions.calls) == 2


async def test_summarise_emits_loop_start_progress_event() -> None:
    events: list[ProgressEvent] = []
    commission, _fake = _commission(
        [llm_response(tool_calls=[("c1", "conclude", {"summary": "x"})])]
    )

    await commission.invoke(
        SummariseInput(content=_SOURCE),
        CallContext(on_progress=events.append),
    )

    assert any(e.phase == "loop_start" and e.commission_name == "summarise" for e in events)


async def test_summarise_invalid_conclude_args_are_fed_back_then_recover() -> None:
    # First conclude omits the required `summary` field; the loop feeds the
    # validation error back and the LLM concludes correctly on retry.
    commission, fake = _commission(
        [
            llm_response(tool_calls=[("c1", "conclude", {"wrong_field": "oops"})]),
            llm_response(tool_calls=[("c2", "conclude", {"summary": "A content cat."})]),
        ]
    )

    result = await commission.invoke(SummariseInput(content=_SOURCE), CallContext())

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.summary == "A content cat."
    # The second call must have carried the validation error as a tool message.
    second_messages = fake.completions.calls[1]["messages"]
    tool_msg = next(m for m in second_messages if m["role"] == "tool")
    assert "validate" in tool_msg["content"]
