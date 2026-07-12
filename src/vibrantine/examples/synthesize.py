"""Synthesize Commission: aggregate source payloads into a typed summary with claims.

Two-step LLM flow: a free-form synthesis pass produces neutral prose; a
structured-output pass converts that prose into typed claims. The LLM emits
source indices, not full provenances; provenances are re-attached on the way
out so the call's output stays grounded in its inputs.

The model is a name resolved against the run's catalog (None means the run
default), and both passes go through the run's provider door like the
default loop's calls. Tests script the model through the catalog
(`testing.scripted_model`).
"""

import json
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from pydantic import BaseModel, Field, ValidationError

from vibrantine._gatekeeper import Gatekeeper, RunCancel, RunHaltedError, UnmeterableCallError
from vibrantine.contract import (
    CallContext,
    Claim,
    Commission,
    CommissionResult,
    ConfidenceLevel,
    CostMetrics,
    ErrorState,
    Provenance,
    estimate_tokens,
)
from vibrantine.dispatch import deposit_llm_trace, halt_checkpoint_error, stop_signal_error
from vibrantine.models import Model, UnknownModelError

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

    async def _run(
        self,
        input: SynthesizeInput,
        ctx: CallContext,
    ) -> CommissionResult[SynthesizeOutput]:
        zero_cost = CostMetrics(estimated_usd=0.0)

        # Resolve the model choice against the run's catalog up front: the
        # custom path mirrors the default loop (an unknown name is a loud
        # validation failure, and provenance, pricing, and the size gate all
        # hang off the entry).
        gatekeeper = ctx._gatekeeper  # pyright: ignore[reportPrivateUsage]
        assert gatekeeper is not None, "synthesize runs inside a run; dispatch refuses outside one"
        try:
            entry = gatekeeper.resolve_model(self._model)
        except UnknownModelError as exc:
            return self._fail(
                "validation",
                str(exc),
                retryable=False,
                provenance=Provenance(
                    source=f"synthesize:{self._model}",
                    fetched_at=datetime.now(UTC),
                    confidence=_overall_confidence(input.sources),
                ),
                cost=zero_cost,
            )

        provenance = Provenance(
            source=f"synthesize:{entry.id}",
            fetched_at=datetime.now(UTC),
            confidence=_overall_confidence(input.sources),
        )

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

        budget_failure = self._budget_unenforceable_failure(ctx, provenance, entry)
        if budget_failure is not None:
            return budget_failure

        user_prompt = _format_user_prompt(input)
        estimated_tokens = estimate_tokens(
            _SYNTHESIS_SYSTEM_PROMPT + _STRUCTURED_SYSTEM_PROMPT + user_prompt,
        )
        if not self._fits_cap(estimated_tokens, self._effective_input_cap(entry)):
            return self._fail(
                "validation",
                f"Estimated input of {estimated_tokens} tokens exceeds the size gate "
                f"for model {entry.id}.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        # Pre-flight budget check using estimated input cost. Cheap lower bound;
        # the post-call check below enforces against actual cost.
        estimated_in_cost = entry.cost_usd(estimated_tokens, 0)
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
            self._cost(in_tokens, out_tokens, entry),
            ctx,
            entry,
            gatekeeper,
            json_mode=False,
        )
        if first_err is not None:
            return first_err
        in_tokens += first_in
        out_tokens += first_out

        if ctx.cancel.is_cancelled:
            # Attributed at the source, like the default loop's checkpoint:
            # a breaker-caused exit carries the stamp the root rewrite
            # claims; a caller cancellation keeps its plain story.
            breaker_only = isinstance(ctx.cancel, RunCancel) and not ctx.cancel.caller_cancelled
            error = (
                halt_checkpoint_error("between synthesis and structured-output passes")
                if breaker_only
                else ErrorState(
                    kind="cancelled",
                    detail="Cancelled between synthesis and structured-output passes.",
                    retryable=False,
                )
            )
            return cast(
                "CommissionResult[SynthesizeOutput]",
                CommissionResult(
                    status="failure",
                    error=error,
                    provenance=provenance,
                    cost=self._cost(in_tokens, out_tokens, entry),
                ),
            )

        # Post-first-call budget check against real usage.
        cost_so_far = self._cost(in_tokens, out_tokens, entry)
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
            self._cost(in_tokens, out_tokens, entry),
            ctx,
            entry,
            gatekeeper,
            json_mode=True,
        )
        if second_err is not None:
            return second_err
        in_tokens += second_in
        out_tokens += second_out

        # Post-second-call check, mirroring the framework loop's post-turn
        # rule: overshoot is reported as budget_exceeded, never returned as
        # a quiet success costing more than the grant.
        cost_after_structured = self._cost(in_tokens, out_tokens, entry)
        if ctx.budget_usd is not None and cost_after_structured.estimated_usd > ctx.budget_usd:
            return self._fail(
                "budget_exceeded",
                f"Cost ${cost_after_structured.estimated_usd:.6f} after "
                f"structured pass exceeds budget ${ctx.budget_usd:.6f}.",
                retryable=False,
                provenance=provenance,
                cost=cost_after_structured,
            )

        try:
            raw = _SynthesizeRaw.model_validate_json(second_text)
        except (json.JSONDecodeError, ValidationError) as exc:
            return self._fail(
                "internal",
                f"Structured output failed to parse: {exc}",
                retryable=True,
                provenance=provenance,
                cost=self._cost(in_tokens, out_tokens, entry),
            )

        try:
            claims = _resolve_claims(raw.claims, input.sources)
        except IndexError as exc:
            return self._fail(
                "internal",
                f"Claim failed to ground in its sources: {exc}",
                retryable=True,
                provenance=provenance,
                cost=self._cost(in_tokens, out_tokens, entry),
            )

        return CommissionResult[SynthesizeOutput](
            status="success",
            output=SynthesizeOutput(summary_text=raw.summary_text, claims=claims),
            provenance=provenance,
            cost=self._cost(in_tokens, out_tokens, entry),
        )

    async def _call(
        self,
        messages: list[ChatCompletionMessageParam],
        provenance: Provenance,
        cost_so_far: CostMetrics,
        ctx: CallContext,
        entry: Model,
        gatekeeper: Gatekeeper,
        *,
        json_mode: bool,
    ) -> tuple[str, int, int, CommissionResult[SynthesizeOutput] | None]:
        # Each pass deposits its transcript (outgoing messages plus the
        # assistant reply, when one arrived) on every exit path; the trace
        # mailbox concatenates the two passes in run order, so the record
        # carries the whole conversation even for failed runs.
        reply: list[dict[str, Any]] = []
        try:
            return await self._call_inner(
                messages,
                provenance,
                cost_so_far,
                ctx,
                entry,
                gatekeeper,
                reply,
                json_mode=json_mode,
            )
        finally:
            deposit_llm_trace([*cast("list[dict[str, Any]]", messages), *reply])

    async def _call_inner(
        self,
        messages: list[ChatCompletionMessageParam],
        provenance: Provenance,
        cost_so_far: CostMetrics,
        ctx: CallContext,
        entry: Model,
        gatekeeper: Gatekeeper,
        reply: list[dict[str, Any]],
        *,
        json_mode: bool,
    ) -> tuple[str, int, int, CommissionResult[SynthesizeOutput] | None]:
        # A custom _run's own LLM calls pass the run's provider door like the
        # default loop's: the fuses, the room, the client vending, and the
        # call log govern the whole tree or they govern nothing. The
        # gatekeeper is _run's one fetch, passed down.
        try:
            async with gatekeeper.provider_call(
                entry=entry,
                commission_name=self.name,
                grant_usd=ctx.budget_usd,
            ) as ticket:
                if json_mode:
                    response: ChatCompletion = await ticket.client.chat.completions.create(
                        model=entry.id,
                        messages=messages,
                        response_format={"type": "json_object"},
                    )
                else:
                    response = await ticket.client.chat.completions.create(
                        model=entry.id,
                        messages=messages,
                    )
                if response.usage is not None:
                    ticket.record_usage(
                        response.usage.prompt_tokens,
                        response.usage.completion_tokens,
                    )
        except RunHaltedError as exc:
            # Caught here rather than left to dispatch's backstop so the
            # cost already spent on prior passes stays on the envelope. The
            # shared builder keeps the vocabulary (and the breaker-born
            # stamp the root rewrite claims) identical to the loop's.
            return (
                "",
                0,
                0,
                cast(
                    "CommissionResult[SynthesizeOutput]",
                    CommissionResult(
                        status="failure",
                        error=stop_signal_error(exc),
                        provenance=provenance,
                        cost=cost_so_far,
                    ),
                ),
            )
        except UnmeterableCallError as exc:
            return (
                "",
                0,
                0,
                self._fail(
                    "internal",
                    str(exc),
                    retryable=False,
                    provenance=provenance,
                    cost=cost_so_far,
                ),
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
                + self._cost(in_tokens, out_tokens, entry).estimated_usd,
                in_tokens=(cost_so_far.in_tokens or 0) + in_tokens,
                out_tokens=(cost_so_far.out_tokens or 0) + out_tokens,
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
        reply.append({"role": "assistant", "content": content})
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
        # The structured prompt demands a non-empty index array; an empty one
        # means the structured pass mis-emitted. Failing (retryable, same as
        # out-of-range below) beats silently dropping the claim.
        if not rc.source_indices:
            raise IndexError(f"claim {rc.value!r} cited no source indices")
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
