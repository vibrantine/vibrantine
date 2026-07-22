"""Contract tests for the outward-compatibility sonnet Commission.

The model is scripted because these tests prove the typed Commission boundary,
not literary judgment. Rhyme, meter, and quality remain evaluation concerns.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from vibrantine import run_commission
from vibrantine.contract import CallContext
from vibrantine.examples.compose_sonnet import (
    ComposeSonnetCommission,
    ComposeSonnetInput,
    ComposeSonnetOutput,
)
from vibrantine.testing import AlwaysCancelled, ScriptedLLM, llm_response, scripted_model

_DESCRIPTION = (
    "Compose an original 14-line sonnet through Vibrantine. Use this when the user "
    "explicitly asks to write a Vibrantine sonnet or invoke the sonnet Commission. "
    "Provide the sonnet's subject. Returns a title and exactly 14 ordered lines."
)
_LINES = [f"Line {number}" for number in range(1, 15)]


def _tool_names(call: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in call["tools"]}


def test_sonnet_identity_matches_the_compatibility_specification() -> None:
    commission = ComposeSonnetCommission()

    assert commission.name == "compose_vibrantine_sonnet"
    assert commission.description == _DESCRIPTION
    assert commission.input_type is ComposeSonnetInput
    assert commission.output_type is ComposeSonnetOutput


def test_sonnet_output_requires_exactly_fourteen_lines() -> None:
    ComposeSonnetOutput(title="Fourteen", lines=_LINES)

    with pytest.raises(ValidationError):
        ComposeSonnetOutput(title="Thirteen", lines=_LINES[:-1])
    with pytest.raises(ValidationError):
        ComposeSonnetOutput(title="Fifteen", lines=[*_LINES, "Line 15"])


def test_sonnet_schema_exposes_the_exact_line_bounds() -> None:
    lines_schema = ComposeSonnetOutput.model_json_schema()["properties"]["lines"]

    assert lines_schema["minItems"] == 14
    assert lines_schema["maxItems"] == 14


def test_sonnet_user_message_carries_the_subject() -> None:
    message = ComposeSonnetCommission().build_user_message(
        ComposeSonnetInput(subject="the first winter rain"),
        CallContext(),
    )

    assert message == "Subject: the first winter rain"


async def test_sonnet_runs_through_the_default_loop() -> None:
    fake = ScriptedLLM(
        [llm_response(tool_calls=[("c1", "conclude", {"title": "Rain", "lines": _LINES})])]
    )

    result = await run_commission(
        ComposeSonnetCommission(),
        ComposeSonnetInput(subject="the first winter rain"),
        models=[scripted_model(fake)],
    )

    assert result.status == "success", result.error
    assert result.output == ComposeSonnetOutput(title="Rain", lines=_LINES)
    assert result.cost.in_tokens == 100
    assert result.cost.out_tokens == 50
    assert len(fake.calls) == 1
    assert _tool_names(fake.calls[0]) == {"conclude"}


async def test_invalid_line_count_is_reported_to_the_model_then_recovers() -> None:
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("c1", "conclude", {"title": "Rain", "lines": _LINES[:-1]})]),
            llm_response(tool_calls=[("c2", "conclude", {"title": "Rain", "lines": _LINES})]),
        ]
    )

    result = await run_commission(
        ComposeSonnetCommission(),
        ComposeSonnetInput(subject="the first winter rain"),
        models=[scripted_model(fake)],
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert len(result.output.lines) == 14
    tool_message = next(
        message for message in fake.calls[1]["messages"] if message["role"] == "tool"
    )
    assert "14" in tool_message["content"]


async def test_sonnet_honors_cancellation_before_calling_the_model() -> None:
    fake = ScriptedLLM([])

    result = await run_commission(
        ComposeSonnetCommission(),
        ComposeSonnetInput(subject="silence"),
        models=[scripted_model(fake)],
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert fake.calls == []
