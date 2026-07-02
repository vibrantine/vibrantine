"""Glob tool: discover files by glob pattern.

Discovery primitive — the LLM names a pattern and gets back matching
paths without loading any content. Pairs with Read or Sample once the
agent has decided which matches to inspect, and with Grep for content
search. Cheap enough that an agent can run many Glob calls during
exploration.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import ZERO_COST, failure, provenance


class GlobInput(BaseModel):
    """Inputs for one glob search."""

    pattern: str = Field(
        description=(
            "Glob pattern, e.g., `**/*.py` or `docs/*.md`. Uses standard "
            "Python `pathlib` glob syntax."
        ),
    )
    base: Path | None = Field(
        default=None,
        description=(
            "Absolute base directory to glob from. If omitted, uses the current working directory."
        ),
    )
    max_matches: int = Field(
        default=1000,
        description=(
            "Maximum number of paths to return. Bounds output size; the "
            "result's `truncated` flag indicates when this cap is hit."
        ),
        gt=0,
    )


class GlobOutput(BaseModel):
    """Discovered paths matching the pattern."""

    matches: list[Path] = Field(
        description="Absolute paths of files matching the pattern.",
    )
    truncated: bool = Field(
        description="True if more matches exist beyond `max_matches`.",
    )
    total_matches: int = Field(
        description="Total number of files matching the pattern.",
    )


class GlobTool(Commission[GlobInput, GlobOutput]):
    """Discover files matching a glob pattern; no content load."""

    name: ClassVar[str] = "glob"
    description: ClassVar[str] = (
        "Discovers files matching a glob pattern. Returns paths only —\n"
        "does not load file content.\n"
        "\n"
        "Usage:\n"
        "- Pattern syntax: `*` matches one path segment, `**` matches any\n"
        "  number of segments, `?` matches one character, `[abc]` a set.\n"
        "- `base` should be an absolute directory; defaults to the current\n"
        "  working directory.\n"
        "- Returns files only — directories are filtered out. Use the\n"
        "  `list_dir` tool to list directory contents.\n"
        "- `max_matches` (default 1000) bounds output size. If `truncated`\n"
        "  is true, more matches exist — narrow the pattern or raise the\n"
        "  cap (mind the context budget).\n"
        "- Follow up with `read` or `sample` to inspect matches, or `grep`\n"
        "  to search them.\n"
        "\n"
        "Returns `matches` (absolute paths in sorted order), `truncated`,\n"
        "and `total_matches` (the full count)."
    )
    input_type: ClassVar[type] = GlobInput
    output_type: ClassVar[type] = GlobOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
        self,
        input: GlobInput,
        ctx: CallContext,
    ) -> CommissionResult[GlobOutput]:
        base = input.base if input.base is not None else Path.cwd()
        prov = provenance(f"glob:{input.pattern}@{base}")

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before glob began.",
                retryable=False,
                provenance=prov,
            )

        if input.base is not None and not input.base.is_absolute():
            return failure(
                "validation",
                f"`base` must be absolute; got {input.base!s}.",
                retryable=False,
                provenance=prov,
            )

        if not base.is_dir():
            return failure(
                "validation",
                f"`base` is not a directory: {base!s}.",
                retryable=False,
                provenance=prov,
            )

        try:
            # Sorting needs the full match list anyway, so the cap bounds the
            # returned payload (the context-budget concern), not the walk.
            matched = sorted(p for p in base.glob(input.pattern) if p.is_file())
        except PermissionError as exc:
            return failure(
                "internal",
                f"Permission denied globbing {base!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )
        except OSError as exc:
            return failure(
                "internal",
                f"Filesystem error globbing {base!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )

        total = len(matched)
        return CommissionResult[GlobOutput](
            status="success",
            output=GlobOutput(
                matches=matched[: input.max_matches],
                truncated=total > input.max_matches,
                total_matches=total,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )
