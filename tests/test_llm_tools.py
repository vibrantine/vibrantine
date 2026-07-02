"""Tests for the LLM-loop helpers.

Covers how `run_llm_loop` translates a commission's opening message
(`str | list[ContentPart]`) into the content the provider actually receives,
how child results are rendered back to the LLM (partial keeps its output),
and the free-text nudge. A fake AsyncOpenAI-shaped client records each call
so the messages it was sent can be inspected.
"""

import json
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from conftest import FakeClient, llm_response
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


class _Out(BaseModel):
    answer: str


async def _run(user_message: str | list[Any]) -> dict[str, Any]:
    """Run one loop with the given opening message; return the user message sent."""
    fake = FakeClient(
        [llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})], in_tokens=10, out_tokens=5)]
    )
    await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model="google/gemini-3-flash-preview",
        system_prompt="sys",
        user_message=user_message,
        toolbox=(),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
    )
    messages = fake.completions.calls[0]["messages"]
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

    async def invoke(
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
    # A partial child's output must reach the calling LLM — partial is the
    # policy that *preserves* usable output, so rendering only the error
    # would waste the child's spend.
    fake = FakeClient(
        [
            llm_response(tool_calls=[("t1", "partial_probe", {"query": "q"})]),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "done"})]),
        ]
    )
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model="google/gemini-3-flash-preview",
        system_prompt="sys",
        user_message="go",
        toolbox=(_PartialTool(),),
        output_type=_Out,
        ctx=CallContext(),
        max_iterations=3,
        prices_per_million=(0.0, 0.0),
    )
    assert outcome.output is not None
    second_call_messages = fake.completions.calls[1]["messages"]
    tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
    rendered = json.loads(tool_msg["content"])
    assert rendered["partial_output"] == {"text": "the usable half"}
    assert rendered["error"]["kind"] == "output_too_large"


# --- Free-text replies: nudge once, fail on repeat --------------------------


async def test_free_text_reply_gets_one_corrective_nudge() -> None:
    fake = FakeClient(
        [
            llm_response(content="I think the answer is x."),
            llm_response(tool_calls=[("c1", "conclude", {"answer": "x"})]),
        ]
    )
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model="google/gemini-3-flash-preview",
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
    second_call_messages = fake.completions.calls[1]["messages"]
    nudges = [
        m
        for m in second_call_messages
        if m["role"] == "user" and "Respond only with tool calls" in str(m["content"])
    ]
    assert len(nudges) == 1


async def test_second_free_text_reply_fails_the_loop() -> None:
    fake = FakeClient(
        [
            llm_response(content="prose"),
            llm_response(content="more prose"),
        ]
    )
    outcome = await run_llm_loop(
        client=cast(AsyncOpenAI, fake),
        model="google/gemini-3-flash-preview",
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
