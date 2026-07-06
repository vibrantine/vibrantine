"""RecursiveResearch's boundary types stay beside the Commission that owns them."""

from pydantic import BaseModel, Field

from vibrantine.contract import ConfidenceLevel


class ResearchInput(BaseModel):
    """Inputs for one research call."""

    question: str = Field(description="The research question to answer.")
    seed_urls: list[str] = Field(
        default_factory=list,
        description="Optional starting sources the agent may fetch.",
    )


class ResearchClaim(BaseModel):
    """One load-bearing assertion, citing its supporting sources by URL.

    Deliberately LLM-emittable: the conclude tool's schema is the output
    type, so every field must be something the researching LLM actually
    knows. Full `Provenance` (with fetch timestamps) is not; the URLs it
    fetched are. Demanding Provenance here made conclude unpassable in
    live runs (2026-07-06).
    """

    value: str = Field(description="The asserted fact, stated briefly.")
    source_urls: list[str] = Field(
        description="URLs of the fetched sources that support this claim.",
    )
    confidence: ConfidenceLevel = Field(
        description="How grounded the claim is in its sources.",
    )


class ResearchOutput(BaseModel):
    """A cited answer to one research question."""

    answer: str = Field(description="Synthesized answer to the question.")
    claims: list[ResearchClaim] = Field(
        description="Load-bearing assertions, each citing supporting source URLs.",
    )
