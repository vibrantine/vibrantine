"""NewsDigest Commission: fetch one news field's sources and digest the survivors.

A Python-coordinator Commission (the author decides the control flow): fan
out fetches over the configured sources in parallel, hand the survivors to
Synthesize, return a cited digest for one field of news. One class serves
every field; international, local, and tech digests are three *instances*
configured at construction with their own field label and source list. The
sources are capacity, like subscriptions; the input carries only the
briefing date.

Instances share the class-level `name` ("news_digest") because identity is
class-level in the contract; it is the parent coordinator that labels which
section is which.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import (
    CallContext,
    Claim,
    Commission,
    CommissionResult,
    CommissionStatus,
    CostMetrics,
    ErrorKind,
    ErrorState,
    Provenance,
)
from vibrantine.dispatch import dispatch
from vibrantine.examples.synthesize import (
    SynthesisSource,
    SynthesizeCommission,
    SynthesizeInput,
)
from vibrantine.tools.fetch import FetchInput, FetchOutput, FetchTool


class NewsDigestInput(BaseModel):
    """Inputs for one digest run."""

    briefing_date: str = Field(
        description="Human-readable date of the briefing, e.g. 'Sunday 05 July 2026'.",
    )


class NewsDigestOutput(BaseModel):
    """One field's digest: summary prose plus the cited claims behind it."""

    field: str = Field(description="The news field this digest covers, e.g. 'international'.")
    summary_text: str = Field(description="Neutral prose digest of the field's sources.")
    claims: list[Claim[str]] = Field(
        description="Load-bearing assertions, each carrying supporting provenance.",
    )
    failed_sources: list[str] = Field(
        default_factory=list,
        description="Source URLs whose fetch failed; included in partial results.",
    )


class NewsDigestCommission(Commission[NewsDigestInput, NewsDigestOutput]):
    """Digest one field of news from configured sources: fetch, synthesize, cite."""

    name: ClassVar[str] = "news_digest"
    description: ClassVar[str] = (
        "Digest one field of news (e.g. international, local, tech) from the "
        "instance's configured sources into a cited summary.\n"
        "\n"
        "Usage:\n"
        "- Call this with the `briefing_date`; the instance fetches its "
        "configured sources in parallel and synthesizes the ones that load.\n"
        "- A partial result is normal: if some sources fail, the digest "
        "covers the rest and lists the failures in `failed_sources`.\n"
        "\n"
        "Returns the `field` label, `summary_text`, `claims` (cited "
        "assertions), and any `failed_sources`."
    )
    input_type: ClassVar[type] = NewsDigestInput
    output_type: ClassVar[type] = NewsDigestOutput

    def __init__(
        self,
        *,
        field: str,
        sources: Sequence[str],
        fetch: FetchTool | None = None,
        synthesize: SynthesizeCommission | None = None,
    ) -> None:
        if not field.strip():
            raise ValueError("news_digest requires a non-empty field label at construction.")
        if not sources:
            raise ValueError("news_digest requires at least one source URL at construction.")
        resolved_fetch = fetch or FetchTool()
        resolved_synthesize = synthesize or SynthesizeCommission()
        super().__init__(toolbox=(resolved_fetch, resolved_synthesize))
        self.field = field
        self._sources = tuple(sources)
        self._fetch = resolved_fetch
        self._synthesize = resolved_synthesize

    async def invoke(
        self,
        input: NewsDigestInput,
        ctx: CallContext,
    ) -> CommissionResult[NewsDigestOutput]:
        provenance = Provenance(
            source=f"news_digest:{self.field}",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        )

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled before fetches were dispatched.",
                retryable=False,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=0.0),
            )

        per_fetch_budget = (
            ctx.budget_usd / len(self._sources) if ctx.budget_usd is not None else None
        )
        fetch_ctx = replace(ctx, budget_usd=per_fetch_budget)

        self._emit(ctx, "fetching", f"{self.field}: {len(self._sources)} sources")
        fetch_results: list[CommissionResult[FetchOutput]] = await asyncio.gather(
            *(dispatch(self._fetch, FetchInput(url=url), fetch_ctx) for url in self._sources)
        )
        fetch_cost = sum(r.cost.estimated_usd for r in fetch_results)

        failed_sources: list[str] = [
            url
            for url, r in zip(self._sources, fetch_results, strict=True)
            if r.status != "success" or r.output is None
        ]
        survivors: list[SynthesisSource] = [
            SynthesisSource(content=r.output.content, provenance=r.provenance)
            for r in fetch_results
            if r.status == "success" and r.output is not None
        ]

        if not survivors:
            return self._fail(
                "internal",
                f"All {len(self._sources)} {self.field} sources failed to fetch.",
                retryable=True,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=fetch_cost),
            )

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled between fetches and synthesis.",
                retryable=False,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=fetch_cost),
            )

        remaining_budget = (
            max(ctx.budget_usd - fetch_cost, 0.0) if ctx.budget_usd is not None else None
        )
        synth_ctx = replace(ctx, budget_usd=remaining_budget)
        self._emit(ctx, "synthesizing", f"{self.field}: {len(survivors)} survivors")
        synth_result = await dispatch(
            self._synthesize,
            SynthesizeInput(
                sources=survivors,
                focus=(
                    f"The {self.field} news stories of {input.briefing_date}: "
                    f"what happened and why it matters."
                ),
            ),
            synth_ctx,
        )
        total_cost = CostMetrics(estimated_usd=fetch_cost + synth_result.cost.estimated_usd)

        if synth_result.status != "success" or synth_result.output is None:
            inner = synth_result.error
            kind: ErrorKind = inner.kind if inner is not None else "internal"
            detail = (
                f"Synthesize failed: {inner.detail}" if inner is not None else "Synthesize failed."
            )
            retryable = inner.retryable if inner is not None else True
            return self._fail(
                kind,
                detail,
                retryable=retryable,
                provenance=provenance,
                cost=total_cost,
            )

        output = NewsDigestOutput(
            field=self.field,
            summary_text=synth_result.output.summary_text,
            claims=synth_result.output.claims,
            failed_sources=failed_sources,
        )
        status: CommissionStatus = "partial" if failed_sources else "success"
        error: ErrorState | None = (
            ErrorState(
                kind="internal",
                detail=(
                    f"{len(failed_sources)} of {len(self._sources)} {self.field} "
                    f"fetches failed: {', '.join(failed_sources)}"
                ),
                retryable=True,
            )
            if failed_sources
            else None
        )

        return CommissionResult[NewsDigestOutput](
            status=status,
            output=output,
            error=error,
            provenance=provenance,
            cost=total_cost,
        )
