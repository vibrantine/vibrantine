"""MorningBriefing Commission: a heterogeneous coordinator tree behind one contract.

The substantive worked example for the author-decided pole of composition
(RecursiveResearch is its LLM-decided counterpart). A Python coordinator
fans out heterogeneous sections in parallel (one Weather leaf, several
configured NewsDigest coordinators), asks Summarize for an executive
summary over the survivors, renders markdown, and writes it to disk.

Three lessons live here:

- The date header is plain application code, not a Commission or a Tool.
  Not everything wears the jacket; the three-categories rule in one file.
- Partial semantics are the author's explicit choice at every level: a
  failed source makes a section partial, a failed section makes the
  briefing partial, and a failed executive summary degrades the briefing
  rather than killing it. Only a morning with no sections at all fails.
- Budget slices down and cost rolls up across a tree with real depth.

File-writing is a deliberate side effect inside the contract: the typed
`MorningBriefingOutput` is the attestation that the write happened.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel

from vibrantine.contract import (
    CallContext,
    Claim,
    Commission,
    CommissionResult,
    CommissionStatus,
    CostMetrics,
    ErrorState,
    Provenance,
)
from vibrantine.dispatch import dispatch
from vibrantine.examples.morning_briefing.subcommissions.news_digest import (
    NewsDigestCommission,
    NewsDigestInput,
    NewsDigestOutput,
)
from vibrantine.examples.morning_briefing.subcommissions.weather import (
    WeatherCommission,
    WeatherInput,
    WeatherOutput,
)
from vibrantine.examples.morning_briefing.types import (
    MorningBriefingInput,
    MorningBriefingOutput,
    SectionReport,
)
from vibrantine.examples.summarize import SummarizeCommission, SummarizeInput


@dataclass
class _RenderSection:
    """Internal render payload: richer than SectionReport, never crosses the boundary."""

    title: str
    summary_text: str
    claims: list[Claim[str]]


class MorningBriefingCommission(Commission[MorningBriefingInput, MorningBriefingOutput]):
    """Coordinate weather, news digests, and an executive summary into one briefing."""

    name: ClassVar[str] = "morning_briefing"
    description: ClassVar[str] = (
        "Compile a morning briefing from configured sections (weather plus "
        "news digests), open it with an executive summary, and write it to "
        "disk as markdown with cited claims.\n"
        "\n"
        "Usage:\n"
        "- Call this with an `output_path` to write today's edition to; the "
        "sections and their sources are fixed when the instance is built.\n"
        "- A partial result is normal: failed sources or sections are "
        "reported in the output and the briefing covers the rest.\n"
        "\n"
        "Returns the `markdown_path` written, the `executive_summary`, a "
        "`sections` report, and any `failed_sections`."
    )
    input_type: ClassVar[type] = MorningBriefingInput
    output_type: ClassVar[type] = MorningBriefingOutput

    def __init__(
        self,
        *,
        weather: WeatherCommission,
        news: Sequence[NewsDigestCommission],
        summarize: SummarizeCommission | None = None,
    ) -> None:
        news_tuple = tuple(news)
        if not news_tuple:
            raise ValueError("morning_briefing requires at least one news digest.")
        resolved_summarize = summarize or SummarizeCommission()
        super().__init__(toolbox=(weather, *news_tuple, resolved_summarize))
        self._weather = weather
        self._news = news_tuple
        self._summarize = resolved_summarize

    async def invoke(
        self,
        input: MorningBriefingInput,
        ctx: CallContext,
    ) -> CommissionResult[MorningBriefingOutput]:
        # The date header is deliberately plain application code: no judgment,
        # no fetch worth wrapping, so no contract jacket.
        now = datetime.now(UTC)
        date_label = now.strftime("%A %d %B %Y")
        provenance = Provenance(
            source=f"morning_briefing:{input.output_path}",
            fetched_at=now,
            confidence="grounded",
        )

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled before sections were dispatched.",
                retryable=False,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=0.0),
            )

        section_children: list[tuple[str, Commission[Any, Any], BaseModel]] = [
            ("Weather", self._weather, WeatherInput(briefing_date=date_label)),
            *(
                (
                    f"{digest.field.capitalize()} news",
                    digest,
                    NewsDigestInput(briefing_date=date_label),
                )
                for digest in self._news
            ),
        ]

        # One share per section plus one reserved for the executive summary;
        # the summary's actual slice is whatever the sections left over.
        shares = len(section_children) + 1
        per_section_budget = ctx.budget_usd / shares if ctx.budget_usd is not None else None
        section_ctx = replace(ctx, budget_usd=per_section_budget)

        self._emit(ctx, "sections", f"{len(section_children)} sections")
        section_results: list[CommissionResult[Any]] = await asyncio.gather(
            *(
                dispatch(child, child_input, section_ctx)
                for _, child, child_input in section_children
            )
        )
        sections_cost = sum(r.cost.estimated_usd for r in section_results)

        rendered: list[_RenderSection] = []
        reports: list[SectionReport] = []
        failed_sections: list[str] = []
        for (title, _, _), result in zip(section_children, section_results, strict=True):
            if result.output is None:
                failed_sections.append(title)
                continue
            if isinstance(result.output, WeatherOutput):
                summary_text = result.output.report
                claims: list[Claim[str]] = []
                failed_sources: list[str] = []
            else:
                digest_output: NewsDigestOutput = result.output
                summary_text = digest_output.summary_text
                claims = digest_output.claims
                failed_sources = digest_output.failed_sources
            rendered.append(_RenderSection(title=title, summary_text=summary_text, claims=claims))
            reports.append(
                SectionReport(
                    title=title,
                    summary_text=summary_text,
                    claim_count=len(claims),
                    failed_sources=failed_sources,
                )
            )

        if not reports:
            return self._fail(
                "internal",
                f"All {len(section_children)} sections failed; nothing to brief.",
                retryable=True,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=sections_cost),
            )

        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled between sections and the executive summary.",
                retryable=False,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=sections_cost),
            )

        # The coordinator's own judgment call: an executive summary over the
        # sections' prose. Its failure degrades the briefing, never kills it.
        remaining_budget = (
            max(ctx.budget_usd - sections_cost, 0.0) if ctx.budget_usd is not None else None
        )
        summary_ctx = replace(ctx, budget_usd=remaining_budget)
        self._emit(ctx, "executive_summary")
        summary_result = await dispatch(
            self._summarize,
            SummarizeInput(
                content="\n\n".join(f"{s.title}: {s.summary_text}" for s in rendered),
                length="short",
                focus="the two or three items a reader must know this morning",
            ),
            summary_ctx,
        )
        total_cost = CostMetrics(
            estimated_usd=sections_cost + summary_result.cost.estimated_usd,
        )
        executive_summary: str | None = (
            summary_result.output.summary
            if summary_result.status == "success" and summary_result.output is not None
            else None
        )

        markdown = _render_markdown(date_label, executive_summary, rendered, failed_sections)

        # Guarded like WriteTool: an unwritable path is a structured failure
        # carrying the real cost of the sections + summary, not a raise that
        # the dispatch backstop reports as $0.
        try:
            input.output_path.parent.mkdir(parents=True, exist_ok=True)
            input.output_path.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            return self._fail(
                "internal",
                f"Briefing was compiled but writing {input.output_path!s} failed: {exc}.",
                retryable=True,
                provenance=provenance,
                cost=total_cost,
            )
        self._emit(ctx, "written", str(input.output_path))

        degradations: list[str] = []
        if failed_sections:
            degradations.append(
                f"{len(failed_sections)} section(s) failed entirely: {', '.join(failed_sections)}"
            )
        failed_source_urls = [url for report in reports for url in report.failed_sources]
        if failed_source_urls:
            degradations.append(
                f"{len(failed_source_urls)} source fetch(es) failed: "
                f"{', '.join(failed_source_urls)}"
            )
        if executive_summary is None:
            degradations.append("executive summary failed; briefing written without it")

        output = MorningBriefingOutput(
            markdown_path=input.output_path,
            executive_summary=executive_summary,
            sections=reports,
            failed_sections=failed_sections,
        )
        status: CommissionStatus = "partial" if degradations else "success"
        error: ErrorState | None = (
            ErrorState(kind="internal", detail="; ".join(degradations), retryable=True)
            if degradations
            else None
        )

        return CommissionResult[MorningBriefingOutput](
            status=status,
            output=output,
            error=error,
            provenance=provenance,
            cost=total_cost,
        )


def _render_markdown(
    date_label: str,
    executive_summary: str | None,
    sections: list[_RenderSection],
    failed_sections: list[str],
) -> str:
    """Render the briefing with per-section claims and one footnoted source list."""
    source_index: dict[str, int] = {}
    source_list: list[Provenance] = []

    def ref(prov: Provenance) -> int:
        if prov.source not in source_index:
            source_index[prov.source] = len(source_list)
            source_list.append(prov)
        return source_index[prov.source] + 1

    lines: list[str] = [f"# Morning briefing: {date_label}", ""]
    if executive_summary:
        lines.extend([executive_summary, ""])

    for section in sections:
        lines.extend([f"## {section.title}", "", section.summary_text, ""])
        if section.claims:
            for claim in section.claims:
                refs = ", ".join(str(ref(p)) for p in claim.sources)
                lines.append(f"- {claim.value} [{refs}]")
            lines.append("")

    if failed_sections:
        lines.extend([f"*Sections unavailable this morning: {', '.join(failed_sections)}.*", ""])

    if source_list:
        lines.extend(["## Sources", ""])
        for i, prov in enumerate(source_list, start=1):
            lines.append(f"{i}. {prov.source}")
        lines.append("")

    return "\n".join(lines)
