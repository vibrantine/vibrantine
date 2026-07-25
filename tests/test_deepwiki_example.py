"""Contract tests for the application-layer DeepWiki binding example."""

from __future__ import annotations

from typing import Any, cast

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from vibrantine import run_commission
from vibrantine.examples.deepwiki import (
    DeepWikiQuestionInput,
    DeepWikiQuestionOutput,
    bind_deepwiki_question,
    create_deepwiki_guide,
)
from vibrantine.mcp.client import MCPConnection
from vibrantine.testing import ScriptedLLM, llm_response, scripted_model


class _FakeSession:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> CallToolResult:
        self.calls.append((name, arguments or {}))
        return CallToolResult(
            content=[TextContent(type="text", text=self.answer)],
            structuredContent={"result": self.answer},
        )


def _connection(session: _FakeSession) -> MCPConnection:
    return MCPConnection(
        cast(ClientSession, session),
        source="https://mcp.deepwiki.com/mcp",
    )


def test_deepwiki_input_requires_one_owner_repository_name() -> None:
    DeepWikiQuestionInput(repository="vibrantine/vibrantine", question="What is it?")

    with pytest.raises(ValidationError):
        DeepWikiQuestionInput(repository="vibrantine", question="What is it?")
    with pytest.raises(ValidationError):
        DeepWikiQuestionInput(repository="vibrantine/vibrantine/extra", question="What is it?")


async def test_deepwiki_tool_translates_the_local_contract() -> None:
    session = _FakeSession("Vibrantine uses typed Commissions.")
    tool = bind_deepwiki_question(_connection(session))

    result = await run_commission(
        tool,
        DeepWikiQuestionInput(
            repository="vibrantine/vibrantine",
            question="What is the core abstraction?",
        ),
    )

    assert tool.name == "deepwiki_ask"
    assert result.status == "success", result.error
    assert result.output == DeepWikiQuestionOutput(
        answer="Vibrantine uses typed Commissions."
    )
    assert session.calls == [
        (
            "ask_question",
            {
                "repoName": "vibrantine/vibrantine",
                "question": "What is the core abstraction?",
            },
        )
    ]


async def test_deepwiki_guide_receives_the_bound_tool_through_its_toolbox() -> None:
    session = _FakeSession("The contract is a typed work order and result envelope.")
    guide = create_deepwiki_guide(_connection(session))
    model = ScriptedLLM(
        [
            llm_response(
                tool_calls=[
                    (
                        "call-1",
                        "deepwiki_ask",
                        {
                            "repository": "vibrantine/vibrantine",
                            "question": "What forms the contract?",
                        },
                    )
                ]
            ),
            llm_response(
                tool_calls=[
                    (
                        "call-2",
                        "conclude",
                        {
                            "answer": (
                                "The contract is a typed work order and result envelope."
                            )
                        },
                    )
                ]
            ),
        ]
    )

    result = await run_commission(
        guide,
        DeepWikiQuestionInput(
            repository="vibrantine/vibrantine",
            question="What forms the contract?",
        ),
        models=[scripted_model(model)],
    )

    assert result.status == "success", result.error
    assert result.output == DeepWikiQuestionOutput(
        answer="The contract is a typed work order and result envelope."
    )
    assert tuple(tool.name for tool in guide.toolbox) == ("deepwiki_ask",)
    assert session.calls == [
        (
            "ask_question",
            {
                "repoName": "vibrantine/vibrantine",
                "question": "What forms the contract?",
            },
        )
    ]
