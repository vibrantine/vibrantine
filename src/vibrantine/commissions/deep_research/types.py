"""DeepResearch's boundary types stay beside the commission that owns them."""

from pydantic import BaseModel, Field

from vibrantine.contract import Claim


class ResearchInput(BaseModel):
    """Inputs for one research call."""

    question: str = Field(description="The research question to answer.")
    seed_urls: list[str] = Field(
        default_factory=list,
        description="Optional starting sources the agent may fetch.",
    )


class ResearchOutput(BaseModel):
    """A cited answer to one research question."""

    answer: str = Field(description="Synthesized answer to the question.")
    claims: list[Claim[str]] = Field(
        description="Load-bearing assertions, each with supporting source provenances.",
    )
