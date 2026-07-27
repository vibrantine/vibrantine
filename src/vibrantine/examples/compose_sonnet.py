"""ComposeSonnetCommission: a small typed LLM task for outward compatibility tests.

The Commission is deliberately unmistakable and self-contained: one subject
goes in, one title and exactly fourteen ordered lines come out. It has no
tools, children, files, networking, or side effects, so external adapters can
exercise discovery and invocation without unrelated behavior obscuring the
Commission boundary.
"""

from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine import CallContext, Commission

_SYSTEM_PROMPT = (
    "You compose original English sonnets about subjects supplied by the user. "
    "Follow a recognizable sonnet form, with coherent imagery, purposeful rhyme "
    "and meter, and a clear turn in thought. When the sonnet is complete, call "
    "`conclude` with a title and exactly 14 ordered lines. The title is separate "
    "from the sonnet and does not count as a line. Put one verse line in each "
    "list item, with no numbering, blank items, or explanatory prose. Do not "
    "produce free-form text outside of tool calls."
)


class ComposeSonnetInput(BaseModel):
    """The subject of one requested sonnet."""

    subject: str = Field(description="The subject the original sonnet should explore.")


class ComposeSonnetOutput(BaseModel):
    """One titled sonnet with its verse kept in reading order."""

    title: str = Field(description="The sonnet's title, separate from its fourteen lines.")
    lines: list[str] = Field(
        min_length=14,
        max_length=14,
        description="Exactly fourteen sonnet lines in reading order.",
    )


class ComposeSonnetCommission(Commission[ComposeSonnetInput, ComposeSonnetOutput]):
    """Compose a structurally valid sonnet through the default LLM loop."""

    name: ClassVar[str] = "compose_vibrantine_sonnet"
    description: ClassVar[str] = (
        "Compose an original 14-line sonnet through Vibrantine. Use this when the user "
        "explicitly asks to write a Vibrantine sonnet or invoke the sonnet Commission. "
        "Provide the sonnet's subject. Returns a title and exactly 14 ordered lines."
    )
    input_type: ClassVar[type] = ComposeSonnetInput
    output_type: ClassVar[type] = ComposeSonnetOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT

    def build_user_message(self, input: ComposeSonnetInput, ctx: CallContext) -> str:
        return f"Subject: {input.subject}"
