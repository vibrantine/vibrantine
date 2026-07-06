"""Rung 2: the same shape, handed a tool.

One new idea: `tools=`. The default loop shows the model a tool menu built
from what you grant, dispatches whatever it picks, and feeds the result
back; the Commission below answers a question about a file by reading the
file itself with `ReadTool`. Tools are granted, never grabbed: an empty
toolbox means the model can touch nothing.

Run it and the Commission reads this very file to answer.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from vibrantine import create_commission, invoke_sync
from vibrantine.tools import ReadTool


class FileQuestionInput(BaseModel):
    """A question about one file on disk."""

    path: str = Field(description="Absolute path of the file to read.")
    question: str = Field(description="The question to answer from the file's content.")


class FileQuestionOutput(BaseModel):
    """The grounded answer."""

    answer: str = Field(description="The answer, grounded in what the file actually says.")


file_answerer = create_commission(
    name="file_answerer",
    description=(
        "Answers a question about one file. Reads the file with the `read` "
        "tool first; never answers from memory."
    ),
    input=FileQuestionInput,
    output=FileQuestionOutput,
    tools=(ReadTool(),),
)


def main() -> None:
    here = Path(__file__).resolve()
    result = invoke_sync(
        file_answerer,
        FileQuestionInput(
            path=str(here),
            question="According to this file's docstring, what is the one new idea on this rung?",
        ),
    )

    if result.status != "success" or result.output is None:
        print(f"failed: {result.error}")
        return
    print(result.output.answer)
    print(f"\ncost: ${result.cost.estimated_usd:.6f}")


if __name__ == "__main__":
    main()
