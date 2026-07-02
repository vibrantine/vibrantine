"""SummariseCommission: shorten one piece of content to a target length.

A basic LLM-loop commission — it declares its identity, I/O types, a
`system_prompt`, and a `build_user_message` hook, then rides the base's
default `invoke`. The toolbox is empty: summarisation is pure judgment over
content already in hand, so there is nothing to fetch. The LLM reads the
message and signals completion through the framework-injected `conclude`
tool, whose schema is this commission's `output_type`.

This is the Transform primitive in its single-source form: long in, short
out, one source, no citations. It is the deliberate counterpart to
`SynthesizeCommission`, which is the *multi*-source form (many sources → one
summary plus cited claims with provenance). The boundary is the contract:
Summarise takes a single `content` string and returns plain prose; Synthesize
takes a list of provenanced sources and returns claims carrying the
provenances that support them. Hold one source and want it shorter? Summarise.
Hold several and want them merged with their trail intact? Synthesize.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission

SummaryLength = Literal["one_sentence", "short", "medium", "long"]
"""Qualitative target sizes for a summary.

A closed vocabulary on the *input* (not the output), so it carries none of the
dynamic-`output_type` tension that per-call labels raise for Classify. The
prose meaning of each member is pinned in the system prompt — an LLM hits a
described size far more reliably than an exact word count.
"""

_SYSTEM_PROMPT = (
    "You summarise a single piece of content. The user message gives you the "
    "content, a target length, and optionally a focus to steer toward.\n"
    "\n"
    "Honour the target length:\n"
    "- one_sentence: exactly one sentence capturing the single most important "
    "point.\n"
    "- short: two or three sentences, or one short paragraph.\n"
    "- medium: a few short paragraphs covering the main points.\n"
    "- long: a thorough multi-paragraph summary that still reads shorter and "
    "tighter than the source.\n"
    "\n"
    "Summarise only what the content supports — never add facts, opinions, or "
    "details that are not in it. If a focus is given, foreground the parts "
    "relevant to it while staying faithful to the source. When you have the "
    "summary, call `conclude` with a single `summary` field. Do not produce "
    "free-form text outside of tool calls."
)


class SummariseInput(BaseModel):
    """Inputs for one summarise call."""

    content: str = Field(
        min_length=1,
        description="The content to summarise; must be non-empty.",
    )
    length: SummaryLength = Field(
        default="short",
        description=("Target length of the summary: 'one_sentence', 'short', 'medium', or 'long'."),
    )
    focus: str | None = Field(
        default=None,
        description="Optional focus to steer the summary toward one aspect.",
    )


class SummariseOutput(BaseModel):
    """Result of one summarise call."""

    summary: str = Field(description="The summary, written to the target length.")


class SummariseCommission(Commission[SummariseInput, SummariseOutput]):
    """Shorten a single piece of content to a target length via an LLM loop."""

    name: ClassVar[str] = "summarise"
    description: ClassVar[str] = (
        "Shorten one piece of content to a target length.\n"
        "\n"
        "Usage:\n"
        "- Call this with the `content` to shorten and an optional `length` "
        "('one_sentence', 'short', 'medium', 'long'; default 'short'); pass an "
        "optional `focus` to steer the summary toward one aspect.\n"
        "- Single source in hand — it does not fetch, and it does not cite. To "
        "merge several sources into a summary with provenance, use `synthesize` "
        "instead.\n"
        "\n"
        "Returns a `summary`: faithful prose written to the requested length."
    )
    input_type: ClassVar[type] = SummariseInput
    output_type: ClassVar[type] = SummariseOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT
    # Empty toolbox: summarisation is pure judgment over the supplied content.

    def build_user_message(self, input: SummariseInput, ctx: CallContext) -> str:
        parts = [f"Target length: {input.length}"]
        if input.focus:
            parts.append(f"Focus: {input.focus}")
        parts.append("")
        parts.append("Content to summarise:")
        parts.append(input.content)
        return "\n".join(parts)
