"""Read tool: read a file's content with offset/limit pagination.

Foundation of the std-lib file-I/O layer. The `truncated` and
`total_lines` fields in the output let an LLM-driven caller decide
whether to paginate further. 2000-line default matches Claude Code;
local-model Commissions may want a smaller default.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import (
    ZERO_COST,
    ReadFailure,
    failure,
    provenance,
    read_text_utf8,
)

_MAX_LINE_CHARS = 30_000
_TRUNCATION_MARKER = "…[truncated]"


class ReadInput(BaseModel):
    """Inputs for one file read."""

    path: Path = Field(description="Absolute path of the file to read.")
    offset: int = Field(
        default=0,
        description="Line offset (0-based) to start reading from.",
        ge=0,
    )
    limit: int = Field(
        default=2000,
        description="Maximum number of lines to return.",
        gt=0,
    )


class ReadOutput(BaseModel):
    """Returned content plus pagination metadata."""

    content: str = Field(description="The slice of file content returned.")
    line_count: int = Field(description="Number of lines in the returned content.")
    truncated: bool = Field(
        description="True if more content remains beyond the returned slice.",
    )
    total_lines: int = Field(description="Total number of lines in the file.")


class ReadTool(Commission[ReadInput, ReadOutput]):
    """Read a file from the local filesystem, with offset/limit pagination."""

    name: ClassVar[str] = "read"
    description: ClassVar[str] = (
        "Reads a file from the local filesystem.\n"
        "\n"
        "Usage:\n"
        "- `path` must be absolute, not relative.\n"
        "- Default returns up to 2000 lines from the start. Use `offset` and\n"
        "  `limit` to paginate; check `truncated` to know if more remains.\n"
        "- Lines longer than 30,000 chars are truncated with the marker\n"
        f"  `{_TRUNCATION_MARKER}`.\n"
        "\n"
        "Returns `content` (faithful to the file's bytes including line\n"
        "endings), `line_count`, `truncated`, and `total_lines`."
    )
    input_type: ClassVar[type] = ReadInput
    output_type: ClassVar[type] = ReadOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def _run(
        self,
        input: ReadInput,
        ctx: CallContext,
    ) -> CommissionResult[ReadOutput]:
        prov = provenance(str(input.path))

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before file read began.",
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

        # Windows raises PermissionError on read_text(directory); POSIX
        # raises IsADirectoryError. Check ahead so behavior is deterministic.
        if input.path.is_dir():
            return failure(
                "validation",
                f"Path is a directory, not a file: {input.path!s}.",
                retryable=False,
                provenance=prov,
            )

        try:
            text = read_text_utf8(input.path)
        except ReadFailure as exc:
            return failure(exc.kind, exc.detail, retryable=False, provenance=prov)

        all_lines = text.splitlines(keepends=True)
        total_lines = len(all_lines)
        sliced = all_lines[input.offset : input.offset + input.limit]
        content = "".join(_truncate_long(line) for line in sliced)
        truncated = (input.offset + len(sliced)) < total_lines

        return CommissionResult[ReadOutput](
            status="success",
            output=ReadOutput(
                content=content,
                line_count=len(sliced),
                truncated=truncated,
                total_lines=total_lines,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )


def _truncate_long(line: str) -> str:
    """Truncate a single line to _MAX_LINE_CHARS with a visible marker."""
    if len(line) <= _MAX_LINE_CHARS:
        return line
    if line.endswith("\n"):
        return line[: _MAX_LINE_CHARS - 1] + _TRUNCATION_MARKER + "\n"
    return line[:_MAX_LINE_CHARS] + _TRUNCATION_MARKER
