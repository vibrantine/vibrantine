"""Write tool: create or overwrite a file with the given content.

Completes the `C` in text CRUD. Prefer `edit` when only a small portion
of an existing file needs to change; `write` is the right tool when the
new content bears no resemblance to the old. Parent directories are
created automatically (matches Claude Code's Write behavior).
Overwriting an existing file is destructive; as with `delete`, gating
and confirmation are the caller's policy (capabilities, or the
application layer above), and `create_only=True` is the tool-level
refusal to overwrite.
"""

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import ZERO_COST, failure, provenance


class WriteInput(BaseModel):
    """Inputs for one file write."""

    path: Path = Field(description="Absolute path of the file to write.")
    content: str = Field(description="Text content to write to the file.")
    create_only: bool = Field(
        default=False,
        description=(
            "If true, the call fails when the target already exists. "
            "Use to guard against accidental overwrites."
        ),
    )


class WriteOutput(BaseModel):
    """Attestation that the write happened."""

    path: Path = Field(description="Absolute path of the file written.")
    bytes_written: int = Field(description="Number of bytes written to the file.")


class WriteTool(Commission[WriteInput, WriteOutput]):
    """Write a file to the local filesystem; create or overwrite."""

    name: ClassVar[str] = "write"
    description: ClassVar[str] = (
        "Writes content to a file on the local filesystem, creating or\n"
        "overwriting it.\n"
        "\n"
        "Usage:\n"
        "- `path` must be absolute, not relative.\n"
        "- Set `create_only=true` to fail if the target already exists.\n"
        "- Parent directories are created automatically.\n"
        "- For small modifications to an existing file, prefer `edit`.\n"
        "\n"
        "Returns `path` and `bytes_written` (UTF-8 byte count)."
    )
    input_type: ClassVar[type] = WriteInput
    output_type: ClassVar[type] = WriteOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def _run(
        self,
        input: WriteInput,
        ctx: CallContext,
    ) -> CommissionResult[WriteOutput]:
        prov = provenance(str(input.path))

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before file write began.",
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

        if input.create_only and input.path.exists():
            return failure(
                "validation",
                f"create_only is true but target already exists: {input.path!s}.",
                retryable=False,
                provenance=prov,
            )

        # Encode explicitly + write_bytes: write_text returns *character* count
        # (not bytes) and translates \n → \r\n on Windows in text mode.
        encoded = input.content.encode("utf-8")
        try:
            input.path.parent.mkdir(parents=True, exist_ok=True)
            input.path.write_bytes(encoded)
        except (FileExistsError, NotADirectoryError) as exc:
            # A parent component of `path` is an existing file: a caller
            # path mistake, not a filesystem malfunction.
            return failure(
                "validation",
                f"A parent of {input.path!s} is not a directory: {exc}.",
                retryable=False,
                provenance=prov,
            )
        except PermissionError as exc:
            return failure(
                "internal",
                f"Permission denied writing {input.path!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )
        except OSError as exc:
            return failure(
                "internal",
                f"Filesystem error writing {input.path!s}: {exc}.",
                retryable=False,
                provenance=prov,
            )

        return CommissionResult[WriteOutput](
            status="success",
            output=WriteOutput(path=input.path, bytes_written=len(encoded)),
            provenance=prov,
            cost=ZERO_COST,
        )
