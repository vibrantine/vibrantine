"""Synthesize Commission: aggregate source payloads into a typed summary with claims.

Two-step LLM flow: a free-form synthesis pass produces neutral prose; a
structured-output pass converts that prose into typed claims. The LLM emits
source indices, not full provenances; provenances are re-attached on the way
out so the call's output stays grounded in its inputs.

The default model is `google/gemini-3-flash-preview` via OpenRouter, accessed
through the `openai` SDK with `base_url` swapped. Tests inject a fake client
through the constructor's `client` parameter.
"""

import json
from datetime import UTC, datetime
from typing import ClassVar

import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from pydantic import BaseModel, Field, ValidationError

from vibrantine.contract import (
    CallContext,
    Claim,
    Commission,
    CommissionResult,
    ConfidenceLevel,
    CostMetrics,
    Provenance,
    estimate_tokens,
)

_SYNTHESIS_SYSTEM_PROMPT = (
    "You are a research analyst. Read the provided sources and write a concise, "
    "neutral summary that captures the load-bearing facts. Do not invent facts "
    "not supported by the sources. Reference sources by their bracketed index."
)

_STRUCTURED_SYSTEM_PROMPT = (
    "Convert the prior synthesis into structured JSON with two top-level keys: "
    "`summary_text` (string) and `claims` (array). Each claim has `value` (a "
    "short asserted fact), `source_indices` (non-empty array of integer indices "
    "into the provided sources that support the claim), and `confidence` (one of "
    "'verified', 'grounded', 'speculative'). Respond with JSON only."
)


class SynthesisSource(BaseModel):
    """One source payload fed into Synthesize: content plus its provenance."""

    content: str = Field(description="Text content of the source.")
    provenance: Provenance = Field(description="Origin and trust level of the content.")


class SynthesizeInput(BaseModel):
    """Inputs for one synthesis call."""

    sources: list[SynthesisSource] = Field(
        description="Source payloads to synthesize.",
    )
    focus: str | None = Field(
        default=None,
        description="Optional focus question to steer the synthesis.",
    )


class SynthesizeOutput(BaseModel):
    """Structured summary plus the load-bearing claims that support it."""

    summary_text: str = Field(description="Neutral prose summary of the sources.")
    claims: list[Claim[str]] = Field(
        description="Load-bearing assertions with their supporting source provenances.",
    )


class _ClaimRaw(BaseModel):
    """LLM-emitted claim. `source_indices` reference `SynthesizeInput.sources`."""

    value: str = Field(description="Short asserted fact emitted by the LLM.")
    source_indices: list[int] = Field(
        description="Indices into SynthesizeInput.sources that support this claim.",
    )
    confidence: ConfidenceLevel = Field(description="LLM-emitted confidence for this claim.")


class _SynthesizeRaw(BaseModel):
    """LLM-emitted JSON shape for the structured pass."""

    summary_text: str = Field(description="Neutral prose summary emitted by the LLM.")
    claims: list[_ClaimRaw] = Field(description="Claims emitted by the LLM with source indices.")


