"""MorningBriefing commission: a coordinator that exercises recursive composition.

Fans out Fetches in parallel, hands survivors to Synthesize, writes a markdown
briefing to disk. Sits behind the same contract as a worker (typed
input/output, errors-as-values, structural cost rollup), so it can itself be
invoked by another coordinator.

File-writing is a deliberate side effect inside the contract: the typed
`MorningBriefingOutput` is the attestation that the write happened.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.commissions.synthesize import (
    SynthesisSource,
    SynthesizeCommission,
    SynthesizeInput,
    SynthesizeOutput,
)
from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    CommissionStatus,
    CostMetrics,
    ErrorKind,
    ErrorState,
    Provenance,
)
from vibrantine.dispatch import dispatch
from vibrantine.tools.fetch import (
    FetchInput,
    FetchOutput,
    FetchTool,
)


class MorningBriefingInput(BaseModel):
    """Inputs for one morning briefing run."""

    urls: list[str] = Field(description="URLs to fetch and synthesize.")
    output_path: Path = Field(description="Filesystem path the markdown briefing is written to.")


class MorningBriefingOutput(BaseModel):
    """Attestation of one briefing: where it was written, what it contains."""

    markdown_path: Path = Field(description="Path of the markdown file written by this call.")
    summary_text: str = Field(description="The synthesized summary that appears in the briefing.")
    claim_count: int = Field(description="Number of load-bearing claims in the briefing.")
    failed_urls: list[str] = Field(
        default_factory=list,
        description="URLs whose fetch failed; included in partial-success results.",
    )


class MorningBriefingCommission(Commission[MorningBriefingInput, MorningBriefingOutput]):
    """Coordinator: fetch URLs, synthesize survivors, write a markdown briefing."""

    name: ClassVar[str] = "morning_briefing"
    description: ClassVar[str] = (
        "Fetch a list of URLs, synthesize the ones that load, and write a "
        "markdown briefing with cited claims to disk.\n"
        "\n"
        "Usage:\n"
        "- Call this with the `urls` to compile and an `output_path` to write "
        "to; it fetches in parallel, synthesizes the survivors, and writes the "
        "briefing file.\n"
        "- A partial result is normal: if some URLs fail, the briefing covers "
        "the rest and the failures are listed in `failed_urls` (status "
        "`partial`).\n"
        "\n"
        "Returns the `markdown_path` written, the `summary_text` it contains, "
        "the `claim_count`, and any `failed_urls`."
    )
    input_type: ClassVar[type] = MorningBriefingInput
    output_type: ClassVar[type] = MorningBriefingOutput

    def __init__(
        self,
        *,
        fetch: FetchTool | None = None,
        synthesize: SynthesizeCommission | None = None,
    ) -> None:
        resolved_fetch = fetch or FetchTool()
        resolved_synthesize = synthesize or SynthesizeCommission()
        super().__init__(toolbox=(resolved_fetch, resolved_synthesize))
        self._fetch = resolved_fetch
        self._synthesize = resolved_synthesize

    async def invoke(
        self,
        input: MorningBriefingInput,
        ctx: CallContext,
    ) -> CommissionResult[MorningBriefingOutput]:
        provenance = Provenance(
            source=f"morning_briefing:{input.output_path}",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        )
        zero_cost = CostMetrics(estimated_usd=0.0)

        if not input.urls:
            return self._fail(
                "validation",
                "morning_briefing requires at least one URL.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled before fetches were dispatched.",
                retryable=False,
                provenance=provenance,
                cost=zero_cost,
            )

        per_fetch_budget = ctx.budget_usd / len(input.urls) if ctx.budget_usd is not None else None
        # `replace` preserves backend / parent_run_id / future ctx fields
        # without re-listing them on every coordinator slice.
        fetch_ctx = replace(ctx, budget_usd=per_fetch_budget)

        self._emit(ctx, "fetching", f"{len(input.urls)} URLs")
        fetch_results: list[CommissionResult[FetchOutput]] = await asyncio.gather(
            *(dispatch(self._fetch, FetchInput(url=url), fetch_ctx) for url in input.urls)
        )
        fetch_cost = sum(r.cost.estimated_usd for r in fetch_results)

        failed_urls: list[str] = [
            url
            for url, r in zip(input.urls, fetch_results, strict=True)
            if r.status != "success" or r.output is None
        ]
        sources: list[SynthesisSource] = [
            SynthesisSource(content=r.output.content, provenance=r.provenance)
            for r in fetch_results
            if r.status == "success" and r.output is not None
        ]

        if not sources:
            return self._fail(
                "internal",
                f"All {len(input.urls)} fetches failed; nothing to synthesize.",
                retryable=True,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=fetch_cost),
            )

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled between fetches and synthesize.",
                retryable=False,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=fetch_cost),
            )

        remaining_budget = (
            max(ctx.budget_usd - fetch_cost, 0.0) if ctx.budget_usd is not None else None
        )
        synth_ctx = replace(ctx, budget_usd=remaining_budget)
        self._emit(ctx, "synthesizing", f"{len(sources)} survivors")
        synth_result = await dispatch(self._synthesize, SynthesizeInput(sources=sources), synth_ctx)
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

        # Guarded like WriteTool: an unwritable path is a structured failure
        # carrying the real cost of the fetches + synthesis, not a raise that
        # the dispatch backstop reports as $0.
        try:
            input.output_path.parent.mkdir(parents=True, exist_ok=True)
            input.output_path.write_text(_render_markdown(synth_result.output), encoding="utf-8")
        except OSError as exc:
            return self._fail(
                "internal",
                f"Briefing was synthesized but writing {input.output_path!s} failed: {exc}.",
                retryable=True,
                provenance=provenance,
                cost=total_cost,
            )
        self._emit(ctx, "written", str(input.output_path))

        output = MorningBriefingOutput(
            markdown_path=input.output_path,
            summary_text=synth_result.output.summary_text,
            claim_count=len(synth_result.output.claims),
            failed_urls=failed_urls,
        )
        status: CommissionStatus = "partial" if failed_urls else "success"
        error: ErrorState | None = (
            ErrorState(
                kind="internal",
                detail=(
                    f"{len(failed_urls)} of {len(input.urls)} fetches failed: "
                    f"{', '.join(failed_urls)}"
                ),
                retryable=True,
            )
            if failed_urls
            else None
        )

        return CommissionResult[MorningBriefingOutput](
            status=status,
            output=output,
            error=error,
            provenance=provenance,
            cost=total_cost,
        )


def _render_markdown(output: SynthesizeOutput) -> str:
    """Render SynthesizeOutput as briefing markdown with footnoted citations."""
    sources: list[Provenance] = []
    source_index: dict[str, int] = {}
    for claim in output.claims:
        for prov in claim.sources:
            if prov.source not in source_index:
                source_index[prov.source] = len(sources)
                sources.append(prov)

    lines: list[str] = ["# Morning briefing", "", output.summary_text, ""]

    if output.claims:
        lines.extend(["## Claims", ""])
        for claim in output.claims:
            refs = ", ".join(str(source_index[p.source] + 1) for p in claim.sources)
            lines.append(f"- {claim.value} [{refs}]")
        lines.append("")

    if sources:
        lines.extend(["## Sources", ""])
        for i, prov in enumerate(sources, start=1):
            lines.append(f"{i}. {prov.source}")
        lines.append("")

    return "\n".join(lines)
