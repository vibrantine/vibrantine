"""Delete tool: remove a file from the filesystem.

The `D` in text CRUD. Destructive and irreversible at the tool level.
The caller (commission or its consuming LLM) is responsible for
confirmation or capability gating; this tool just deletes the named
file when invoked.

Directories are intentionally out of scope — `path` must be a regular
file. A separate RemoveDirectory tool would handle that case if it
ever earns its slot.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import ZERO_COST, failure, provenance


class DeleteInput(BaseModel):
    """Inputs for one file deletion."""

    path: Path = Field(description="Absolute path of the file to delete.")


class DeleteOutput(BaseModel):
    """Attestation of the deletion."""

    path: Path = Field(description="Absolute path of the file deleted.")


class DeleteTool(Commission[DeleteInput, DeleteOutput]):
    """Delete a file from the local filesystem. Irreversible."""

    name: ClassVar[str] = "delete"
    description: ClassVar[str] = (
        "Deletes a file from the local filesystem. Irreversible.\n"
        "\n"
        "Usage:\n"
        "- `path` must be absolute.\n"
        "- `path` must point to a regular file, not a directory.\n"
        "- Confirm intent before calling — no recovery is available\n"
        "  through this tool.\n"
        "\n"
        "Returns `path` (for confirmation)."
    )
    input_type: ClassVar[type] = DeleteInput
    output_type: ClassVar[type] = DeleteOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
        self,
        input: DeleteInput,
        ctx: CallContext,
    ) -> CommissionResult[DeleteOutput]:
        prov = provenance(f"delete:{input.path}")

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before delete began.",
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

        if input.path.is_dir():
            return failure(
                "validation",
                f"Path is a directory, not a file: {input.path!s}.",
                retryable=False,
                provenance=prov,
            )

        try:
            input.path.unlink()
        except PermissionError as exc:
            return failure(
                "internal",
                f"Permission denied deleting {input.path!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )
        except OSError as exc:
            return failure(
                "internal",
                f"Filesystem error deleting {input.path!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )

        return CommissionResult[DeleteOutput](
            status="success",
            output=DeleteOutput(path=input.path),
            provenance=prov,
            cost=ZERO_COST,
        )
