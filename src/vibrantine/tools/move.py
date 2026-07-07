"""Move tool: move or rename a file.

One tool covers both rename and move because Unix `mv` does, and
splitting them adds no semantic value. Mildly destructive: with
`overwrite=True` and an existing target, the target's content is
lost; default `overwrite=False` rejects that case as a validation
error.
"""

import os
import shutil
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import ZERO_COST, failure, provenance


class MoveInput(BaseModel):
    """Inputs for one file move/rename."""

    source: Path = Field(description="Absolute path of the source file.")
    target: Path = Field(description="Absolute path of the destination.")
    overwrite: bool = Field(
        default=False,
        description=(
            "If true, overwrite `target` if it already exists. "
            "Defaults to false to avoid silent data loss."
        ),
    )


class MoveOutput(BaseModel):
    """Attestation of the move."""

    source: Path = Field(description="Absolute path the file was moved from.")
    target: Path = Field(description="Absolute path the file was moved to.")


class MoveTool(Commission[MoveInput, MoveOutput]):
    """Move or rename a file from source to target."""

    name: ClassVar[str] = "move"
    description: ClassVar[str] = (
        "Moves or renames a file. Covers both same-directory renames and\n"
        "cross-directory moves; the operation is identical.\n"
        "\n"
        "Usage:\n"
        "- Both `source` and `target` must be absolute paths.\n"
        "- `source` must exist and be a regular file.\n"
        "- By default the call fails if `target` already exists; set\n"
        "  `overwrite=true` to replace an existing target.\n"
        "- The target's parent directory must exist.\n"
        "- If `target` is an existing directory (reachable only with\n"
        "  `overwrite=true`), the file is moved INTO that directory; the\n"
        "  returned `target` is the file's actual new path.\n"
        "\n"
        "Returns `source` (for confirmation) and `target` (the actual new\n"
        "path the file landed at)."
    )
    input_type: ClassVar[type] = MoveInput
    output_type: ClassVar[type] = MoveOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
        self,
        input: MoveInput,
        ctx: CallContext,
    ) -> CommissionResult[MoveOutput]:
        prov = provenance(f"move:{input.source}->{input.target}")

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before move began.",
                retryable=False,
                provenance=prov,
            )

        if not input.source.is_absolute():
            return failure(
                "validation",
                f"source must be absolute; got {input.source!s}.",
                retryable=False,
                provenance=prov,
            )

        if not input.target.is_absolute():
            return failure(
                "validation",
                f"target must be absolute; got {input.target!s}.",
                retryable=False,
                provenance=prov,
            )

        # lexists on both checks: a broken symlink is still a real entry;
        # exists() follows the link and would misreport it as absent.
        if not os.path.lexists(input.source):
            return failure(
                "validation",
                f"source does not exist: {input.source!s}.",
                retryable=False,
                provenance=prov,
            )

        if not input.source.is_file():
            return failure(
                "validation",
                f"source is not a regular file: {input.source!s}.",
                retryable=False,
                provenance=prov,
            )

        if os.path.lexists(input.target) and not input.overwrite:
            return failure(
                "validation",
                f"target already exists and overwrite is false: {input.target!s}.",
                retryable=False,
                provenance=prov,
            )

        if not input.target.parent.exists():
            return failure(
                "validation",
                f"target parent directory does not exist: {input.target.parent!s}.",
                retryable=False,
                provenance=prov,
            )

        try:
            # Attest where the file actually landed: when target is an
            # existing directory, shutil.move relocates the file INTO it
            # and returns that inner path, not the requested target.
            moved_to = Path(shutil.move(str(input.source), str(input.target)))
        except PermissionError as exc:
            return failure(
                "internal",
                f"Permission denied moving {input.source!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )
        except OSError as exc:
            return failure(
                "internal",
                f"Filesystem error moving {input.source!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )

        return CommissionResult[MoveOutput](
            status="success",
            output=MoveOutput(source=input.source, target=moved_to),
            provenance=prov,
            cost=ZERO_COST,
        )
