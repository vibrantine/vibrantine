"""Grep tool: search file contents for a regex pattern.

Content-search primitive. Files are searched directly; directories are
walked recursively. Pair with Glob (find by name) for full-filesystem
discovery; pair with Read or Sample once an interesting match is found.

The first tool whose result size can grow alarmingly: `max_matches`
bounds it, with `truncated=True` signalling more remains. This is the
tool that surfaces the tool-result-budgeting design question
concretely (see `docs/design-decisions.md § Oversized output is a
policy the caller picks`).

Unreadable files (binary content, permission denied, vanished mid-walk)
are skipped silently during directory walks so one bad entry never
aborts the rest; on a direct file path, the same failures surface as
errors so the caller knows the requested file was unreadable.
"""

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult, ErrorKind
from vibrantine.tools._helpers import (
    ZERO_COST,
    ReadFailure,
    failure,
    iter_text_segments,
    provenance,
)

_MAX_LINE_PREVIEW_CHARS = 2_000
_STREAM_SEGMENT_CHARS = 8_192
_MAX_REGEX_OVERLAP_CHARS = 30_000


class _ParsedPattern(Protocol):
    def getwidth(self) -> tuple[int, int]: ...


class GrepMatch(BaseModel):
    """One matching line, with its file and position."""

    path: Path = Field(description="Absolute path of the file containing the match.")
    line_number: int = Field(description="1-based line number of the match.")
    line: str = Field(description="Bounded preview of the matching line, without its newline.")
    line_chars: int = Field(description="Full matching-line length, excluding its newline.")
    line_truncated: bool = Field(
        description=(
            "True when `line` is only a preview. Use Read with this `path`, "
            "`offset=line_number-1`, and `char_offset=0` for complete content."
        ),
    )


class GrepInput(BaseModel):
    """Inputs for one content search."""

    pattern: str = Field(
        description="Regular expression to search for (Python `re` syntax).",
    )
    path: Path = Field(
        description=(
            "Absolute path to search. Single files are searched directly; "
            "directories are walked recursively."
        ),
    )
    max_matches: int = Field(
        default=100,
        description=(
            "Maximum number of matches to return. Bounds output size; the "
            "result's `truncated` flag indicates when this cap is hit."
        ),
        gt=0,
    )
    ignore_case: bool = Field(
        default=False,
        description="If true, the regex match is case-insensitive.",
    )


class GrepOutput(BaseModel):
    """Matching lines plus a truncation flag."""

    matches: list[GrepMatch] = Field(description="Matching lines, in encounter order.")
    truncated: bool = Field(
        description="True if more matches exist beyond `max_matches`.",
    )


class GrepTool(Commission[GrepInput, GrepOutput]):
    """Search file contents for a regex pattern; bounded by max_matches."""

    name: ClassVar[str] = "grep"
    description: ClassVar[str] = (
        "Searches file contents for a regular-expression pattern. Pair with\n"
        "`glob` (find by name) for full filesystem discovery.\n"
        "\n"
        "Usage:\n"
        "- `pattern` uses Python `re` syntax.\n"
        "- `path` must be absolute. Files are searched directly; directories\n"
        "  are walked recursively. Unreadable files (binary content,\n"
        "  permission denied) are skipped silently during directory walks.\n"
        "- `max_matches` (default 100) bounds output size. Check the\n"
        "  `truncated` flag: if true, narrow your pattern or path.\n"
        "- Each matching `line` is a bounded preview. If its\n"
        "  `line_truncated` is true, call Read with the match's `path`,\n"
        "  `offset=line_number-1`, and `char_offset=0` to retrieve the full\n"
        "  line through Read's continuation fields.\n"
        "- Lines over 30,000 characters require a finite-width pattern\n"
        "  without anchors, lookarounds, backreferences, or unbounded\n"
        "  repeats. Other patterns fail validation on such a line rather\n"
        "  than risking an incorrect streaming match; narrow the pattern or\n"
        "  use Shell with a suitable search program.\n"
        "- Set `ignore_case=true` for case-insensitive matching.\n"
        "\n"
        "Returns `matches` (path, line number, preview, full character count,\n"
        "and preview-truncation flag) plus global `truncated`."
    )
    input_type: ClassVar[type] = GrepInput
    output_type: ClassVar[type] = GrepOutput
    deterministic: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def _run(
        self,
        input: GrepInput,
        ctx: CallContext,
    ) -> CommissionResult[GrepOutput]:
        prov = provenance(f"grep:{input.pattern}@{input.path}")

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before grep began.",
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

        if not input.path.exists():
            return failure(
                "validation",
                f"Path does not exist: {input.path!s}.",
                retryable=False,
                provenance=prov,
            )

        flags = re.IGNORECASE if input.ignore_case else 0
        try:
            compiled = re.compile(input.pattern, flags)
        except re.error as exc:
            return failure(
                "validation",
                f"Invalid regex {input.pattern!r}: {exc}.",
                retryable=False,
                provenance=prov,
            )

        files = _iter_target_files(input.path)
        matches: list[GrepMatch] = []
        truncated = False

        for file in files:
            if ctx.cancel.is_cancelled:
                return failure(
                    "cancelled",
                    f"Cancelled mid-walk at {file!s}.",
                    retryable=False,
                    provenance=prov,
                )
            remaining = input.max_matches - len(matches)
            file_matches, file_err = _grep_file(
                file,
                compiled,
                input.path.is_file(),
                max_matches=remaining + 1,
            )
            if file_err is not None:
                return failure(
                    file_err[0],
                    file_err[1],
                    retryable=False,
                    provenance=prov,
                )
            matches.extend(file_matches[:remaining])
            if len(file_matches) > remaining:
                truncated = True
            if truncated:
                break

        return CommissionResult[GrepOutput](
            status="success",
            output=GrepOutput(matches=matches, truncated=truncated),
            provenance=prov,
            cost=ZERO_COST,
        )


