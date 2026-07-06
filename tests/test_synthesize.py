"""Tests for the Synthesize Commission.

Tests inject a fake AsyncOpenAI-shaped client via the constructor. The fake
records every call so we can assert that the size gate skips the LLM and that
cancellation lands before any network work.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from openai import AsyncOpenAI

from vibrantine.contract import CallContext, ProgressEvent, Provenance
from vibrantine.examples.synthesize import (
    SynthesisSource,
    SynthesizeCommission,
    SynthesizeInput,
)
from vibrantine.testing import AlwaysCancelled, ScriptedLLM, llm_response


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
    model: str = "google/gemini-3-flash-preview",  # stable fixture for pricing math
) -> tuple[SynthesizeCommission, ScriptedLLM]:
    fake = ScriptedLLM(responses)
    commission = SynthesizeCommission(
        client=cast(AsyncOpenAI, fake),
        max_input_tokens=max_input_tokens,
        model=model,
    )
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
    commission, _fake = _commission(
        [llm_response(content="Both sources agree X."), llm_response(content=_VALID_STRUCTURED)]
    )

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.summary_text == "Both sources confirm X."
    assert len(result.output.claims) >= 1
    assert result.output.claims[0].value == "X is true"


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
    commission, _fake = _commission(
        [llm_response(content="free"), llm_response(content=structured)]
    )

    result = await commission.invoke(SynthesizeInput(sources=sources), CallContext())

    assert result.status == "success"
    assert result.output is not None
    input_uris = {s.provenance.source for s in sources}
    for claim in result.output.claims:
        assert len(claim.sources) >= 1
        for prov in claim.sources:
            assert prov.source in input_uris


async def test_synthesize_cost_reflects_token_usage_and_pricing() -> None:
    # google/gemini-3-flash-preview: $0.50/M input, $3.00/M output.
    commission, _fake = _commission(
        [
            llm_response(content="free", in_tokens=1000, out_tokens=200),
            llm_response(content=_VALID_STRUCTURED, in_tokens=500, out_tokens=100),
        ]
    )

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(),
    )

    assert result.status == "success"
    expected = (1500 * 0.50 + 300 * 3.00) / 1_000_000
    assert result.cost.estimated_usd > 0
    assert abs(result.cost.estimated_usd - expected) < 1e-9


async def test_synthesize_cancellation_before_llm_call_makes_no_call() -> None:
    commission, fake = _commission([llm_response(content="unused"), llm_response(content="unused")])

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0)]),
        CallContext(cancel=AlwaysCancelled()),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert result.error.retryable is False
    assert len(fake.calls) == 0


async def test_synthesize_emits_progress_events_for_each_phase() -> None:
    events: list[ProgressEvent] = []
    commission, _fake = _commission(
        [llm_response(content="free"), llm_response(content=_VALID_STRUCTURED)]
    )

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(on_progress=events.append),
    )

    assert result.status == "success"
    phases = [e.phase for e in events]
    assert phases == ["synthesis_pass", "structured_pass"]
    assert all(e.commission_name == "synthesize" for e in events)
    assert events[0].detail == "2 sources"


async def test_synthesize_budget_too_small_blocks_before_any_llm_call() -> None:
    # Pre-flight: estimated input cost from the prompt alone exceeds $0.0000001.
    commission, fake = _commission([llm_response(content="unused"), llm_response(content="unused")])

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(budget_usd=1e-7),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "budget_exceeded"
    assert result.error.retryable is False
    assert len(fake.calls) == 0
    assert result.cost.estimated_usd == 0.0


async def test_synthesize_budget_exhausted_after_first_call_skips_second() -> None:
    # First call costs (1000 * 0.50 + 200 * 3.00) / 1M = $0.0011.
    # Budget $0.0005 fits the tiny pre-flight estimate but trips after the first call.
    commission, fake = _commission(
        [
            llm_response(content="free", in_tokens=1000, out_tokens=200),
            llm_response(content=_VALID_STRUCTURED, in_tokens=500, out_tokens=100),
        ]
    )

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(budget_usd=0.0005),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "budget_exceeded"
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

    result = await commission.invoke(
        SynthesizeInput(sources=[bulky_source]),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert result.error.retryable is False
    assert len(fake.calls) == 0
    assert result.cost.estimated_usd == 0.0


async def test_synthesize_empty_sources_fails_validation_before_llm() -> None:
    commission, fake = _commission([])

    result = await commission.invoke(SynthesizeInput(sources=[]), CallContext())

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
    commission, _fake = _commission(
        [llm_response(content="free"), llm_response(content=structured)]
    )

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "out of range" in result.error.detail


def test_synthesize_has_empty_toolbox() -> None:
    # A Python coordinator with no sub-Commissions: empty toolbox by default.
    synth = SynthesizeCommission(client=cast(AsyncOpenAI, ScriptedLLM([])))
    assert synth.toolbox == ()


async def test_synthesize_budget_with_unpriced_model_refuses_before_any_call() -> None:
    # budget_usd set + model not in KNOWN_MODELS → fail fast: cost can't be
    # priced, so the budget can't be honored.
    commission, fake = _commission(
        [llm_response(content="unused"), llm_response(content="unused")],
        model="unregistered/model",
    )

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(budget_usd=1.0),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "KNOWN_MODELS" in result.error.detail
    assert len(fake.calls) == 0
    assert result.cost.estimated_usd == 0.0


async def test_synthesize_unpriced_model_without_budget_still_runs() -> None:
    # No budget → an unregistered model runs to completion as before.
    commission, fake = _commission(
        [llm_response(content="free"), llm_response(content=_VALID_STRUCTURED)],
        model="unregistered/model",
    )

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(),
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

    result = await commission.invoke(
        SynthesizeInput(sources=[_src(0), _src(1)]),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "no choices" in result.error.detail
    assert result.cost.estimated_usd > 0
    assert len(fake.calls) == 1
