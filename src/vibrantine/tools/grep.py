"""Grep tool: search file contents for a regex pattern.

Content-search primitive. Files are searched directly; directories are
walked recursively. Pair with Glob (find by name) for full-filesystem
discovery; pair with Read or Sample once an interesting match is found.

The first tool whose result size can grow alarmingly — `max_matches`
bounds it, with `truncated=True` signalling more remains. This is the
tool that surfaces the tool-result-budgeting design question
concretely (see `docs/composition.md § Output discipline`).

Binary files (any read that raises UnicodeDecodeError) are skipped
silently during directory walks; on a direct file path, the same error
surfaces as an `internal` failure so the caller knows the requested
file was unreadable.
"""

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult, ErrorKind
from vibrantine.tools._helpers import ZERO_COST, failure, provenance


class GrepMatch(BaseModel):
    """One matching line, with its file and position."""

    path: Path = Field(description="Absolute path of the file containing the match.")
    line_number: int = Field(description="1-based line number of the match.")
    line: str = Field(description="The matching line's full text, without trailing newline.")


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
        "  are walked recursively. Binary files are skipped silently during\n"
        "  directory walks.\n"
        "- `max_matches` (default 100) bounds output size. Check the\n"
        "  `truncated` flag — if true, narrow your pattern or raise the cap\n"
        "  (mind the context budget).\n"
        "- Set `ignore_case=true` for case-insensitive matching.\n"
        "\n"
        "Returns `matches` (list of `path`, `line_number`, `line`) and\n"
        "`truncated`."
    )
    input_type: ClassVar[type] = GrepInput
    output_type: ClassVar[type] = GrepOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
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
            file_matches, file_err = _grep_file(
                file,
                compiled,
                input.path.is_file(),
            )
            if file_err is not None:
                return failure(
                    file_err[0],
                    file_err[1],
                    retryable=False,
                    provenance=prov,
                )
            for m in file_matches:
                if len(matches) >= input.max_matches:
                    truncated = True
                    break
                matches.append(m)
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
    raise_on_decode_error: bool,
) -> tuple[list[GrepMatch], tuple[ErrorKind, str] | None]:
    """Search one file. Returns (matches, error-tuple).

    `raise_on_decode_error=True` surfaces the encoding failure as an
    error tuple (kind, detail); False skips the file silently (the
    directory-walk case).
    """
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], ("validation", f"File not found: {file!s}.")
    except PermissionError:
        # Skip unreadable files during directory walks; surface on direct paths.
        if raise_on_decode_error:
            return [], ("internal", f"Permission denied reading {file!s}.")
        return [], None
    except UnicodeDecodeError as exc:
        if raise_on_decode_error:
            return [], ("internal", f"Could not decode {file!s} as UTF-8: {exc}.")
        return [], None
    matches = [
        GrepMatch(path=file, line_number=i + 1, line=line)
        for i, line in enumerate(text.splitlines())
        if pattern.search(line)
    ]
    return matches, None
