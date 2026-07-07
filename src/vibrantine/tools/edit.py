"""Edit tool: string replacement in a file (the `U` in text CRUD).

Cheaper than Write for small modifications because the LLM only emits
the changed substring. Models Claude Code's Edit: `old_string` must
match exactly, once by default. Set `replace_all=True` to substitute
every occurrence. Empty or zero-match `old_string` fails with a
validation error; no silent no-ops.
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


class EditInput(BaseModel):
    """Inputs for one in-place file edit."""

    path: Path = Field(description="Absolute path of the file to edit.")
    old_string: str = Field(
        description=(
            "Exact substring to replace. Must appear in the file. If "
            "`replace_all` is false (default), must appear exactly once."
        ),
    )
    new_string: str = Field(description="Replacement string for `old_string`.")
    replace_all: bool = Field(
        default=False,
        description="If true, replace every occurrence of `old_string`.",
    )


class EditOutput(BaseModel):
    """Attestation of the edit, with replacement count."""

    path: Path = Field(description="Absolute path of the file edited.")
    replacements_made: int = Field(
        description="Number of substitutions performed (1 unless `replace_all`).",
    )


class EditTool(Commission[EditInput, EditOutput]):
    """Replace an exact substring in a file; the cheap U in text CRUD."""

    name: ClassVar[str] = "edit"
    description: ClassVar[str] = (
        "Replaces an exact substring in a file with new content.\n"
        "\n"
        "Usage:\n"
        "- `path` must be absolute, not relative.\n"
        "- `old_string` must match the file's content exactly, character for\n"
        "  character including whitespace and indentation.\n"
        "- Must occur exactly once by default; the call fails if it appears\n"
        "  zero times or more than once. Set `replace_all=true` to substitute\n"
        "  every match.\n"
        "- Empty `old_string` is rejected.\n"
        "- Cheaper than `write` when only a small portion changes.\n"
        "\n"
        "Returns `path` and `replacements_made`."
    )
    input_type: ClassVar[type] = EditInput
    output_type: ClassVar[type] = EditOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
        self,
        input: EditInput,
        ctx: CallContext,
    ) -> CommissionResult[EditOutput]:
        prov = provenance(str(input.path))

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before file edit began.",
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

        if not input.old_string:
            return failure(
                "validation",
                "`old_string` must be non-empty.",
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
            text = read_text_utf8(input.path)
        except ReadFailure as exc:
            return failure(exc.kind, exc.detail, retryable=False, provenance=prov)

        match_count = text.count(input.old_string)
        if match_count == 0:
            return failure(
                "validation",
                f"`old_string` did not match any content in {input.path!s}.",
                retryable=False,
                provenance=prov,
            )
        if match_count > 1 and not input.replace_all:
            return failure(
                "validation",
                (
                    f"`old_string` matched {match_count} times in "
                    f"{input.path!s}; set `replace_all=true` or narrow the match."
                ),
                retryable=False,
                provenance=prov,
            )

        replacements = match_count if input.replace_all else 1
        new_text = text.replace(input.old_string, input.new_string, replacements)

        try:
            input.path.write_bytes(new_text.encode("utf-8"))
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

        return CommissionResult[EditOutput](
            status="success",
            output=EditOutput(path=input.path, replacements_made=replacements),
            provenance=prov,
            cost=ZERO_COST,
        )
