"""Opt-in live proof of the DeepWiki MCP binding.

Run with:

    $env:VIBRANTINE_RUN_DEEPWIKI_INTEGRATION = "1"
    uv run pytest -m integration tests/test_deepwiki_integration.py
"""

import os

import pytest

from vibrantine import run_commission
from vibrantine.examples.deepwiki import (
    DEEPWIKI_MCP_URL,
    DeepWikiQuestionInput,
    bind_deepwiki_question,
)
from vibrantine.mcp.client import open_mcp_http

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VIBRANTINE_RUN_DEEPWIKI_INTEGRATION") != "1",
        reason="set VIBRANTINE_RUN_DEEPWIKI_INTEGRATION=1 to call public DeepWiki",
    ),
]


async def test_deepwiki_answers_through_the_bound_tool() -> None:
    async with open_mcp_http(DEEPWIKI_MCP_URL) as connection:
        result = await run_commission(
            bind_deepwiki_question(connection),
            DeepWikiQuestionInput(
                repository="modelcontextprotocol/python-sdk",
                question="Which class represents an MCP client session?",
            ),
        )

    assert result.status == "success", result.error
    assert result.output is not None
    assert "ClientSession" in result.output.answer
    assert result.provenance.source == ("mcp:https://mcp.deepwiki.com/mcp#ask_question")
