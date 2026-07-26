"""Shell tool: run a shell command and capture its output.

The escape-hatch tool: anything the std lib doesn't have a dedicated
tool for, an LLM can fall back to via Shell. The most powerful and
most side-effectful tool in the std lib; placed last in the build
order so the protocol's failure surface was well-exercised on simpler
tools first. Gating and confirmation are the caller's policy
(capabilities, or the application layer above); the tool runs what it
is told.

Captures stdout, stderr, exit code, and wall-clock runtime. Honors a
timeout; exceeding it terminates the process and returns
ErrorState(kind="timeout", retryable=True). The kill targets the shell
process itself; on Windows especially, grandchildren the shell spawned
may survive it (no process-tree kill).

Output is decoded as strict UTF-8 first (the modern norm on every
platform); bytes that refuse that reading fall back to the machine's
legacy codepage (the OEM codepage on Windows, where console-era
programs still write it), so accented filenames and localized messages
reach the LLM as text instead of replacement characters.

Host shell is whatever asyncio.create_subprocess_shell uses on the
running platform (cmd.exe on Windows, /bin/sh on POSIX). The agent's
prompt is responsible for knowing which shell semantics apply; this
tool does not paper over the difference.
"""

import asyncio
import codecs
import locale
import sys
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, ClassVar, Final, cast

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission, CommissionResult
from vibrantine.tools._helpers import ZERO_COST, failure, provenance

DEFAULT_MAX_OUTPUT_CHARS: int = 30_000

# The codebook for bytes that provably aren't UTF-8. Windows console-era
# programs (cmd.exe built-ins among them) write the machine's OEM codepage,
# for which Python ships the "oem" alias; on POSIX the locale encoding is
# the only other plausible reading. Where the locale is itself UTF-8 (the
# POSIX norm), the fallback degrades to today's replace behavior.
_FALLBACK_ENCODING: Final[str] = (
    "oem" if sys.platform == "win32" else locale.getpreferredencoding(False)
)


def _decode(  # pyright: ignore[reportUnusedFunction]
    data: bytes,
    fallback: str = _FALLBACK_ENCODING,
) -> str:
    """Decode command output: strict UTF-8 first, legacy codepage second.

    UTF-8 is self-validating, so a strict decode succeeding means the bytes
    almost certainly were UTF-8. Bytes that refuse that reading are the
    legacy tail; reading them with the machine's own codebook beats
    rendering them as replacement characters inside a "success" result.
    `errors="replace"` survives only as the final backstop.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(fallback, errors="replace")


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
            "so anything past the cap is dropped; narrow the command (pipe "
            "through grep/tail), redirect to a file and Read it, or raise the cap."
        ),
        gt=0,
    )


class ShellOutput(BaseModel):
    """The captured outputs of one shell command."""

    exit_code: int = Field(description="Process exit code (0 = success in most shells).")
    stdout: str = Field(
        description=(
            "Captured standard output (UTF-8, with a legacy-codepage fallback "
            "for non-UTF-8 bytes), capped at max_output_chars."
        ),
    )
    stderr: str = Field(
        description=(
            "Captured standard error (UTF-8, with a legacy-codepage fallback "
            "for non-UTF-8 bytes), capped at max_output_chars."
        ),
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
        "  for the task; Shell is for cases not covered by them.\n"
        "- Non-zero `exit_code` is returned as success; interpretation is\n"
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
    deterministic: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def _run(
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

        with tempfile.TemporaryFile() as stdout_spool, tempfile.TemporaryFile() as stderr_spool:
            stdout_task = asyncio.create_task(
                _drain(cast(asyncio.StreamReader, proc.stdout), stdout_spool)
            )
            stderr_task = asyncio.create_task(
                _drain(cast(asyncio.StreamReader, proc.stderr), stderr_spool)
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=input.timeout_seconds)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                elapsed = time.monotonic() - start
                return failure(
                    "timeout",
                    f"Command exceeded {input.timeout_seconds}s timeout "
                    f"(killed at {elapsed:.2f}s).",
                    retryable=True,
                    provenance=prov,
                )
            try:
                await asyncio.gather(stdout_task, stderr_task)
                stdout, stdout_truncated, stdout_total = _decode_spooled(
                    stdout_spool,
                    input.max_output_chars,
                )
                stderr, stderr_truncated, stderr_total = _decode_spooled(
                    stderr_spool,
                    input.max_output_chars,
                )
            except OSError as exc:
                return failure(
                    "internal",
                    f"Failed while capturing shell output: {exc}.",
                    retryable=False,
                    provenance=prov,
                )

        runtime = time.monotonic() - start

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


async def _drain(stream: asyncio.StreamReader, spool: BinaryIO) -> None:
    """Drain one subprocess pipe to disk with fixed-size memory use."""
    while chunk := await stream.read(64 * 1024):
        spool.write(chunk)


def _decode_spooled(
    spool: BinaryIO,
    max_chars: int,
    fallback: str = _FALLBACK_ENCODING,
) -> tuple[str, bool, int]:
    """Decode a completed spool, retrying the legacy codepage if needed."""
    try:
        return _decode_spooled_as(spool, max_chars, "utf-8", errors="strict")
    except UnicodeDecodeError:
        return _decode_spooled_as(spool, max_chars, fallback, errors="replace")


def _decode_spooled_as(
    spool: BinaryIO,
    max_chars: int,
    encoding: str,
    *,
    errors: str,
) -> tuple[str, bool, int]:
    spool.seek(0)
    decoder_type = codecs.getincrementaldecoder(encoding)
    decoder = decoder_type(errors=errors)
    kept: list[str] = []
    kept_chars = 0
    total = 0

    while data := spool.read(64 * 1024):
        text = decoder.decode(data)
        total += len(text)
        if kept_chars < max_chars:
            part = text[: max_chars - kept_chars]
            kept.append(part)
            kept_chars += len(part)

    tail = decoder.decode(b"", final=True)
    total += len(tail)
    if kept_chars < max_chars:
        kept.append(tail[: max_chars - kept_chars])
    return "".join(kept), total > max_chars, total