class SynthesizeCommission(Commission[SynthesizeInput, SynthesizeOutput]):
    """Synthesize multiple source payloads into a structured summary with cited claims."""

    name: ClassVar[str] = "synthesize"
    description: ClassVar[str] = (
        "Aggregate multiple source payloads into one structured summary with "
        "cited claims.\n"
        "\n"
        "Usage:\n"
        "- Call this with two or more `sources` (each text plus its provenance) "
        "to merge into a single neutral summary; pass an optional `focus` to "
        "steer it toward one question.\n"
        "- Use it when you already hold the source texts; it does not fetch. "
        "Gather sources first (e.g. via `fetch`), then synthesize.\n"
        "\n"
        "Returns `summary_text` (neutral prose) and `claims` (load-bearing "
        "assertions, each carrying the provenances of the sources that support "
        "it)."
    )
    input_type: ClassVar[type] = SynthesizeInput
    output_type: ClassVar[type] = SynthesizeOutput

    async def invoke(
        self,
        input: SynthesizeInput,
        ctx: CallContext,
    ) -> CommissionResult[SynthesizeOutput]:
        provenance = Provenance(
            source=f"synthesize:{self._model}",
            fetched_at=datetime.now(UTC),
            confidence=_overall_confidence(input.sources),
        )
        zero_cost = CostMetrics(estimated_usd=0.0)

        if not input.sources:
            return self._fail(
                "validation",
                "synthesize requires at least one source.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled before synthesis began.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        budget_failure = self._budget_unenforceable_failure(ctx, provenance)
        if budget_failure is not None:
            return budget_failure

        user_prompt = _format_user_prompt(input)
        estimated_tokens = estimate_tokens(
            _SYNTHESIS_SYSTEM_PROMPT + _STRUCTURED_SYSTEM_PROMPT + user_prompt,
        )
        if not self.fits(estimated_tokens):
            return self._fail(
                "validation",
                f"Estimated input of {estimated_tokens} tokens exceeds the size gate "
                f"for model {self._model}.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        # Pre-flight budget check using estimated input cost. Cheap lower bound;
        # the post-call check below enforces against actual cost.
        in_price, _out_price = self._prices()
        estimated_in_cost = (estimated_tokens * in_price) / 1_000_000
        if ctx.budget_usd is not None and estimated_in_cost > ctx.budget_usd:
            return self._fail(
                "budget_exceeded",
                f"Estimated input cost ${estimated_in_cost:.6f} exceeds budget "
                f"${ctx.budget_usd:.6f} before any LLM call.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        in_tokens = 0
        out_tokens = 0

        self._emit(ctx, "synthesis_pass", f"{len(input.sources)} sources")
        synth_messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        first_text, first_in, first_out, first_err = await self._call(
            synth_messages,
            provenance,
            self._cost(in_tokens, out_tokens),
            json_mode=False,
        )
        if first_err is not None:
            return first_err
        in_tokens += first_in
        out_tokens += first_out

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled between synthesis and structured-output passes.",
                retryable=False,
                provenance=provenance,
                cost=self._cost(in_tokens, out_tokens),
            )

        # Post-first-call budget check against real usage.
        cost_so_far = self._cost(in_tokens, out_tokens)
        if ctx.budget_usd is not None and cost_so_far.estimated_usd > ctx.budget_usd:
            return self._fail(
                "budget_exceeded",
                f"Cost ${cost_so_far.estimated_usd:.6f} after synthesis pass exceeds "
                f"budget ${ctx.budget_usd:.6f}; structured pass skipped.",
                retryable=False,
                provenance=provenance,
                cost=cost_so_far,
            )

        self._emit(ctx, "structured_pass")
        structured_messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _STRUCTURED_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Prior synthesis:\n{first_text}\n\n"
                    f"Available source indices: 0..{len(input.sources) - 1}"
                ),
            },
        ]
        second_text, second_in, second_out, second_err = await self._call(
            structured_messages,
            provenance,
            self._cost(in_tokens, out_tokens),
            json_mode=True,
        )
        if second_err is not None:
            return second_err
        in_tokens += second_in
        out_tokens += second_out

        try:
            raw = _SynthesizeRaw.model_validate_json(second_text)
        except (json.JSONDecodeError, ValidationError) as exc:
            return self._fail(
                "internal",
                f"Structured output failed to parse: {exc}",
                retryable=True,
                provenance=provenance,
                cost=self._cost(in_tokens, out_tokens),
            )

        try:
            claims = _resolve_claims(raw.claims, input.sources)
        except IndexError as exc:
            return self._fail(
                "internal",
                f"Claim referenced source index out of range: {exc}",
                retryable=True,
                provenance=provenance,
                cost=self._cost(in_tokens, out_tokens),
            )

        return CommissionResult[SynthesizeOutput](
            status="success",
            output=SynthesizeOutput(summary_text=raw.summary_text, claims=claims),
            provenance=provenance,
            cost=self._cost(in_tokens, out_tokens),
        )

    async def _call(
        self,
        messages: list[ChatCompletionMessageParam],
        provenance: Provenance,
        cost_so_far: CostMetrics,
        *,
        json_mode: bool,
    ) -> tuple[str, int, int, CommissionResult[SynthesizeOutput] | None]:
        try:
            if json_mode:
                response: ChatCompletion = await self._resolved_client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
            else:
                response = await self._resolved_client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                )
        except openai.RateLimitError as exc:
            return (
                "",
                0,
                0,
                self._fail(
                    "rate_limit",
                    f"Rate limit from LLM provider: {exc}",
                    retryable=True,
                    provenance=provenance,
                    cost=cost_so_far,
                ),
            )
        except openai.APIError as exc:
            return (
                "",
                0,
                0,
                self._fail(
                    "internal",
                    f"LLM provider error: {exc}",
                    retryable=True,
                    provenance=provenance,
                    cost=cost_so_far,
                ),
            )

        usage = response.usage
        in_tokens = usage.prompt_tokens if usage is not None else 0
        out_tokens = usage.completion_tokens if usage is not None else 0
        if not response.choices:
            cost = CostMetrics(
                estimated_usd=cost_so_far.estimated_usd
                + self._cost(in_tokens, out_tokens).estimated_usd
            )
            return (
                "",
                in_tokens,
                out_tokens,
                self._fail(
                    "internal",
                    "LLM provider returned no choices.",
                    retryable=True,
                    provenance=provenance,
                    cost=cost,
                ),
            )
        content = response.choices[0].message.content or ""
        return content, in_tokens, out_tokens, None


def _overall_confidence(sources: list[SynthesisSource]) -> ConfidenceLevel:
    # Deliberately never "verified": synthesis is an LLM transformation of
    # its sources, so even all-verified inputs yield at-best-grounded output.
    if any(s.provenance.confidence == "speculative" for s in sources):
        return "speculative"
    return "grounded"


def _format_user_prompt(input: SynthesizeInput) -> str:
    parts: list[str] = []
    if input.focus:
        parts.append(f"Focus: {input.focus}")
        parts.append("")
    for idx, source in enumerate(input.sources):
        parts.append(f"--- Source [{idx}] ({source.provenance.source}) ---")
        parts.append(source.content)
        parts.append("")
    return "\n".join(parts)


def _resolve_claims(
    raw_claims: list[_ClaimRaw],
    sources: list[SynthesisSource],
) -> list[Claim[str]]:
    resolved: list[Claim[str]] = []
    for rc in raw_claims:
        if not rc.source_indices:
            continue
        # Explicit bounds check: Python accepts negative indices, so a
        # model-emitted -1 would otherwise silently attach the *last*
        # source's provenance to the claim: a misattribution, not an error.
        for i in rc.source_indices:
            if i < 0 or i >= len(sources):
                raise IndexError(f"source index {i} is outside 0..{len(sources) - 1}")
        provenances = [sources[i].provenance for i in rc.source_indices]
        resolved.append(
            Claim[str](
                value=rc.value,
                sources=provenances,
                confidence=rc.confidence,
            )
        )
    return resolved
