"""MorningBriefing's boundary types stay beside the Commission that owns them."""

from pathlib import Path

from pydantic import BaseModel, Field


class MorningBriefingInput(BaseModel):
    """Inputs for one morning briefing run.

    Deliberately thin: what the briefing covers (the weather source, the news
    fields and their sources) is capacity, fixed when the Commission tree is
    constructed. The per-run work order is only where to write today's
    edition.
    """

    output_path: Path = Field(description="Filesystem path the markdown briefing is written to.")


class SectionReport(BaseModel):
    """Typed account of one section that made it into the briefing."""

    title: str = Field(description="Section heading as rendered in the briefing.")
    summary_text: str = Field(description="The section's summary prose.")
    claim_count: int = Field(
        default=0,
        description="Number of cited claims in the section; 0 for uncited sections like weather.",
    )
    failed_sources: list[str] = Field(
        default_factory=list,
        description="Source URLs within this section whose fetch failed.",
    )


class MorningBriefingOutput(BaseModel):
    """Attestation of one briefing: where it was written, what it contains."""

    markdown_path: Path = Field(description="Path of the markdown file written by this call.")
    executive_summary: str | None = Field(
        default=None,
        description=(
            "The executive summary that opens the briefing; None when the "
            "summary step failed and the briefing was written without it."
        ),
    )
    sections: list[SectionReport] = Field(
        description="Sections that made it into the briefing, in rendered order.",
    )
    failed_sections: list[str] = Field(
        default_factory=list,
        description="Titles of sections that produced nothing and were skipped.",
    )
