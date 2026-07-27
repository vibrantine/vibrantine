"""Integration test for SynthesizeCommission against real OpenRouter.

Skips automatically when `OPENROUTER_API_KEY` is not set, so the default
`uv run pytest` invocation stays offline. Run with credentials via:

    uv run --env-file .env pytest -m integration

Per AGENTS.md, this is the canonical pattern for tests that touch the LLM
provider: marked `@pytest.mark.integration`, gated on the env var.
"""

import os
from datetime import UTC, datetime

import pytest

from vibrantine import Provenance, run_commission
from vibrantine.examples.synthesize import (
    SynthesisSource,
    SynthesizeCommission,
    SynthesizeInput,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set; integration test skipped.",
    ),
]


async def test_synthesize_against_real_openrouter_returns_grounded_summary() -> None:
    sources = [
        SynthesisSource(
            content=(
                "The Apollo 11 mission launched on July 16, 1969 and landed on "
                "the Moon on July 20. Neil Armstrong was the first person to "
                "walk on the lunar surface."
            ),
            provenance=Provenance(
                source="https://example.test/apollo-1",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
        ),
        SynthesisSource(
            content=(
                "Buzz Aldrin followed Neil Armstrong onto the Moon's surface "
                "shortly after Armstrong's initial step. Michael Collins "
                "remained in lunar orbit aboard the command module."
            ),
            provenance=Provenance(
                source="https://example.test/apollo-2",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
        ),
    ]

    result = await run_commission(
        SynthesizeCommission(),
        SynthesizeInput(
            sources=sources,
            focus="Who walked on the Moon during Apollo 11?",
        ),
        budget_usd=0.10,
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.summary_text.strip()
    assert len(result.output.claims) >= 1

    input_uris = {s.provenance.source for s in sources}
    for claim in result.output.claims:
        assert claim.sources, "every claim must cite at least one source"
        for prov in claim.sources:
            assert prov.source in input_uris, "claim cited a source not in the input set"

    assert result.cost.estimated_usd > 0
