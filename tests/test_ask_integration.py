"""Integration test for AskCommission against real OpenRouter.

Skips automatically when `OPENROUTER_API_KEY` is not set, so the default
`uv run pytest` invocation stays offline. Run with credentials via:

    uv run --env-file .env pytest -m integration

This is the first LLM-loop integration test; it validates the full
LLM-tool-wrapper + dispatch-loop machinery end-to-end against a live
model. If this passes, the conclude-tool mechanic, the tool dispatch,
and the cost rollup are all proven.
"""

import os
from pathlib import Path

import pytest

from vibrantine.examples.ask import AskCommission, AskInput
from vibrantine.orchestrator import run_commission

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set; integration test skipped.",
    ),
]


async def test_ask_against_real_openrouter_answers_from_file(tmp_path: Path) -> None:
    file = tmp_path / "fact.txt"
    file.write_text(
        "The capital of France is Paris. Paris sits on the Seine river.\n",
        encoding="utf-8",
    )

    result = await run_commission(
        AskCommission(),
        AskInput(question="What is the capital of France?", file_path=file),
        budget_usd=0.10,
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert "paris" in result.output.answer.lower()
    assert result.cost.estimated_usd > 0
