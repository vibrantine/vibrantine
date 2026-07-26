"""Read tool: read a file's content with offset/limit pagination.

Foundation of the std-lib file-I/O layer. The `truncated` and
`total_lines` fields in the output let an LLM-driven caller decide
whether to continue from the returned line-and-character position.
2000-line default matches Claude Code; local-model Commissions may want
a smaller default.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import (
    ZERO_COST,
    ReadFailure,
    failure,
    iter_text_segments,
    provenance,
)

_MAX_LINE_CHARS = 30_000
_STREAM_SEGMENT_CHARS = 8_192


class ReadInput(BaseModel):
    """Inputs for one file read."""

    path: Path = Field(description="Absolute path of the file to read.")
    offset: int = Field(
        default=0,
        description="Line offset (0-based) to start reading from.",
        ge=0,
    )
    char_offset: int = Field(
        default=0,
        description="Character offset within `offset`'s line to start from.",
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
    next_offset: int | None = Field(
        default=None,
        description="Next zero-based line offset when `truncated` is true.",
    )
    next_char_offset: int | None = Field(
        default=None,
        description="Character offset within `next_offset` where continuation starts.",
    )


class ReadTool(Commission[ReadInput, ReadOutput]):
    """Read a file from the local filesystem, with offset/limit pagination."""

    name: ClassVar[str] = "read"
    description: ClassVar[str] = (
        "Reads a file from the local filesystem.\n"
        "\n"
        "Usage:\n"
        "- `path` must be absolute, not relative.\n"
        "- Default returns up to 2000 lines from the start. Use `offset`,\n"
        "  `char_offset`, and `limit` to continue from an exact position.\n"
        "- A returned line segment is capped at 30,000 characters. If the\n"
        "  cap or line limit is reached, `truncated` is true and\n"
        "  `next_offset`/`next_char_offset` identify the next character;\n"
        "  pass those values back unchanged to retrieve the remainder.\n"
        "\n"
        "Returns `content` (faithful to the file's bytes including line\n"
        "endings), `line_count`, `truncated`, `total_lines`, and the optional\n"
        "next continuation position."
    )
    input_type: ClassVar[type] = ReadInput
    output_type: ClassVar[type] = ReadOutput
    deterministic: ClassVar[bool] = True

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

        content_parts: list[str] = []
        total_lines = 0
        line_count = 0
        returned_in_line = 0
        output_stopped = False
        next_offset: int | None = None
        next_char_offset: int | None = None
        target_char_seen = input.char_offset == 0

        try:
            for line, char_start, segment, line_complete in iter_text_segments(
                input.path,
                segment_chars=_STREAM_SEGMENT_CHARS,
            ):
                total_lines = max(total_lines, line + 1)
                if output_stopped or line < input.offset:
                    continue
                if line >= input.offset + input.limit:
                    output_stopped = True
                    next_offset = line
                    next_char_offset = 0
                    continue

                start = 0
                if line == input.offset:
                    if char_start + len(segment) <= input.char_offset:
                        if line_complete and char_start + len(segment) < input.char_offset:
                            return failure(
                                "validation",
                                f"char_offset {input.char_offset} exceeds line "
                                f"{input.offset}'s length.",
                                retryable=False,
                                provenance=prov,
                            )
                        continue
                    start = max(0, input.char_offset - char_start)
                    target_char_seen = True

                available = segment[start:]
                remaining = _MAX_LINE_CHARS - returned_in_line
                kept = available[:remaining]
                content_parts.append(kept)
                returned_in_line += len(kept)

                if len(available) > remaining or (
                    returned_in_line == _MAX_LINE_CHARS and not line_complete
                ):
                    output_stopped = True
                    next_offset = line
                    next_char_offset = char_start + start + len(kept)
                    line_count += 1
                    continue

                if line_complete:
                    line_count += 1
                    returned_in_line = 0
                    if line_count == input.limit:
                        output_stopped = True
                        next_offset = line + 1
                        next_char_offset = 0
        except ReadFailure as exc:
            return failure(exc.kind, exc.detail, retryable=False, provenance=prov)

        if total_lines > input.offset and not target_char_seen:
            return failure(
                "validation",
                f"char_offset {input.char_offset} exceeds line {input.offset}'s length.",
                retryable=False,
                provenance=prov,
            )

        truncated = next_offset is not None and (
            next_offset < total_lines or (next_offset == total_lines and next_char_offset != 0)
        )
        if not truncated:
            next_offset = None
            next_char_offset = None

        return CommissionResult[ReadOutput](
            status="success",
            output=ReadOutput(
                content="".join(content_parts),
                line_count=line_count,
                truncated=truncated,
                total_lines=total_lines,
                next_offset=next_offset,
                next_char_offset=next_char_offset,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )
