"""Tests for the Shell tool.

Uses `python -c` for cross-platform commands; host shells differ
between Windows (cmd.exe) and POSIX (/bin/sh), but the Python
interpreter behaves identically on both.
"""

import sys
from pathlib import Path

from vibrantine.contract import CallContext
from vibrantine.dispatch import dispatch
from vibrantine.tools.shell import (
    ShellInput,
    ShellTool,
    _decode,  # pyright: ignore[reportPrivateUsage]
)


class _AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


_PYTHON = sys.executable


async def test_shell_success_captures_stdout(tmp_path: Path) -> None:
    result = await dispatch(
        ShellTool(),
        ShellInput(command=f'"{_PYTHON}" -c "print(\'hello\')"'),
        CallContext(),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.exit_code == 0
    assert "hello" in result.output.stdout
    assert result.output.runtime_seconds > 0
    assert result.cost.estimated_usd == 0.0


async def test_shell_non_zero_exit_returned_as_success(tmp_path: Path) -> None:
    # A non-zero exit is data the caller interprets, not a tool-level failure.
    result = await dispatch(
        ShellTool(),
        ShellInput(command=f'"{_PYTHON}" -c "import sys; sys.exit(7)"'),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.exit_code == 7


async def test_shell_stderr_separated_from_stdout(tmp_path: Path) -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr)"
    result = await dispatch(
        ShellTool(),
        ShellInput(command=f'"{_PYTHON}" -c "{code}"'),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert "out" in result.output.stdout
    assert "err" in result.output.stderr
    # Cross-check: out should NOT be in stderr (separation is the point).
    assert "out" not in result.output.stderr


async def test_shell_timeout_kills_process_and_returns_timeout(tmp_path: Path) -> None:
    code = "import time; time.sleep(5)"
    result = await dispatch(
        ShellTool(),
        ShellInput(
            command=f'"{_PYTHON}" -c "{code}"',
            timeout_seconds=0.5,
        ),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "timeout"
    assert result.error.retryable is True


async def test_shell_cancelled_before_launch_returns_cancelled(tmp_path: Path) -> None:
    result = await dispatch(
        ShellTool(),
        ShellInput(command=f'"{_PYTHON}" -c "print(\'should not run\')"'),
        CallContext(cancel=_AlwaysCancelled()),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"


async def test_shell_cwd_honored(tmp_path: Path) -> None:
    # Write a marker file in tmp_path; cwd into tmp_path; have the command
    # confirm the marker is present.
    (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
    code = "import os; print(os.path.exists('marker.txt'))"

    result = await dispatch(
        ShellTool(),
        ShellInput(command=f'"{_PYTHON}" -c "{code}"', cwd=tmp_path),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert "True" in result.output.stdout


async def test_shell_relative_cwd_returns_validation(tmp_path: Path) -> None:
    result = await dispatch(
        ShellTool(),
        ShellInput(command="echo hi", cwd=Path("relative")),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_shell_nonexistent_cwd_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"

    result = await dispatch(
        ShellTool(),
        ShellInput(command="echo hi", cwd=missing),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_shell_caps_stdout_and_flags_truncation(tmp_path: Path) -> None:
    # Emit 500 chars on stdout; cap at 100 → head kept, truncated, total reported.
    result = await dispatch(
        ShellTool(),
        ShellInput(
            command=f"\"{_PYTHON}\" -c \"print('x' * 500, end='')\"",
            max_output_chars=100,
        ),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert len(result.output.stdout) == 100
    assert result.output.stdout_truncated is True
    assert result.output.stdout_total_chars == 500
    # stderr was empty → not truncated.
    assert result.output.stderr_truncated is False
    assert result.output.stderr_total_chars == 0


async def test_shell_small_output_not_truncated(tmp_path: Path) -> None:
    result = await dispatch(
        ShellTool(),
        ShellInput(command=f"\"{_PYTHON}\" -c \"print('hi', end='')\""),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.stdout == "hi"
    assert result.output.stdout_truncated is False
    assert result.output.stdout_total_chars == 2


# --- output decoding: strict UTF-8 first, legacy codepage fallback ----------


def test_decode_valid_utf8_is_exact() -> None:
    assert _decode("café ✓".encode()) == "café ✓"


def test_decode_falls_back_to_legacy_codepage() -> None:
    # 0x82 is é in cp437 (a Windows OEM codepage) and invalid as UTF-8.
    # The fallback is passed explicitly so the test is deterministic on
    # every machine and platform.
    assert _decode(b"caf\x82", fallback="cp437") == "café"


def test_decode_replace_is_the_final_backstop() -> None:
    # Bytes unreadable in both codebooks degrade to replacement characters
    # rather than raising: the old behavior, now last resort.
    assert _decode(b"caf\xff", fallback="ascii") == "caf�"


async def test_shell_utf8_output_round_trips(tmp_path: Path) -> None:
    # End to end: genuine UTF-8 bytes from a real subprocess survive intact
    # on every platform (the strict-decode path, which is the modern norm).
    code = "import sys; sys.stdout.buffer.write('café'.encode('utf-8'))"
    result = await dispatch(
        ShellTool(),
        ShellInput(command=f'"{_PYTHON}" -c "{code}"'),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.stdout == "café"
