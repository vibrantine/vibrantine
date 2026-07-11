"""Tests for MorningBriefingCommission: the heterogeneous coordinator tree.

Every child runs against its own scripted fake (fetches through
httpx.MockTransport, LLM children through fake clients), so the whole
three-level tree executes without network or API keys and the partial
semantics and cost arithmetic are exact assertions.
"""

from pathlib import Path
from typing import Any

import pytest

from vibrantine.contract import ProgressEvent
from vibrantine.examples.morning_briefing import (
    MorningBriefingCommission,
    MorningBriefingInput,
)
from vibrantine.examples.morning_briefing.tests.fakes import (
    SYNTH_COST,
    TURN_COST,
    make_digest,
    make_summarize,
    make_weather,
)
from vibrantine.orchestrator import run_one
from vibrantine.testing import AlwaysCancelled, ScriptedLLM

WORLD_PAGES = {
    "https://news.test/world/a": (200, "world story a"),
    "https://news.test/world/b": (200, "world story b"),
}
TECH_PAGES = {
    "https://news.test/tech/a": (200, "tech story a"),
}

WORLD_CLAIMS = [
    {"value": "Talks resumed overnight", "source_indices": [0], "confidence": "grounded"},
    {"value": "A ceasefire holds", "source_indices": [0, 1], "confidence": "grounded"},
]
TECH_CLAIMS = [
    {"value": "A chip fab broke ground", "source_indices": [0], "confidence": "grounded"},
]


def _briefing(
    *,
    weather_fails: bool = False,
    summarize_fails: bool = False,
    world_pages: dict[str, tuple[int, str]] | None = None,
    tech_pages: dict[str, tuple[int, str]] | None = None,
    world_claims: list[dict[str, Any]] | None = None,
) -> tuple[MorningBriefingCommission, dict[str, ScriptedLLM]]:
    weather, weather_fake = make_weather(fails=weather_fails)
    world, world_fake = make_digest(
        field="international",
        pages=world_pages or WORLD_PAGES,
        claims=world_claims if world_claims is not None else WORLD_CLAIMS,
        summary="A steady morning internationally.",
    )
    tech, tech_fake = make_digest(
        field="tech",
        pages=tech_pages or TECH_PAGES,
        claims=TECH_CLAIMS,
        summary="One big tech story.",
    )
    summarize, summarize_fake = make_summarize(fails=summarize_fails)
    briefing = MorningBriefingCommission(weather=weather, news=(world, tech), summarize=summarize)
    fakes = {
        "weather": weather_fake,
        "world": world_fake,
        "tech": tech_fake,
        "summarize": summarize_fake,
    }
    return briefing, fakes


def test_construction_requires_at_least_one_news_digest() -> None:
    weather, _ = make_weather()
    with pytest.raises(ValueError, match="news digest"):
        MorningBriefingCommission(weather=weather, news=(), summarize=make_summarize()[0])


def test_toolbox_lists_every_child_in_order() -> None:
    briefing, _ = _briefing()
    assert [child.name for child in briefing.toolbox] == [
        "weather",
        "news_digest",
        "news_digest",
        "summarize",
    ]


async def test_success_writes_briefing_with_all_sections(tmp_path: Path) -> None:
    briefing, _ = _briefing()
    out_path = tmp_path / "briefing.md"

    result = await run_one(briefing, MorningBriefingInput(output_path=out_path), budget_usd=0.50)

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.markdown_path == out_path
    assert result.output.executive_summary == "The morning in brief."
    assert result.output.failed_sections == []
    assert [s.title for s in result.output.sections] == [
        "Weather",
        "International news",
        "Tech news",
    ]
    assert [s.claim_count for s in result.output.sections] == [0, 2, 1]

    text = out_path.read_text(encoding="utf-8")
    assert text.startswith("# Morning briefing: ")
    assert "The morning in brief." in text
    assert "## Weather" in text
    assert "Cold and clear, light rain after noon." in text
    assert "## International news" in text
    assert "## Tech news" in text
    assert "A ceasefire holds" in text
    # One footnoted source list across sections, numbered in citation order.
    assert "## Sources" in text
    for url in [*WORLD_PAGES, *TECH_PAGES]:
        assert url in text


async def test_total_cost_sums_every_child(tmp_path: Path) -> None:
    briefing, _ = _briefing()
    # weather turn + two digests (Synthesize runs) + summarize turn.
    expected = TURN_COST + 2 * SYNTH_COST + TURN_COST

    result = await run_one(
        briefing, MorningBriefingInput(output_path=tmp_path / "b.md"), budget_usd=0.50
    )

    assert abs(result.cost.estimated_usd - expected) < 1e-12


async def test_failed_source_inside_a_section_makes_the_briefing_partial(
    tmp_path: Path,
) -> None:
    pages = dict(WORLD_PAGES)
    pages["https://news.test/world/b"] = (404, "gone")
    # Only one source survives, so the scripted claims may cite index 0 only.
    briefing, _ = _briefing(world_pages=pages, world_claims=WORLD_CLAIMS[:1])

    result = await run_one(briefing, MorningBriefingInput(output_path=tmp_path / "b.md"))

    assert result.status == "partial"
    assert result.output is not None
    assert result.output.failed_sections == []
    world = result.output.sections[1]
    assert world.failed_sources == ["https://news.test/world/b"]
    assert result.error is not None
    assert "https://news.test/world/b" in result.error.detail
    # The briefing still covers everything that survived.
    assert result.output.executive_summary is not None


async def test_whole_section_failing_is_skipped_and_named(tmp_path: Path) -> None:
    briefing, _ = _briefing(weather_fails=True)
    out_path = tmp_path / "b.md"

    result = await run_one(briefing, MorningBriefingInput(output_path=out_path))

    assert result.status == "partial"
    assert result.output is not None
    assert result.output.failed_sections == ["Weather"]
    assert [s.title for s in result.output.sections] == ["International news", "Tech news"]
    assert result.error is not None
    assert "Weather" in result.error.detail

    text = out_path.read_text(encoding="utf-8")
    assert "## Weather" not in text
    assert "Sections unavailable this morning: Weather." in text


async def test_all_sections_failing_fails_without_writing(tmp_path: Path) -> None:
    briefing, fakes = _briefing(
        weather_fails=True,
        world_pages={url: (500, "boom") for url in WORLD_PAGES},
        tech_pages={url: (500, "boom") for url in TECH_PAGES},
    )
    out_path = tmp_path / "b.md"

    result = await run_one(briefing, MorningBriefingInput(output_path=out_path))

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "All 3 sections failed" in result.error.detail
    assert not out_path.exists()
    assert len(fakes["summarize"].calls) == 0


async def test_executive_summary_failure_degrades_instead_of_killing(
    tmp_path: Path,
) -> None:
    briefing, _ = _briefing(summarize_fails=True)
    out_path = tmp_path / "b.md"

    result = await run_one(briefing, MorningBriefingInput(output_path=out_path))

    assert result.status == "partial"
    assert result.output is not None
    assert result.output.executive_summary is None
    assert result.error is not None
    assert "executive summary failed" in result.error.detail
    # The briefing is still written, just without the opening paragraph.
    text = out_path.read_text(encoding="utf-8")
    assert "## International news" in text


async def test_unwritable_path_fails_with_real_accumulated_cost(tmp_path: Path) -> None:
    briefing, _ = _briefing()
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed", encoding="utf-8")
    out_path = blocker / "briefing.md"  # parent is a file, so mkdir raises

    result = await run_one(briefing, MorningBriefingInput(output_path=out_path))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert str(out_path) in result.error.detail
    assert result.cost.estimated_usd > 0.0  # section + summary spend not lost


async def test_cancellation_before_sections_returns_cancelled(tmp_path: Path) -> None:
    briefing, fakes = _briefing()

    result = await run_one(
        briefing,
        MorningBriefingInput(output_path=tmp_path / "b.md"),
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert all(len(fake.calls) == 0 for fake in fakes.values())


async def test_progress_events_bubble_from_every_level(tmp_path: Path) -> None:
    briefing, _ = _briefing()
    events: list[ProgressEvent] = []

    result = await run_one(
        briefing,
        MorningBriefingInput(output_path=tmp_path / "b.md"),
        budget_usd=0.50,
        on_progress=events.append,
    )

    assert result.status == "success"
    coordinator_phases = [e.phase for e in events if e.commission_name == "morning_briefing"]
    assert coordinator_phases == ["sections", "executive_summary", "written"]
    # Children two and three levels down share the same callback.
    assert any(e.commission_name == "news_digest" and e.phase == "fetching" for e in events)
    assert any(e.commission_name == "synthesize" and e.phase == "synthesis_pass" for e in events)
    assert any(e.commission_name == "weather" and e.phase == "loop_start" for e in events)