def _iter_target_files(path: Path) -> Iterator[Path]:
    """One file, or a lazy depth-first walk of a directory.

    Lazy so a huge tree (the large-corpus case) starts matching immediately
    and stays responsive to the caller's per-file cancel check, instead of
    materializing the whole listing up front. Entries are sorted per
    directory for a deterministic encounter order.
    """
    if path.is_file():
        yield path
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames.sort()
        for name in sorted(filenames):
            child = Path(dirpath) / name
            if child.is_file():  # skip broken symlinks etc.
                yield child


def _grep_file(
    file: Path,
    pattern: re.Pattern[str],
    surface_read_errors: bool,
    *,
    max_matches: int = 100,
) -> tuple[list[GrepMatch], tuple[ErrorKind, str] | None]:
    """Search one file. Returns (matches, error-tuple).

    `surface_read_errors=True` (the direct-file case) returns any read
    failure as an error tuple (kind, detail); False (the directory-walk
    case) skips the unreadable file silently, so one bad entry never
    aborts the rest of the walk.
    """
    matches: list[GrepMatch] = []
    preview_parts: list[str] = []
    line_chars = 0
    search_tail = ""
    line_matched = False
    current_line = 0
    overlap = _streaming_overlap(pattern)

    try:
        for line, _, segment, line_complete in iter_text_segments(
            file,
            segment_chars=_STREAM_SEGMENT_CHARS,
        ):
            current_line = line
            content = _without_line_ending(segment) if line_complete else segment
            line_chars += len(content)
            if line_chars > _MAX_REGEX_OVERLAP_CHARS and overlap is None:
                return [], (
                    "validation",
                    f"Pattern {pattern.pattern!r} cannot be evaluated soundly "
                    f"with bounded memory on {file!s} line {line + 1}; lines "
                    "over 30,000 characters require a finite-width pattern "
                    "without anchors, lookarounds, backreferences, or "
                    "unbounded repeats.",
                )
            preview_remaining = _MAX_LINE_PREVIEW_CHARS - sum(map(len, preview_parts))
            if preview_remaining > 0:
                preview_parts.append(content[:preview_remaining])

            if not line_matched:
                search_window = search_tail + content
                line_matched = pattern.search(search_window) is not None
                search_tail = search_window[-overlap:] if overlap else ""

            if not line_complete:
                continue
            if line_matched:
                matches.append(
                    GrepMatch(
                        path=file,
                        line_number=line + 1,
                        line="".join(preview_parts),
                        line_chars=line_chars,
                        line_truncated=line_chars > _MAX_LINE_PREVIEW_CHARS,
                    )
                )
                if len(matches) >= max_matches:
                    break
            preview_parts = []
            line_chars = 0
            search_tail = ""
            line_matched = False
    except ReadFailure as exc:
        # During a walk every unreadable entry (vanished mid-walk, permission
        # denied, binary content) is skipped so one bad file never aborts the
        # rest; a direct path surfaces the classified failure.
        if surface_read_errors:
            return [], (exc.kind, exc.detail)
        return [], None
    if line_chars > 0 and line_matched and len(matches) < max_matches:
        matches.append(
            GrepMatch(
                path=file,
                line_number=current_line + 1,
                line="".join(preview_parts),
                line_chars=line_chars,
                line_truncated=line_chars > _MAX_LINE_PREVIEW_CHARS,
            )
        )
    return matches, None


def _without_line_ending(segment: str) -> str:
    if segment.endswith("\r\n"):
        return segment[:-2]
    if segment.endswith(("\r", "\n")):
        return segment[:-1]
    return segment


def _streaming_overlap(pattern: re.Pattern[str]) -> int | None:
    """Overlap for exact chunked matching, or None for unsafe long lines."""
    text = pattern.pattern
    unsafe_fragments = ("^", "$", "*", "+", "{", "(?", r"\A", r"\Z", r"\b", r"\B", r"\1")
    if any(fragment in text for fragment in unsafe_fragments):
        return None
    parser: Any = re.__dict__["_parser"]
    parsed = cast(
        _ParsedPattern,
        parser.parse(
            text,
            pattern.flags,
        ),
    )
    _, max_width = parsed.getwidth()
    if max_width > _MAX_REGEX_OVERLAP_CHARS:
        return None
    return max(0, max_width - 1)
