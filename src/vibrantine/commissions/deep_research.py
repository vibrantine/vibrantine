"""DeepResearch commission: a recursive, LLM-loop research agent.

Answers a research question by letting its LLM decompose it into narrower
sub-questions, delegate each to a shallower sub-researcher (recursion), and
ground leaf answers in fetched sources. It rides the base's default `invoke`
(the LLM loop) — recursion is wired entirely through the toolbox, with no
custom control flow.

Termination is structural: the recursion is a finite chain of distinct,
shallower instances built at construction. At `max_depth=0` the toolbox holds
only `FetchTool`, so the recurse tool is absent from the LLM's menu and a leaf
must answer from fetches alone. No reliance on the LLM to stop, no reference
cycle, no new CallContext field.

This is the first commission wired into an LLM-loop toolbox, so its
`description` is LLM-facing and follows the tool prose pattern.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import (
    CallContext,
    Claim,
    Commission,
    OverflowPolicy,
)
from vibrantine.models import Model
from vibrantine.tools.fetch import FetchTool

if TYPE_CHECKING:
    from openai import AsyncOpenAI


_RESEARCH_SYSTEM_PROMPT = (
    "You are a research agent. Answer the user's research question accurately "
    "and concisely.\n"
    "- If the question is broad or has separable parts, break it into a few "
    "(at most three) narrower sub-questions and delegate each to the "
    "`deep_research` tool; it returns a cited answer for that sub-question.\n"
    "- Ground your answer in sources: call `fetch` to retrieve a URL before "
    "asserting facts that depend on it.\n"
    "- When you have enough to answer, call `conclude` with `answer` (your "
    "synthesized answer) and `claims` (the load-bearing assertions, each "
    "carrying the source provenances that support it).\n"
    "- Do not assert facts you have not grounded in a fetched source or a "
    "delegated sub-answer.\n"
    "- If the `deep_research` tool is not offered to you, you are at the leaf "
    "level: answer directly from `fetch` results — do not try to delegate."
)


@dataclass(frozen=True)
class DeepResearchModelMenu:
    """The tree's LLM seats, filled by the caller at construction.

    The commission declares its seats; the caller fills them (or none).
    `researcher` is the root's own loop; `subresearcher` is every delegated
    level below the root. An unfilled seat falls back to `default`, then to
    the system default model. Dumb data: resolution happens in `__init__`.
    """

    default: str | Model | None = None
    researcher: str | Model | None = None
    subresearcher: str | Model | None = None


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


class DeepResearchCommission(Commission[ResearchInput, ResearchOutput]):
    """Answer a research question recursively, grounding leaf answers in fetches."""

    name: ClassVar[str] = "deep_research"
    description: ClassVar[str] = (
        "Answer a research question with citations. Decomposes broad questions "
        "into narrower sub-questions, delegates them to sub-researchers, and "
        "grounds leaf answers in fetched sources.\n"
        "\n"
        "Usage:\n"
        "- Call this to delegate one self-contained sub-question; it returns an "
        "`answer` plus `claims` (cited assertions).\n"
        "- Prefer one delegation per distinct sub-question.\n"
        "- For a question you can answer directly from a source, fetch and "
        "conclude yourself instead of delegating."
    )
    input_type: ClassVar[type] = ResearchInput
    output_type: ClassVar[type] = ResearchOutput
    system_prompt: ClassVar[str | None] = _RESEARCH_SYSTEM_PROMPT

    # Sub-answers are rendered whole into the parent loop's context; cap them
    # so one verbose delegate can't bloat the parent. `partial` keeps the
    # usable output and flags the truncation through the result jacket.
    max_output_tokens: int | None = 4000
    overflow_policy: OverflowPolicy = "partial"

    def __init__(
        self,
        *,
        max_depth: int = 2,
        fetch: FetchTool | None = None,
        model: str | Model | None = None,
        models: DeepResearchModelMenu | None = None,
        client: "AsyncOpenAI | None" = None,
        max_iterations: int = 10,
    ) -> None:
        if model is not None and models is not None:
            raise ValueError(
                "Pass model= (one model for the whole tree) or models= (a menu of seats), not both."
            )
        own_model = model
        sub_model = model
        if models is not None:
            own_model = models.researcher or models.default
            sub_model = models.subresearcher or models.default
        resolved_fetch = fetch or FetchTool()
        toolbox: tuple[Commission[Any, Any], ...]
        if max_depth > 0:
            # The whole chain below the root shares the subresearcher seat,
            # threaded down as a plain model= like any single-model tree.
            sub = DeepResearchCommission(
                max_depth=max_depth - 1,
                fetch=resolved_fetch,
                model=sub_model,
                client=client,
                max_iterations=max_iterations,
            )
            toolbox = (sub, resolved_fetch)
        else:
            toolbox = (resolved_fetch,)
        super().__init__(
            toolbox=toolbox,
            model=own_model,
            client=client,
            max_iterations=max_iterations,
        )
        self.max_depth = max_depth

    def build_user_message(self, input: ResearchInput, ctx: CallContext) -> str:
        message = f"Research question: {input.question}"
        if input.seed_urls:
            seeds = "\n".join(input.seed_urls)
            message += f"\n\nSeed sources you may fetch:\n{seeds}"
        return message
