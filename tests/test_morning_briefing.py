"""Tests for MorningBriefingCommission.

Fetch is exercised through httpx.MockTransport. Synthesize is exercised
through the same fake AsyncOpenAI client pattern as test_synthesize.py.
Together they let the whole pipeline run without network or API keys.
"""

import json
from pathlib import Path
from typing import Any, cast

import httpx
from conftest import AlwaysCancelled, FakeClient, llm_response
from openai import AsyncOpenAI

from vibrantine.contract import CallContext, ProgressEvent
from vibrantine.examples.morning_briefing import (
    MorningBriefingCommission,
    MorningBriefingInput,
)
from vibrantine.examples.synthesize import SynthesizeCommission
from vibrantine.orchestrator import run_one
from vibrantine.tools.fetch import FetchTool


def _handler_factory(
    responses: dict[str, tuple[int, str]],
) -> httpx.MockTransport:
    """Map URL → (status, body). Unknown URLs raise to surface test bugs loudly."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) not in responses:
            raise AssertionError(f"unexpected URL in test: {request.url}")
        status, body = responses[str(request.url)]
        return httpx.Response(status, text=body, headers={"content-type": "text/plain"})

    return httpx.MockTransport(handler)


def _structured(claims: list[dict[str, Any]], summary: str = "Synthesized.") -> str:
    return json.dumps({"summary_text": summary, "claims": claims})


def _synthesize(
    structured_payload: str,
    *,
    free_in: int = 1000,
    free_out: int = 200,
    structured_in: int = 500,
    structured_out: int = 100,
) -> tuple[SynthesizeCommission, FakeClient]:
    fake = FakeClient(
        [
            llm_response(content="Free-form synthesis.", in_tokens=free_in, out_tokens=free_out),
            llm_response(
                content=structured_payload, in_tokens=structured_in, out_tokens=structured_out
            ),
        ]
    )
    commission = SynthesizeCommission(
        client=cast(AsyncOpenAI, fake),
        model="google/gemini-3-flash-preview",  # stable fixture for pricing math
    )
    return commission, fake


URLS = [
    "https://example.test/a",
    "https://example.test/b",
    "https://example.test/c",
]


async def test_briefing_success_writes_markdown_and_returns_typed_output(
    tmp_path: Path,
) -> None:
    fetch = FetchTool(
        transport=_handler_factory({url: (200, f"body-{i}") for i, url in enumerate(URLS)})
    )
    structured = _structured(
        [
            {"value": "Claim one", "source_indices": [0, 1], "confidence": "grounded"},
            {"value": "Claim two", "source_indices": [2], "confidence": "speculative"},
        ],
        summary="Three sources align on the key facts.",
    )
    synth, _fake = _synthesize(structured)
    out_path = tmp_path / "briefing.md"

    result = await run_one(
        MorningBriefingCommission(fetch=fetch, synthesize=synth),
        MorningBriefingInput(urls=URLS, output_path=out_path),
        budget_usd=0.50,
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.markdown_path == out_path
    assert result.output.claim_count == 2
    assert result.output.failed_urls == []
    assert "Three sources align on the key facts." in result.output.summary_text

    text = out_path.read_text(encoding="utf-8")
    assert "Claim one" in text and "Claim two" in text
    for url in URLS:
        assert url in text


async def test_briefing_partial_fetch_failure_returns_partial_status(
    tmp_path: Path,
) -> None:
    fetch = FetchTool(
        transport=_handler_factory(
            {
                URLS[0]: (200, "alpha"),
                URLS[1]: (404, "missing"),
                URLS[2]: (200, "gamma"),
            }
        )
    )
    structured = _structured(
        [{"value": "Survivors agree", "source_indices": [0, 1], "confidence": "grounded"}],
        summary="Two survivors.",
    )
    synth, _fake = _synthesize(structured)
    out_path = tmp_path / "briefing.md"

    result = await run_one(
        MorningBriefingCommission(fetch=fetch, synthesize=synth),
        MorningBriefingInput(urls=URLS, output_path=out_path),
        budget_usd=0.50,
    )

    assert result.status == "partial"
    assert result.output is not None
    assert result.output.failed_urls == [URLS[1]]
    assert result.error is not None
    assert URLS[1] in result.error.detail

    text = result.output.markdown_path.read_text(encoding="utf-8")
    assert URLS[0] in text and URLS[2] in text
    assert URLS[1] not in text


async def test_briefing_total_cost_sums_children(tmp_path: Path) -> None:
    fetch = FetchTool(
        transport=_handler_factory({url: (200, f"body-{i}") for i, url in enumerate(URLS)})
    )
    # gemini-3-flash-preview: $0.50/M input, $3.00/M output.
    synth, _fake = _synthesize(
        _structured([{"value": "A", "source_indices": [0], "confidence": "grounded"}]),
        free_in=1000,
        free_out=200,
        structured_in=500,
        structured_out=100,
    )
    expected_synth_cost = (1500 * 0.50 + 300 * 3.00) / 1_000_000

    result = await run_one(
        MorningBriefingCommission(fetch=fetch, synthesize=synth),
        MorningBriefingInput(urls=URLS, output_path=tmp_path / "b.md"),
        budget_usd=0.50,
    )

    # Fetch costs are zero today; coordinator cost equals Synthesize cost alone.
    assert abs(result.cost.estimated_usd - expected_synth_cost) < 1e-9


async def test_briefing_all_fetches_fail_returns_failure_no_file_written(
    tmp_path: Path,
) -> None:
    fetch = FetchTool(transport=_handler_factory({url: (500, "boom") for url in URLS}))
    synth, fake = _synthesize(
        _structured([{"value": "unused", "source_indices": [0], "confidence": "grounded"}])
    )
    out_path = tmp_path / "briefing.md"

    result = await run_one(
        MorningBriefingCommission(fetch=fetch, synthesize=synth),
        MorningBriefingInput(urls=URLS, output_path=out_path),
        budget_usd=0.50,
    )

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "All 3 fetches failed" in result.error.detail
    assert len(fake.completions.calls) == 0
    assert not out_path.exists()


async def test_briefing_synthesize_failure_propagates_error_kind(
    tmp_path: Path,
) -> None:
    fetch = FetchTool(
        transport=_handler_factory({url: (200, f"body-{i}") for i, url in enumerate(URLS)})
    )
    # Malformed JSON triggers Synthesize's structured-parse failure → kind="internal".
    synth, _fake = _synthesize("not valid json at all")
    out_path = tmp_path / "briefing.md"

    result = await run_one(
        MorningBriefingCommission(fetch=fetch, synthesize=synth),
        MorningBriefingInput(urls=URLS, output_path=out_path),
        budget_usd=0.50,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert result.error.detail.startswith("Synthesize failed:")
    assert not out_path.exists()


async def test_briefing_unwritable_path_fails_with_real_cost(tmp_path: Path) -> None:
    # The write is guarded like WriteTool: an unwritable output path is a
    # structured failure that still reports the fetch + synthesis spend,
    # not a raise that the dispatch backstop reports as $0.
    fetch = FetchTool(
        transport=_handler_factory({url: (200, f"body-{i}") for i, url in enumerate(URLS)})
    )
    synth, _fake = _synthesize(
        _structured([{"value": "A", "source_indices": [0], "confidence": "grounded"}])
    )
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed", encoding="utf-8")
    out_path = blocker / "briefing.md"  # parent is a file → OSError on mkdir

    result = await run_one(
        MorningBriefingCommission(fetch=fetch, synthesize=synth),
        MorningBriefingInput(urls=URLS, output_path=out_path),
        budget_usd=0.50,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert str(out_path) in result.error.detail
    assert result.cost.estimated_usd > 0.0  # synthesis spend not lost


async def test_briefing_empty_urls_returns_validation_failure(tmp_path: Path) -> None:
    # Pass a fake synth so default construction doesn't hit AsyncOpenAI's
    # credential check; the validation path runs before any sub-invoke anyway.
    synth, _fake = _synthesize(_structured([]))
    result = await run_one(
        MorningBriefingCommission(synthesize=synth),
        MorningBriefingInput(urls=[], output_path=tmp_path / "b.md"),
        budget_usd=0.10,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert result.error.retryable is False


async def test_briefing_emits_progress_events_around_each_phase(tmp_path: Path) -> None:
    fetch = FetchTool(
        transport=_handler_factory({url: (200, f"body-{i}") for i, url in enumerate(URLS)})
    )
    synth, _fake = _synthesize(
        _structured([{"value": "A", "source_indices": [0], "confidence": "grounded"}])
    )
    out_path = tmp_path / "briefing.md"
    events: list[ProgressEvent] = []

    result = await MorningBriefingCommission(fetch=fetch, synthesize=synth).invoke(
        MorningBriefingInput(urls=URLS, output_path=out_path),
        CallContext(budget_usd=0.50, on_progress=events.append),
    )

    assert result.status == "success"
    coordinator_phases = [e.phase for e in events if e.commission_name == "morning_briefing"]
    assert coordinator_phases == ["fetching", "synthesizing", "written"]
    # Synthesize's own events also bubble up via the shared on_progress callback.
    assert any(e.commission_name == "synthesize" and e.phase == "synthesis_pass" for e in events)


async def test_briefing_cancellation_before_fetches_returns_cancelled(
    tmp_path: Path,
) -> None:
    fetch = FetchTool(transport=_handler_factory({url: (200, "body") for url in URLS}))
    synth, fake = _synthesize(
        _structured([{"value": "unused", "source_indices": [0], "confidence": "grounded"}])
    )
    commission = MorningBriefingCommission(fetch=fetch, synthesize=synth)

    result = await commission.invoke(
        MorningBriefingInput(urls=URLS, output_path=tmp_path / "b.md"),
        CallContext(cancel=AlwaysCancelled(), budget_usd=0.50),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert len(fake.completions.calls) == 0


def test_morning_briefing_toolbox_lists_fetch_and_synthesize() -> None:
    fetch = FetchTool()
    synth = SynthesizeCommission(client=cast(AsyncOpenAI, FakeClient([])))
    mb = MorningBriefingCommission(fetch=fetch, synthesize=synth)

    assert mb.toolbox == (fetch, synth)
