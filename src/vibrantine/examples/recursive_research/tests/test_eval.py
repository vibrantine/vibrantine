"""Heuristic evaluation cases for RecursiveResearchCommission.

This is the evaluation lane of docs/commission-testing.md: a real model runs
the Commission and the output is graded against criteria written in advance.
The sources are pinned, not live: fixture pages about a fictional
desalination project are served through the real `FetchTool` via an injected
`httpx.MockTransport`, and the model is pinned to `EVAL_MODEL`. The only free
variable is the Commission's competence (fetch, ground, cite, synthesize).
The subject is fictional so the model cannot answer from its own knowledge;
every correct fact must come from a fixture page.

Each case plants targets (facts the answer must carry) and traps (nearby
wrong answers a sloppy read would pick up). Delegation behavior (leaves
answering from fetches, at most three sub-questions) is not output-observable
today and is reviewed from transcripts instead.

Skips without OPENROUTER_API_KEY. Run with transcripts shown:

    uv run --env-file .env pytest -m eval -s
"""

import os

import httpx
import pytest

from vibrantine.contract import CommissionResult
from vibrantine.examples.recursive_research import (
    RecursiveResearchCommission,
    ResearchInput,
    ResearchOutput,
)
from vibrantine.orchestrator import run_one
from vibrantine.tools.fetch import FetchTool

from .fixture_pages import (
    FIXTURE_PAGES,
    URL_COMMISSION_GOV,
    URL_COMMISSION_NEWS,
    URL_COST,
    URL_ENERGY,
    URL_OUTPUT,
)

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.environ.get("OPENROUTER_API_KEY"),
        reason="OPENROUTER_API_KEY not set; eval cases skipped.",
    ),
]

# Pinned so a failing case means the Commission changed, not the default
# model. Swapping this constant is a deliberate, separate experiment.
EVAL_MODEL = "google/gemini-3-flash-preview"

BUDGET_USD = 0.25


def _fixture_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        page = FIXTURE_PAGES.get(str(request.url))
        if page is None:
            return httpx.Response(404, text="No such fixture page.")
        return httpx.Response(200, text=page, headers={"content-type": "text/plain"})

    return httpx.MockTransport(handler)


def _agent() -> RecursiveResearchCommission:
    return RecursiveResearchCommission(
        max_depth=1,
        fetch=FetchTool(transport=_fixture_transport()),
        model=EVAL_MODEL,
    )


def _cited_sources(result: CommissionResult[ResearchOutput]) -> set[str]:
    assert result.output is not None
    return {p.source for claim in result.output.claims for p in claim.sources}


def _transcript(case: str, question: str, result: CommissionResult[ResearchOutput]) -> None:
    """Human-readable dump of the run; mechanical checks catch what we
    predicted, skimming this catches what we didn't."""
    print(f"\n=== eval: {case} ===")
    print(f"Q: {question}")
    if result.output is None:
        print(f"FAILED: {result.error}")
        return
    print(f"A: {result.output.answer}")
    for claim in result.output.claims:
        sources = ", ".join(p.source for p in claim.sources)
        print(f"  claim [{claim.confidence}]: {claim.value}  <- {sources}")
    print(f"cost: ${result.cost.estimated_usd:.4f}")


async def test_direct_source_question_cites_target_and_avoids_trap() -> None:
    # One page answers the question outright. Target: the audited 4,821 ML
    # figure. Trap: the neighbouring Saltgrass plant's 7,450 ML on the same
    # page; quoting it means the model confused the two plants.
    question = "How much water did the Meridian Vale desalination project produce in 2025?"
    result = await run_one(
        _agent(),
        ResearchInput(question=question, seed_urls=[URL_OUTPUT]),
        budget_usd=BUDGET_USD,
    )
    _transcript("direct_source_question", question, result)

    assert result.status == "success", result.error
    assert result.output is not None
    assert "4,821" in result.output.answer
    # A good answer may mention the neighbour for comparison (the first live
    # run did exactly that, correctly attributed); the trap only springs if
    # 7,450 is presented as Meridian Vale's output. So: any sentence or claim
    # carrying the trap figure must name Saltgrass.
    statements = result.output.answer.split(".") + [str(c.value) for c in result.output.claims]
    for statement in statements:
        if "7,450" in statement:
            assert "saltgrass" in statement.lower(), (
                f"trap figure appears without Saltgrass attribution: {statement!r}"
            )
    cited = _cited_sources(result)
    assert cited, "expected at least one cited claim"
    assert cited <= set(FIXTURE_PAGES), f"cited a non-fixture source: {cited}"
    assert URL_OUTPUT in cited


async def test_broad_question_covers_all_aspects_from_multiple_sources() -> None:
    # Three pages carry one aspect each; no single fetch answers the whole
    # question. Targets: the output figure, the solar power source, and the
    # construction cost, with at least two distinct sources cited.
    question = (
        "For the Meridian Vale desalination project, summarize its 2025 water "
        "output, its power source, and its final construction cost."
    )
    result = await run_one(
        _agent(),
        ResearchInput(question=question, seed_urls=[URL_OUTPUT, URL_ENERGY, URL_COST]),
        budget_usd=BUDGET_USD,
    )
    _transcript("broad_decomposable_question", question, result)

    assert result.status == "success", result.error
    assert result.output is not None
    answer = result.output.answer
    assert "4,821" in answer
    assert "copperline" in answer.lower() or "solar" in answer.lower()
    assert "1.9" in answer
    cited = _cited_sources(result)
    assert cited <= set(FIXTURE_PAGES), f"cited a non-fixture source: {cited}"
    assert len(cited) >= 2, f"expected claims from at least two sources, got {cited}"


async def test_conflicting_sources_preserve_both_accounts() -> None:
    # Watchlist case. The government page and the news page disagree on when
    # the plant became operational (March 2024 vs November 2025). The crude
    # heuristic below only proves both accounts survived into the answer;
    # whether the disagreement is *framed* well (attributed, not averaged
    # away) is judged by a human from the transcript.
    question = "When did the Meridian Vale desalination project become operational?"
    result = await run_one(
        _agent(),
        ResearchInput(question=question, seed_urls=[URL_COMMISSION_GOV, URL_COMMISSION_NEWS]),
        budget_usd=BUDGET_USD,
    )
    _transcript("source_conflict", question, result)

    assert result.status == "success", result.error
    assert result.output is not None
    assert "2024" in result.output.answer
    assert "2025" in result.output.answer
    cited = _cited_sources(result)
    assert cited <= set(FIXTURE_PAGES), f"cited a non-fixture source: {cited}"
