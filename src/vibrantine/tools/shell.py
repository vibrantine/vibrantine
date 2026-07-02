"""Shell tool: run a shell command and capture its output.

The escape-hatch tool — anything the std lib doesn't have a dedicated
tool for, an LLM can fall back to via Shell. The most powerful and
most side-effectful tool in the std lib; placed last in the build
order so the protocol's failure surface was well-exercised on simpler
tools first.

Captures stdout, stderr, exit code, and wall-clock runtime. Honours a
timeout — exceeding it terminates the process and returns
ErrorState(kind="timeout", retryable=True). The kill targets the shell
process itself; on Windows especially, grandchildren the shell spawned
may survive it (no process-tree kill).

Host shell is whatever asyncio.create_subprocess_shell uses on the
running platform (cmd.exe on Windows, /bin/sh on POSIX). The agent's
prompt is responsible for knowing which shell semantics apply; this
tool does not paper over the difference.
"""

import asyncio
import time
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import ZERO_COST, failure, provenance

DEFAULT_MAX_OUTPUT_CHARS: int = 30_000


class ShellInput(BaseModel):
    """Inputs for one shell command execution."""

    command: str = Field(description="The shell command to execute.")
    cwd: Path | None = Field(
        default=None,
        description=(
            "Absolute working directory for the command. If omitted, the "
            "current working directory of the host process is used."
        ),
    )
    timeout_seconds: float = Field(
        default=30.0,
        description=(
            "Wall-clock timeout for the command. Exceeding this terminates "
            'the process and returns ErrorState(kind="timeout").'
        ),
        gt=0.0,
    )
    max_output_chars: int = Field(
        default=DEFAULT_MAX_OUTPUT_CHARS,
        description=(
            "Maximum characters of stdout and of stderr to return (each capped "
            "independently). A finished command's output can't be re-paginated, "
            "so anything past the cap is dropped — narrow the command (pipe "
            "through grep/tail), redirect to a file and Read it, or raise the cap."
        ),
        gt=0,
    )


class ShellOutput(BaseModel):
    """The captured outputs of one shell command."""

    exit_code: int = Field(description="Process exit code (0 = success in most shells).")
    stdout: str = Field(
        description="Captured standard output (decoded UTF-8), capped at max_output_chars.",
    )
    stderr: str = Field(
        description="Captured standard error (decoded UTF-8), capped at max_output_chars.",
    )
    runtime_seconds: float = Field(description="Wall-clock runtime of the command.")
    stdout_truncated: bool = Field(
        description="True if stdout exceeded max_output_chars and was cut.",
    )
    stderr_truncated: bool = Field(
        description="True if stderr exceeded max_output_chars and was cut.",
    )
    stdout_total_chars: int = Field(
        description="Full length of stdout before truncation, in characters.",
    )
    stderr_total_chars: int = Field(
        description="Full length of stderr before truncation, in characters.",
    )


class ShellTool(Commission[ShellInput, ShellOutput]):
    """Run a shell command and capture stdout/stderr/exit code."""

    name: ClassVar[str] = "shell"
    description: ClassVar[str] = (
        "Runs a shell command and captures stdout, stderr, exit code, and\n"
        "wall-clock runtime.\n"
        "\n"
        "Usage:\n"
        "- `command` is passed to the host shell (cmd.exe on Windows,\n"
        "  /bin/sh on POSIX). Quoting/escaping is your responsibility.\n"
        "- Be explicit if portability matters: full paths, no shell\n"
        "  built-ins that differ across platforms.\n"
        "- `cwd` should be absolute; defaults to the host's current dir.\n"
        "- `timeout_seconds` defaults to 30; raise for long-running cmds.\n"
        "  On timeout the shell process is killed; processes it spawned may\n"
        "  survive (especially on Windows).\n"
        "- Prefer specific tools (read, write, glob, etc.) when one exists\n"
        "  for the task — Shell is for cases not covered by them.\n"
        "- Non-zero `exit_code` is returned as success — interpretation is\n"
        "  the caller's job. Only timeouts and pre-launch failures return\n"
        "  ErrorState.\n"
        "- `stdout`/`stderr` are each capped at `max_output_chars` (default\n"
        "  30000). A finished command can't be re-paginated; if\n"
        "  `stdout_truncated`/`stderr_truncated` is set, narrow the command\n"
        "  (grep/tail), redirect to a file and Read it, or raise the cap.\n"
        "\n"
        "Returns `exit_code`, `stdout`, `stderr`, `runtime_seconds`, the\n"
        "`stdout_truncated`/`stderr_truncated` flags, and\n"
        "`stdout_total_chars`/`stderr_total_chars` (full lengths)."
    )
    input_type: ClassVar[type] = ShellInput
    output_type: ClassVar[type] = ShellOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
        self,
        input: ShellInput,
        ctx: CallContext,
    ) -> CommissionResult[ShellOutput]:
        prov = provenance(f"shell:{input.command[:80]}")

        if ctx.cancel.is_cancelled:
            return failure(
                "cancelled",
                "Cancelled before command was launched.",
                retryable=False,
                provenance=prov,
            )

        if input.cwd is not None and not input.cwd.is_absolute():
            return failure(
                "validation",
                f"cwd must be absolute; got {input.cwd!s}.",
                retryable=False,
                provenance=prov,
            )

        if input.cwd is not None and not input.cwd.is_dir():
            return failure(
                "validation",
                f"cwd does not exist or is not a directory: {input.cwd!s}.",
                retryable=False,
                provenance=prov,
            )

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                input.command,
                cwd=input.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return failure(
                "internal",
                f"Failed to launch shell command: {exc}.",
                retryable=False,
                provenance=prov,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=input.timeout_seconds,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            elapsed = time.monotonic() - start
            return failure(
                "timeout",
                f"Command exceeded {input.timeout_seconds}s timeout (killed at {elapsed:.2f}s).",
                retryable=True,
                provenance=prov,
            )

        runtime = time.monotonic() - start

        stdout, stdout_truncated, stdout_total = _cap(
            stdout_bytes.decode("utf-8", errors="replace"), input.max_output_chars
        )
        stderr, stderr_truncated, stderr_total = _cap(
            stderr_bytes.decode("utf-8", errors="replace"), input.max_output_chars
        )

        return CommissionResult[ShellOutput](
            status="success",
            output=ShellOutput(
                exit_code=proc.returncode if proc.returncode is not None else -1,
                stdout=stdout,
                stderr=stderr,
                runtime_seconds=runtime,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
                stdout_total_chars=stdout_total,
                stderr_total_chars=stderr_total,
            ),
            provenance=prov,
            cost=ZERO_COST,
        )


def _cap(text: str, max_chars: int) -> tuple[str, bool, int]:
    """Cap `text` to `max_chars`. Returns (kept, truncated, total_chars).

    Keeps the head: a completed command can't be re-paginated, so the start of
    the output (plus the separately-captured exit_code) is the predictable slice
    to retain. The caller narrows the command or raises the cap for more.
    """
    total = len(text)
    if total <= max_chars:
        return text, False, total
    return text[:max_chars], True, total
