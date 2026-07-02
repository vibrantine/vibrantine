"""ListDir tool: list the immediate contents of a directory.

`ls`-shaped alternative to Glob for the "what's in here" question.
Glob with `*` could cover the same ground, but ListDir maps onto how
humans think about file systems and returns each entry's type so an
agent can decide whether to recurse, sample, or read.
"""

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import ZERO_COST, failure, provenance

type DirEntryKind = Literal["file", "directory", "symlink", "other"]


class DirEntry(BaseModel):
    """One entry in a directory listing."""

    name: str = Field(description="Name of the entry (basename, not full path).")
    kind: DirEntryKind = Field(description="Category of the entry.")


class ListDirInput(BaseModel):
    """Inputs for one directory listing."""

    path: Path = Field(description="Absolute path of the directory to list.")
    max_entries: int = Field(
        default=1000,
        description=(
            "Maximum number of entries to return. Bounds output size; the "
            "result's `truncated` flag indicates when this cap is hit."
        ),
        gt=0,
    )


class ListDirOutput(BaseModel):
    """The listed directory and its immediate contents."""

    path: Path = Field(description="Absolute path of the listed directory.")
    entries: list[DirEntry] = Field(
        description="Immediate children of the directory, sorted by name.",
    )
    truncated: bool = Field(
        description="True if more entries exist beyond `max_entries`.",
    )
    total_entries: int = Field(
        description="Total number of entries in the directory.",
    )


class ListDirTool(Commission[ListDirInput, ListDirOutput]):
    """List the immediate contents of a directory with entry kinds."""

    name: ClassVar[str] = "list_dir"
    description: ClassVar[str] = (
        "Lists the immediate contents of a directory.\n"
        "\n"
        "Usage:\n"
        "- `path` must be absolute and a directory.\n"
        "- Returns direct children only — not recursive. For recursive\n"
        "  discovery use `glob` with `**` patterns.\n"
        "- Each entry has a `kind`: `file`, `directory`, `symlink`, or\n"
        "  `other`. Use it to decide whether to recurse, sample, or read.\n"
        "- `max_entries` (default 1000) bounds output size. If `truncated`\n"
        "  is true, more entries exist — use `glob` with a narrower pattern\n"
        "  or raise the cap (mind the context budget).\n"
        "\n"
        "Returns `path`, `entries` (sorted by name), `truncated`, and\n"
        "`total_entries` (the full count)."
    )
    input_type: ClassVar[type] = ListDirInput
    output_type: ClassVar[type] = ListDirOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
        self,
        input: ListDirInput,
        ctx: CallContext,
    ) -> CommissionResult[ListDirOutput]:
        prov = provenance(str(input.path))

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before listing began.",
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

        if not input.path.is_dir():
            return failure(
                "validation",
                f"Path is not a directory: {input.path!s}.",
                retryable=False,
                provenance=prov,
            )

        try:
            children = sorted(input.path.iterdir(), key=lambda p: p.name)
        except PermissionError as exc:
            return failure(
                "internal",
                f"Permission denied listing {input.path!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )
        except OSError as exc:
            return failure(
                "internal",
                f"Filesystem error listing {input.path!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )

        total = len(children)
        entries = [DirEntry(name=p.name, kind=_classify(p)) for p in children[: input.max_entries]]

        return CommissionResult[ListDirOutput](
            status="success",
            output=ListDirOutput(
                path=input.path,
                entries=entries,
                truncated=total > input.max_entries,
                total_entries=total,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )


def _classify(p: Path) -> DirEntryKind:
    """Categorise a path. is_symlink first because symlinks satisfy is_file/is_dir."""
    if p.is_symlink():
        return "symlink"
    if p.is_file():
        return "file"
    if p.is_dir():
        return "directory"
    return "other"
