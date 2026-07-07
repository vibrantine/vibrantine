"""RecursiveResearch Commission: a recursive, LLM-loop research agent.

Answers a research question by letting its LLM decompose it into narrower
sub-questions, delegate each to a shallower sub-researcher (recursion), and
ground leaf answers in fetched sources. It rides the base's default `invoke`
(the LLM loop); recursion is wired entirely through the toolbox, with no
custom control flow.

Termination is structural: the recursion is a finite chain of distinct,
shallower instances built at construction. At `max_depth=0` the toolbox holds
only `FetchTool`, so the recurse tool is absent from the LLM's menu and a leaf
must answer from fetches alone. No reliance on the LLM to stop, no reference
cycle, no new CallContext field.

This is the first Commission wired into an LLM-loop toolbox, so its
`description` is LLM-facing and follows the tool prose pattern.
"""

from typing import TYPE_CHECKING, Any, ClassVar

from vibrantine._prompts import load_prompt
from vibrantine.contract import (
    DEFAULT_MAX_ITERATIONS,
    CallContext,
    Commission,
    OverflowPolicy,
    estimate_tokens,
)
from vibrantine.examples.recursive_research.models import RecursiveResearchModelMenu
from vibrantine.examples.recursive_research.types import ResearchInput, ResearchOutput
from vibrantine.models import Model
from vibrantine.tools.fetch import FetchTool

if TYPE_CHECKING:
    from openai import AsyncOpenAI


_PACKAGE = "vibrantine.examples.recursive_research"


# TODO: Recursion Redesign. Budget exhaustion here is an emergency stop
# (`budget_exceeded` failure); the settled principle is a graceful wind-down:
# reserve a wrap-up slice of the grant, stop delegating as spend approaches
# it, and conclude with a partial result plus receipts. Needs mid-run cost
# visibility for the LLM.
class RecursiveResearchCommission(Commission[ResearchInput, ResearchOutput]):
    """Answer a research question recursively, grounding leaf answers in fetches."""

    name: ClassVar[str] = "recursive_research"
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
    system_prompt: ClassVar[str | None] = load_prompt(_PACKAGE)

    # Sub-answers are rendered whole into the parent loop's context, so an
    # oversized one is chopped at the boundary: `truncate_output` below keeps
    # claims over prose, the full version is persisted under the run_id named
    # in the jacket, and the parent's LLM sees the chop plus the size warning.
    # Without a persistence backend the policy degrades to `partial` (full
    # output, flagged, never silent), which is a warning light rather than a
    # guard rail.
    max_output_tokens: int | None = 4000
    overflow_policy: OverflowPolicy = "truncate_with_reference"

    def __init__(
        self,
        *,
        max_depth: int = 2,
        fetch: FetchTool | None = None,
        model: str | Model | None = None,
        models: RecursiveResearchModelMenu | None = None,
        client: "AsyncOpenAI | None" = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
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
            sub = RecursiveResearchCommission(
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

    def truncate_output(self, output: ResearchOutput, max_tokens: int) -> ResearchOutput | None:
        """Chop an oversized sub-answer, keeping claims over prose.

        Claims are the receipts a parent researcher cites; the answer prose
        gets re-synthesized upstream anyway. So the answer is halved until
        the whole output fits, and trailing claims are dropped only once the
        answer is gone. Deterministic throughout: no LLM in the overflow
        path (design.md rules out LLM-summarized fallbacks).
        """
        marker = " …[truncated]"
        claims = list(output.claims)
        length = len(output.answer)
        while True:
            if length >= len(output.answer):
                answer = output.answer
            else:
                answer = output.answer[:length].rstrip() + marker
            candidate = ResearchOutput(answer=answer, claims=claims)
            if estimate_tokens(candidate.model_dump_json()) <= max_tokens:
                return candidate
            if length > 0:
                length //= 2
            elif claims:
                claims.pop()
            else:
                return None  # cap too small for even an empty output; degrade
