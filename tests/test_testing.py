"""Tests for the vibrantine.testing doubles themselves.

The rest of the suite uses these doubles constantly, which exercises the
happy path for free; what needs direct proof is the module's own promises:
queue order, call recording, the loud script-exhaustion failure, and that a
ScriptedLLM drives a real Commission end to end through the public entry
points.
"""

from typing import ClassVar

import pytest
from pydantic import BaseModel, Field

from vibrantine import CallContext, Commission, run_one
from vibrantine.testing import AlwaysCancelled, ScriptedLLM, llm_response, scripted_model


class _In(BaseModel):
    question: str = Field(description="Probe input.")


class _Out(BaseModel):
    answer: str = Field(description="Probe output.")


class _Probe(Commission[_In, _Out]):
    """Minimal basic Commission: empty toolbox, rides the default loop."""

    name: ClassVar[str] = "probe"
    description: ClassVar[str] = "Test probe Commission."
    input_type: ClassVar[type] = _In
    output_type: ClassVar[type] = _Out
    system_prompt: ClassVar[str | None] = "Answer, then conclude."

    def build_user_message(self, input: _In, ctx: CallContext) -> str:
        return input.question


async def test_scripted_llm_drives_a_real_commission() -> None:
    scripted = ScriptedLLM([llm_response(tool_calls=[("t1", "conclude", {"answer": "42"})])])
    probe = _Probe()

    result = await run_one(probe, _In(question="?"), models=[scripted_model(scripted)])

    assert result.status == "success"
    assert result.output is not None and result.output.answer == "42"
    assert result.run_id is not None  # the real dispatch wrapping ran


async def test_responses_pop_in_order_and_calls_record_requests() -> None:
    scripted = ScriptedLLM(
        [
            llm_response(content="first: no tool call"),
            llm_response(tool_calls=[("t1", "conclude", {"answer": "second"})]),
        ]
    )
    probe = _Probe()

    result = await run_one(probe, _In(question="hello"), models=[scripted_model(scripted)])

    assert result.output is not None and result.output.answer == "second"
    assert len(scripted.calls) == 2
    first_messages = scripted.calls[0]["messages"]
    assert first_messages[0] == {"role": "system", "content": "Answer, then conclude."}
    assert first_messages[1] == {"role": "user", "content": "hello"}


async def test_exhausted_script_fails_loudly() -> None:
    scripted = ScriptedLLM([])
    with pytest.raises(AssertionError, match="ran out of scripted responses"):
        await scripted.chat.completions.create(model="x", messages=[])


def test_llm_response_carries_token_counts() -> None:
    response = llm_response(content="hi", in_tokens=1000, out_tokens=200)
    assert response.usage.prompt_tokens == 1000
    assert response.usage.completion_tokens == 200


def test_always_cancelled_is_cancelled() -> None:
    assert AlwaysCancelled().is_cancelled is True
