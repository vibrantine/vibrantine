"""Tests for NewsDigestCommission: one field's fetch-and-synthesize coordinator."""

import pytest

from vibrantine import run_commission
from vibrantine.examples.morning_briefing.subcommissions.news_digest import (
    NewsDigestCommission,
    NewsDigestInput,
)
from vibrantine.examples.morning_briefing.tests.fakes import (
    SYNTH_COST,
    make_digest,
)
from vibrantine.testing import AlwaysCancelled

DATE = "Monday 06 July 2026"

PAGES = {
    "https://news.test/world/a": (200, "world story a"),
    "https://news.test/world/b": (200, "world story b"),
}

CLAIMS = [
    {"value": "Talks resumed overnight", "source_indices": [0], "confidence": "grounded"},
    {"value": "A ceasefire holds", "source_indices": [0, 1], "confidence": "grounded"},
]


def test_construction_requires_field_and_sources() -> None:
    with pytest.raises(ValueError, match="field"):
        make_digest(field=" ", pages=PAGES, claims=[])
    with pytest.raises(ValueError, match="source"):
        NewsDigestCommission(field="world", sources=[])


async def test_success_returns_cited_digest_with_summed_cost() -> None:
    digest, _, entry = make_digest(
        field="international",
        pages=PAGES,
        claims=CLAIMS,
        summary="A steady morning internationally.",
    )
    result = await run_commission(
        digest, NewsDigestInput(briefing_date=DATE), budget_usd=0.50, models=[entry]
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.field == "international"
    assert result.output.summary_text == "A steady morning internationally."
    assert [c.value for c in result.output.claims] == [c["value"] for c in CLAIMS]
    assert result.output.failed_sources == []
    # Fetches are free today, so the digest's cost is the Synthesize run alone.
    assert abs(result.cost.estimated_usd - SYNTH_COST) < 1e-12
    # Claims carry the fetched sources' provenance, re-attached by Synthesize.
    assert result.output.claims[1].sources[1].source == "https://news.test/world/b"


async def test_partial_when_a_source_fails_lists_it() -> None:
    pages = dict(PAGES)
    pages["https://news.test/world/b"] = (404, "gone")
    digest, _, entry = make_digest(field="world", pages=pages, claims=CLAIMS[:1])

    result = await run_commission(digest, NewsDigestInput(briefing_date=DATE), models=[entry])

    assert result.status == "partial"
    assert result.output is not None
    assert result.output.failed_sources == ["https://news.test/world/b"]
    assert result.error is not None
    assert "https://news.test/world/b" in result.error.detail


async def test_all_sources_failing_fails_before_any_llm_call() -> None:
    pages = {url: (500, "boom") for url in PAGES}
    digest, fake, entry = make_digest(field="tech", pages=pages, claims=[])

    result = await run_commission(digest, NewsDigestInput(briefing_date=DATE), models=[entry])

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "tech" in result.error.detail
    assert len(fake.calls) == 0


async def test_synthesize_failure_propagates_its_error_kind() -> None:
    digest, _, entry = make_digest(
        field="world",
        pages=PAGES,
        claims=[],
        synth_payload="not valid json at all",
    )
    result = await run_commission(digest, NewsDigestInput(briefing_date=DATE), models=[entry])

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert result.error.detail.startswith("Synthesize failed:")


async def test_cancellation_before_fetches_returns_cancelled() -> None:
    digest, fake, entry = make_digest(field="world", pages=PAGES, claims=[])

    result = await run_commission(
        digest,
        NewsDigestInput(briefing_date=DATE),
        models=[entry],
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert len(fake.calls) == 0
