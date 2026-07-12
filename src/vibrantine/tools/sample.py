"""Sample tool: file metadata + head/tail instead of the whole file.

Structural-discovery primitive load-bearing for the doc-management
use case: an agent learning corpus shape by sampling files cheaply
before committing context budget to a full Read. The agent runs many
Sample calls during exploration; Read is reserved for files that
warrant full content load. Cheap on both sides: the returned payload
is head/tail lines only, and the file is streamed in chunks (one full
pass for the exact line count, but never held in memory whole). Lines
longer than the per-line cap are truncated with a visible marker, so
a single-line pathology (minified JS, a JSONL dump) can't flood the
context the tool exists to protect.
"""

import codecs
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import (
    ZERO_COST,
    ReadFailure,
    failure,
    provenance,
)

# Per-line character cap: generous for any real prose or code line, fatal
# to the single-line pathology. Also what bounds the sampler's memory: the
# incomplete-line buffer and every kept line are trimmed to this.
LINE_CAP_CHARS: Final[int] = 2_000
_TRUNCATION_MARKER: Final[str] = "... [line truncated]"
_CHUNK_BYTES: Final[int] = 64 * 1024


def _capped(content: str, was_trimmed: bool) -> str:
    if was_trimmed or len(content) > LINE_CAP_CHARS:
        return content[:LINE_CAP_CHARS] + _TRUNCATION_MARKER
    return content


def _sample_file(
    path: Path,
    head_lines: int,
    tail_lines: int,
    chunk_bytes: int = _CHUNK_BYTES,
) -> tuple[list[str], list[str], int]:
    """One chunked pass over the file: head, tail, and exact line count.

    Memory stays bounded regardless of file size: the first `head_lines`
    lines, a rolling buffer of the last `tail_lines`, and at most one
    line-cap's worth of incomplete line. Line splitting matches
    `str.splitlines()` on the whole decoded file (what this replaced),
    including a CRLF pair straddling a chunk boundary: a trailing carriage
    return is held back until the next chunk can say whether a linefeed
    follows. UTF-8 is decoded incrementally, so multibyte characters split
    across chunks decode correctly and invalid bytes still fail strictly.
    Raises `ReadFailure` with the same classification as `read_text_utf8`.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    head: list[str] = []
    tail: deque[str] = deque(maxlen=tail_lines)
    count = 0
    buf = ""  # decoded text not yet closed by a line boundary
    trimmed = False  # buf was cut mid-line by the cap; marker owed at close

    def close_line(piece: str) -> None:
        nonlocal count, trimmed
        count += 1
        content = piece.splitlines()[0] if piece else ""
        line = _capped(content, trimmed)
        trimmed = False
        if len(head) < head_lines:
            head.append(line)
        tail.append(line)

    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(chunk_bytes)
                text = buf + decoder.decode(chunk, final=not chunk)
                buf = ""
                if not chunk:
                    for piece in text.splitlines(keepends=True):
                        close_line(piece)
                    break
                # Hold a trailing '\r': the next chunk may open with the
                # '\n' that completes it as one CRLF boundary.
                hold = ""
                if text.endswith("\r"):
                    hold = "\r"
                    text = text[:-1]
                pieces = text.splitlines(keepends=True)
                if pieces and len(pieces[-1]) == len(pieces[-1].splitlines()[0]):
                    buf = pieces.pop() + hold  # last piece has no boundary yet
                else:
                    buf = hold
                for piece in pieces:
                    close_line(piece)
                if len(buf) > LINE_CAP_CHARS + 1:
                    keep_cr = "\r" if buf.endswith("\r") else ""
                    buf = buf[:LINE_CAP_CHARS] + keep_cr
                    trimmed = True
    except FileNotFoundError:
        raise ReadFailure("validation", f"File not found: {path!s}.") from None
    except PermissionError as exc:
        raise ReadFailure("internal", f"Permission denied reading {path!s}: {exc}.") from exc
    except UnicodeDecodeError as exc:
        raise ReadFailure("internal", f"Could not decode {path!s} as UTF-8: {exc}.") from exc
    except OSError as exc:
        raise ReadFailure("internal", f"Filesystem error reading {path!s}: {exc}.") from exc
    return head, list(tail), count


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
    head: list[str] = Field(
        description=(
            "First `head_lines` lines of the file. Lines longer than "
            f"{LINE_CAP_CHARS} characters are cut and end with "
            f"'{_TRUNCATION_MARKER}'."
        ),
    )
    tail: list[str] = Field(
        description=(
            "Last `tail_lines` lines of the file. Lines longer than "
            f"{LINE_CAP_CHARS} characters are cut and end with "
            f"'{_TRUNCATION_MARKER}'."
        ),
    )


class SampleTool(Commission[SampleInput, SampleOutput]):
    """Sample a file: metadata plus head/tail without full content load."""

    name: ClassVar[str] = "sample"
    description: ClassVar[str] = (
        "Returns file metadata and head/tail content instead of the full\n"
        "body. Use for understanding corpus shape before committing\n"
        "context budget to a full read.\n"
        "\n"
        "Usage:\n"
        "- `path` must be absolute.\n"
        "- `head_lines` and `tail_lines` default to 20 each; set to 0 to\n"
        "  skip either end.\n"
        "- Prefer over `read` when you want to know what a file *contains*\n"
        "  without paying for its full content.\n"
        "- Lines longer than 2000 characters are cut and end with\n"
        "  '... [line truncated]', so single-line files (minified JS,\n"
        "  JSONL) stay cheap to sample.\n"
        "\n"
        "Returns `path`, `size_bytes`, `modified_at`, `line_count`,\n"
        "`head` (list of strings, without trailing newlines), and `tail`."
    )
    input_type: ClassVar[type] = SampleInput
    output_type: ClassVar[type] = SampleOutput
    deterministic: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def _run(
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

        # The streaming sampler carries read_text_utf8's standard failure
        # classification, including a file deleted between the stat above
        # and this read (validation, like every sibling tool's missing file).
        try:
            head, tail, line_count = _sample_file(input.path, input.head_lines, input.tail_lines)
        except ReadFailure as exc:
            return failure(exc.kind, exc.detail, retryable=False, provenance=prov)
        # If head+tail span the whole file, tail repeats head's content; that
        # is faithful to the request, and the LLM can dedupe by comparing
        # head[-1] to tail[0] if it cares.

        return CommissionResult[SampleOutput](
            status="success",
            output=SampleOutput(
                path=input.path,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                line_count=line_count,
                head=head,
                tail=tail,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )
