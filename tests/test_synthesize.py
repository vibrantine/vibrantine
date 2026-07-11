"""Tests for the Synthesize Commission.

Tests register a ScriptedLLM fake as the run's model catalog entry
(`scripted_model`). The fake records every call so we can assert that the
size gate skips the LLM and that cancellation lands before any network work.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from vibrantine import run_one
from vibrantine.contract import ProgressEvent, Provenance
from vibrantine.examples.synthesize import (
    SynthesisSource,
    SynthesizeCommission,
    SynthesizeInput,
)
from vibrantine.persistence import FilesystemBackend
from vibrantine.testing import AlwaysCancelled, ScriptedLLM, llm_response, scripted_model


def _src(idx: int, content: str = "fact") -> SynthesisSource:
    return SynthesisSource(
        content=content,
        provenance=Provenance(
            source=f"https://example.test/{idx}",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        ),
    )


def _commission(
    responses: list[SimpleNamespace],
    *,
    max_input_tokens: int | None = None,
) -> tuple[SynthesizeCommission, ScriptedLLM]:
    fake = ScriptedLLM(responses)
    commission = SynthesizeCommission(max_input_tokens=max_input_tokens)
    return commission, fake


_VALID_STRUCTURED = json.dumps(
    {
        "summary_text": "Both sources confirm X.",
        "claims": [
            {"value": "X is true", "source_indices": [0, 1], "confidence": "grounded"},
        ],
    }
)


async def test_synthesize_success_returns_at_least_one_claim() -> None:
    commission, fake = _commission(
        [llm_response(content="Both sources agree X."), llm_response(content=_VALID_STRUCTURED)]
    )

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.summary_text == "Both sources confirm X."
    assert len(result.output.claims) >= 1
    assert result.output.claims[0].value == "X is true"


async def test_synthesize_record_carries_both_pass_transcripts(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    commission, fake = _commission(
        [llm_response(content="Both sources agree X."), llm_response(content=_VALID_STRUCTURED)]
    )

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
        backend=backend,
        record="always",
    )

    assert result.run_id is not None
    record = await backend.load(result.run_id)
    assert record is not None and record.llm_trace is not None
    # Two passes, each system + user + assistant, concatenated in run order.
    assert [m["role"] for m in record.llm_trace] == [
        "system",
        "user",
        "assistant",
        "system",
        "user",
        "assistant",
    ]
    assert record.llm_trace[2]["content"] == "Both sources agree X."
    assert record.llm_trace[5]["content"] == _VALID_STRUCTURED


async def test_synthesize_failed_run_still_carries_transcripts(tmp_path: Path) -> None:
    # A structured pass that emits junk fails the run; the record still holds
    # both conversations, which is the autopsy the trace exists for.
    backend = FilesystemBackend(tmp_path)
    commission, fake = _commission([llm_response(content="free"), llm_response(content="not json")])

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0)]),
        models=[scripted_model(fake)],
        backend=backend,
        record="always",
    )

    assert result.status == "failure"
    assert result.run_id is not None
    record = await backend.load(result.run_id)
    assert record is not None and record.llm_trace is not None
    assert len(record.llm_trace) == 6
    assert record.llm_trace[5]["content"] == "not json"


async def test_synthesize_claim_sources_are_subset_of_input_provenances() -> None:
    structured = json.dumps(
        {
            "summary_text": "Two claims.",
            "claims": [
                {"value": "A", "source_indices": [0], "confidence": "grounded"},
                {"value": "B", "source_indices": [1, 2], "confidence": "speculative"},
            ],
        }
    )
    sources = [_src(0), _src(1), _src(2)]
    commission, fake = _commission([llm_response(content="free"), llm_response(content=structured)])

    result = await run_one(
        commission, SynthesizeInput(sources=sources), models=[scripted_model(fake)]
    )

    assert result.status == "success"
    assert result.output is not None
    input_uris = {s.provenance.source for s in sources}
    for claim in result.output.claims:
        assert len(claim.sources) >= 1
        for prov in claim.sources:
            assert prov.source in input_uris


async def test_synthesize_cost_reflects_token_usage_and_pricing() -> None:
    # The scripted entry keeps FIXTURE_MODEL's pricing: $0.50/M in, $3.00/M out.
    commission, fake = _commission(
        [
            llm_response(content="free", in_tokens=1000, out_tokens=200),
            llm_response(content=_VALID_STRUCTURED, in_tokens=500, out_tokens=100),
        ]
    )

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
    )

    assert result.status == "success"
    expected = (1500 * 0.50 + 300 * 3.00) / 1_000_000
    assert result.cost.estimated_usd > 0
    assert abs(result.cost.estimated_usd - expected) < 1e-9
    # Raw counts survive pricing: both passes summed.
    assert result.cost.in_tokens == 1500
    assert result.cost.out_tokens == 300


async def test_synthesize_cancellation_before_llm_call_makes_no_call() -> None:
    commission, fake = _commission([llm_response(content="unused"), llm_response(content="unused")])

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0)]),
        models=[scripted_model(fake)],
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert result.error.retryable is False
    assert len(fake.calls) == 0


async def test_synthesize_emits_progress_events_for_each_phase() -> None:
    events: list[ProgressEvent] = []
    commission, fake = _commission(
        [llm_response(content="free"), llm_response(content=_VALID_STRUCTURED)]
    )

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
        on_progress=events.append,
    )

    assert result.status == "success"
    phases = [e.phase for e in events]
    assert phases == ["synthesis_pass", "structured_pass"]
    assert all(e.commission_name == "synthesize" for e in events)
    assert events[0].detail == "2 sources"


async def test_synthesize_budget_too_small_blocks_before_any_llm_call() -> None:
    # Pre-flight: estimated input cost from the prompt alone exceeds $0.0000001.
    commission, fake = _commission([llm_response(content="unused"), llm_response(content="unused")])

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
        budget_usd=1e-7,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "budget_exceeded"
    assert result.error.retryable is False
    assert len(fake.calls) == 0
    assert result.cost.estimated_usd == 0.0


async def test_synthesize_budget_exhausted_after_first_call_skips_second() -> None:
    # First call costs (1000 * 0.50 + 200 * 3.00) / 1M = $0.0011.
    # Budget $0.0005 fits the tiny pre-flight estimate; real spend then
    # settles past the grant, so the spend fuse trips and the root reports
    # run_halted.
    commission, fake = _commission(
        [
            llm_response(content="free", in_tokens=1000, out_tokens=200),
            llm_response(content=_VALID_STRUCTURED, in_tokens=500, out_tokens=100),
        ]
    )

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
        budget_usd=0.0005,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "spend fuse tripped" in result.error.detail
    assert len(fake.calls) == 1
    # Cost still reflects what we actually spent on the first call.
    assert result.cost.estimated_usd > 0


async def test_synthesize_oversized_input_fails_validation_with_no_llm_call() -> None:
    # max_input_tokens=10 with target_input_fraction=0.75 → input fits only if
    # estimated tokens <= 7. The padded source below estimates well above that.
    commission, fake = _commission(
        [llm_response(content="unused"), llm_response(content="unused")],
        max_input_tokens=10,
    )
    bulky_source = _src(0, content="lorem ipsum dolor sit amet " * 50)

    result = await run_one(
        commission,
        SynthesizeInput(sources=[bulky_source]),
        models=[scripted_model(fake)],
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert result.error.retryable is False
    assert len(fake.calls) == 0
    assert result.cost.estimated_usd == 0.0


async def test_synthesize_empty_sources_fails_validation_before_llm() -> None:
    commission, fake = _commission([])

    result = await run_one(commission, SynthesizeInput(sources=[]), models=[scripted_model(fake)])

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert result.error.retryable is False
    assert len(fake.calls) == 0


async def test_synthesize_negative_source_index_is_rejected() -> None:
    # A model-emitted -1 is a valid Python index; without the explicit bounds
    # check it would silently attach the *last* source's provenance.
    structured = json.dumps(
        {
            "summary_text": "One claim.",
            "claims": [{"value": "A", "source_indices": [-1], "confidence": "grounded"}],
        }
    )
    commission, fake = _commission([llm_response(content="free"), llm_response(content=structured)])

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "outside 0..1" in result.error.detail


async def test_synthesize_claim_with_no_source_indices_is_rejected() -> None:
    # The structured prompt demands a non-empty index array per claim; an
    # empty one is an LLM mis-emission and must fail loudly (retryable), not
    # silently drop the claim.
    structured = json.dumps(
        {
            "summary_text": "One claim.",
            "claims": [{"value": "A", "source_indices": [], "confidence": "grounded"}],
        }
    )
    commission, fake = _commission([llm_response(content="free"), llm_response(content=structured)])

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert result.error.retryable is True
    assert "cited no source indices" in result.error.detail


def test_synthesize_has_empty_toolbox() -> None:
    # A Python coordinator with no sub-Commissions: empty toolbox by default.
    synth = SynthesizeCommission()
    assert synth.toolbox == ()


async def test_synthesize_budget_with_unpriced_model_refuses_before_any_call() -> None:
    # budget_usd set + a catalog entry registered without USD rates → fail
    # fast: cost can't be priced, so the budget can't be honored.
    commission, fake = _commission(
        [llm_response(content="unused"), llm_response(content="unused")],
    )

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake, input_usd_per_million=None, output_usd_per_million=None)],
        budget_usd=1.0,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "USD rates" in result.error.detail
    assert len(fake.calls) == 0
    assert result.cost.estimated_usd == 0.0


async def test_synthesize_unpriced_model_without_budget_still_runs() -> None:
    # No budget → an unpriced catalog entry runs to completion as before.
    commission, fake = _commission(
        [llm_response(content="free"), llm_response(content=_VALID_STRUCTURED)],
    )

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake, input_usd_per_million=None, output_usd_per_million=None)],
    )

    assert result.status == "success", result.error
    assert len(fake.calls) == 2


async def test_synthesize_empty_provider_choices_fail_as_value() -> None:
    commission, fake = _commission(
        [
            SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
                choices=[],
            )
        ]
    )

    result = await run_one(
        commission,
        SynthesizeInput(sources=[_src(0), _src(1)]),
        models=[scripted_model(fake)],
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "no choices" in result.error.detail
    assert result.cost.estimated_usd > 0
    assert len(fake.calls) == 1
