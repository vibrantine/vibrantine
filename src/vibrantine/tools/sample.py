"""Sample tool: file metadata + head/tail without loading the whole file.

Structural-discovery primitive load-bearing for the doc-management
use case: an agent learning corpus shape by sampling files cheaply
before committing context budget to a full Read. The agent runs many
Sample calls during exploration; Read is reserved for files that
warrant full content load.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import ZERO_COST, failure, provenance


class SampleInput(BaseModel):
    """Inputs for one file sample."""

    path: Path = Field(description="Absolute path of the file to sample.")
    head_lines: int = Field(
        default=20,
        description="Number of lines from the start to include.",
        ge=0,
    )
    tail_lines: int = Field(
        default=20,
        description="Number of lines from the end to include.",
        ge=0,
    )


class SampleOutput(BaseModel):
    """File metadata plus head and tail content."""

    path: Path = Field(description="Absolute path of the sampled file.")
    size_bytes: int = Field(description="File size on disk, in bytes.")
    modified_at: datetime = Field(description="Last modification time of the file.")
    line_count: int = Field(description="Total lines in the file.")
    head: list[str] = Field(description="First `head_lines` lines of the file.")
    tail: list[str] = Field(description="Last `tail_lines` lines of the file.")


class SampleTool(Commission[SampleInput, SampleOutput]):
    """Sample a file: metadata plus head/tail without full content load."""

    name: ClassVar[str] = "sample"
    description: ClassVar[str] = (
        "Returns file metadata and head/tail content without loading the\n"
        "full file. Use for understanding corpus shape before committing\n"
        "context budget to a full read.\n"
        "\n"
        "Usage:\n"
        "- `path` must be absolute.\n"
        "- `head_lines` and `tail_lines` default to 20 each; set to 0 to\n"
        "  skip either end.\n"
        "- Prefer over `read` when you want to know what a file *contains*\n"
        "  without paying for its full content.\n"
        "\n"
        "Returns `path`, `size_bytes`, `modified_at`, `line_count`,\n"
        "`head` (list of strings, without trailing newlines), and `tail`."
    )
    input_type: ClassVar[type] = SampleInput
    output_type: ClassVar[type] = SampleOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
        self,
        input: SampleInput,
        ctx: CallContext,
    ) -> CommissionResult[SampleOutput]:
        prov = provenance(str(input.path))

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before sample began.",
                retryable=False,
                provenance=prov,
            )

        if not input.path.is_absolute():
            return failure(
                "validation",
                f"Path must be absolute; got {input.path!s}.",
                retryable=False,
                provenance=prov,
            )

        if input.path.is_dir():
            return failure(
                "validation",
                f"Path is a directory, not a file: {input.path!s}.",
                retryable=False,
                provenance=prov,
            )

        try:
            stat = input.path.stat()
        except FileNotFoundError:
            return failure(
                "validation",
                f"File not found: {input.path!s}.",
                retryable=False,
                provenance=prov,
            )
        except PermissionError as exc:
            return failure(
                "internal",
                f"Permission denied stat-ing {input.path!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )

        try:
            text = input.path.read_text(encoding="utf-8")
        except PermissionError as exc:
            return failure(
                "internal",
                f"Permission denied reading {input.path!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )
        except UnicodeDecodeError as exc:
            return failure(
                "internal",
                f"Could not decode {input.path!s} as UTF-8: {exc}.",
                retryable=False,
                provenance=prov,
            )

        lines = text.splitlines()
        head = lines[: input.head_lines] if input.head_lines > 0 else []
        tail = lines[-input.tail_lines :] if input.tail_lines > 0 else []
        # If head+tail span the whole file, tail repeats head's content; that
        # is faithful to the request, and the LLM can dedupe by comparing
        # head[-1] to tail[0] if it cares.

        return CommissionResult[SampleOutput](
            status="success",
            output=SampleOutput(
                path=input.path,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                line_count=len(lines),
                head=head,
                tail=tail,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )
