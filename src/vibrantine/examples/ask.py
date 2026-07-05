"""AskCommission: answer a question about a single file.

A basic LLM-loop Commission: it declares its identity, I/O types, system
prompt, and a `ReadTool` toolbox, then rides the base's default `invoke`. The
LLM decides when to call `read` (possibly multiple times for paginated files),
then signals completion through the framework-injected `conclude` tool.

Intentionally minimal: one tool, one fixed path in input, plain string answer
in output. Built to exercise the LLM-tool-wrapper + dispatch-loop machinery
without piling on the open SSOT questions around overflow policy, persistence,
or layered prompts.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission
from vibrantine.tools.read import ReadTool

_SYSTEM_PROMPT = (
    "You answer questions about a single file. You have one tool: `read`, "
    "which loads a slice of the file given a path, offset, and limit. The "
    "user message tells you which file to read and what question to answer. "
    "Read enough of the file to answer confidently; paginate if `truncated` "
    "is true and the answer hasn't appeared. When you have an answer, call "
    "`conclude` with a single `answer` field. Do not produce free-form text "
    "outside of tool calls."
)


class AskInput(BaseModel):
    """Inputs for one ask call."""

    question: str = Field(description="The question to answer about the file.")
    file_path: Path = Field(description="Absolute path to the file to consult.")


class AskOutput(BaseModel):
    """Result of one ask call."""

    answer: str = Field(description="Natural-language answer to the question.")


class AskCommission(Commission[AskInput, AskOutput]):
    """Answer a question about a single file via an LLM loop over ReadTool."""

    name: ClassVar[str] = "ask"
    description: ClassVar[str] = (
        "Answer a question about a single file by reading its contents.\n"
        "\n"
        "Usage:\n"
        "- Call this with a `file_path` and a `question` about that one file; "
        "it reads the file (paginating large files as needed) and answers.\n"
        "- One file per call. For a question spanning several files, call once "
        "per file and combine the answers yourself.\n"
        "\n"
        "Returns an `answer`: a natural-language response grounded in the "
        "file's contents."
    )
    input_type: ClassVar[type] = AskInput
    output_type: ClassVar[type] = AskOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT
    toolbox = (ReadTool(),)

    def build_user_message(self, input: AskInput, ctx: CallContext) -> str:
        return f"File path: {input.file_path}\nQuestion: {input.question}"
