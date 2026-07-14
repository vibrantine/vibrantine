"""SummarizeCommission: shorten one piece of content to a target length.

A basic LLM-loop Commission: it declares its identity, I/O types, a
`system_prompt`, and a `build_user_message` hook, then rides the base's
default `_run`. The toolbox is empty: summarization is pure judgment over
content already in hand, so there is nothing to fetch. The LLM reads the
message and signals completion through the framework-injected `conclude`
tool, whose schema is this Commission's `output_type`.

This is the Transform primitive in its single-source form: long in, short
out, one source, no citations. It is the deliberate counterpart to
`SynthesizeCommission`, which is the *multi*-source form (many sources → one
summary plus cited claims with provenance). The boundary is the contract:
Summarize takes a single `content` string and returns plain prose; Synthesize
takes a list of provenanced sources and returns claims carrying the
provenances that support them. Hold one source and want it shorter? Summarize.
Hold several and want them merged with their trail intact? Synthesize.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission

SummaryLength = Literal["one_sentence", "short", "medium", "long"]
"""Qualitative target sizes for a summary.

A closed vocabulary on the *input* (not the output), so the typed output
contract never varies per call. The prose meaning of each member is pinned
in the system prompt; an LLM hits a described size far more reliably than
an exact word count.
"""

_SYSTEM_PROMPT = (
    "You summarize a single piece of content. The user message gives you the "
    "content, a target length, and optionally a focus to steer toward.\n"
    "\n"
    "Honor the target length:\n"
    "- one_sentence: exactly one sentence capturing the single most important "
    "point.\n"
    "- short: two or three sentences, or one short paragraph.\n"
    "- medium: a few short paragraphs covering the main points.\n"
    "- long: a thorough multi-paragraph summary that still reads shorter and "
    "tighter than the source.\n"
    "\n"
    "Summarize only what the content supports; never add facts, opinions, or "
    "details that are not in it. If a focus is given, foreground the parts "
    "relevant to it while staying faithful to the source. When you have the "
    "summary, call `conclude` with a single `summary` field. Do not produce "
    "free-form text outside of tool calls."
)


class SummarizeInput(BaseModel):
    """Inputs for one summarize call."""

    content: str = Field(
        min_length=1,
        description="The content to summarize; must be non-empty.",
    )
    length: SummaryLength = Field(
        default="short",
        description=("Target length of the summary: 'one_sentence', 'short', 'medium', or 'long'."),
    )
    focus: str | None = Field(
        default=None,
        description="Optional focus to steer the summary toward one aspect.",
    )


class SummarizeOutput(BaseModel):
    """Result of one summarize call."""

    summary: str = Field(description="The summary, written to the target length.")


class SummarizeCommission(Commission[SummarizeInput, SummarizeOutput]):
    """Shorten a single piece of content to a target length via an LLM loop."""

    name: ClassVar[str] = "summarize"
    description: ClassVar[str] = (
        "Shorten one piece of content to a target length.\n"
        "\n"
        "Usage:\n"
        "- Call this with the `content` to shorten and an optional `length` "
        "('one_sentence', 'short', 'medium', 'long'; default 'short'); pass an "
        "optional `focus` to steer the summary toward one aspect.\n"
        "- Single source in hand; it does not fetch, and it does not cite. To "
        "merge several sources into a summary with provenance, use `synthesize` "
        "instead.\n"
        "\n"
        "Returns a `summary`: faithful prose written to the requested length."
    )
    input_type: ClassVar[type] = SummarizeInput
    output_type: ClassVar[type] = SummarizeOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT
    # Empty toolbox: summarization is pure judgment over the supplied content.

    def build_user_message(self, input: SummarizeInput, ctx: CallContext) -> str:
        parts = [f"Target length: {input.length}"]
        if input.focus:
            parts.append(f"Focus: {input.focus}")
        parts.append("")
        parts.append("Content to summarize:")
        parts.append(input.content)
        return "\n".join(parts)
