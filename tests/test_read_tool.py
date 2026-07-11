"""Tests for the Read tool."""

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vibrantine import run_one
from vibrantine.tools.read import ReadInput, ReadTool


class _AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


def _make_file(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    # newline="" so the fixture holds exactly these bytes on every
    # platform; the tool's reads are byte-faithful and would surface
    # the CRLF that plain write_text inserts on Windows.
    p.write_text(content, encoding="utf-8", newline="")
    return p


async def test_read_success_returns_content_and_metadata(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "hello.txt", "alpha\nbeta\ngamma\n")
    before = datetime.now(UTC)

    result = await run_one(ReadTool(), ReadInput(path=path))

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.content == "alpha\nbeta\ngamma\n"
    assert result.output.line_count == 3
    assert result.output.total_lines == 3
    assert result.output.truncated is False
    assert result.provenance.source == str(path)
    assert result.provenance.confidence == "grounded"
    skew = timedelta(seconds=5)
    after = datetime.now(UTC)
    assert before - skew <= result.provenance.fetched_at <= after + skew
    assert result.cost.estimated_usd == 0.0
    # None, not 0: no LLM was involved in a deterministic tool run.
    assert result.cost.in_tokens is None
    assert result.cost.out_tokens is None


async def test_read_preserves_crlf_line_endings(tmp_path: Path) -> None:
    # The description promises content faithful to the file's bytes,
    # so CRLF endings must survive the read untranslated.
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"alpha\r\nbeta\r\n")

    result = await run_one(ReadTool(), ReadInput(path=path))

    assert result.status == "success"
    assert result.output is not None
    assert result.output.content == "alpha\r\nbeta\r\n"
    assert result.output.total_lines == 2


async def test_read_empty_file_returns_zero_lines(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "empty.txt", "")

    result = await run_one(ReadTool(), ReadInput(path=path))

    assert result.status == "success"
    assert result.output is not None
    assert result.output.content == ""
    assert result.output.line_count == 0
    assert result.output.total_lines == 0
    assert result.output.truncated is False


async def test_read_slicing_returns_correct_window(tmp_path: Path) -> None:
    content = "".join(f"line{i}\n" for i in range(10))
    path = _make_file(tmp_path, "ten.txt", content)

    result = await run_one(
        ReadTool(),
        ReadInput(path=path, offset=3, limit=4),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.content == "line3\nline4\nline5\nline6\n"
    assert result.output.line_count == 4
    assert result.output.total_lines == 10
    assert result.output.truncated is True


async def test_read_truncated_false_when_slice_reaches_end(tmp_path: Path) -> None:
    content = "".join(f"line{i}\n" for i in range(5))
    path = _make_file(tmp_path, "five.txt", content)

    result = await run_one(
        ReadTool(),
        ReadInput(path=path, offset=3, limit=10),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.line_count == 2
    assert result.output.truncated is False


async def test_read_offset_past_end_returns_empty_slice(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "small.txt", "one\ntwo\n")

    result = await run_one(
        ReadTool(),
        ReadInput(path=path, offset=100, limit=10),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.content == ""
    assert result.output.line_count == 0
    assert result.output.total_lines == 2
    assert result.output.truncated is False


async def test_read_oversized_line_is_truncated_with_marker(tmp_path: Path) -> None:
    long_line = "x" * 35_000 + "\n"
    path = _make_file(tmp_path, "long.txt", "short\n" + long_line + "trailer\n")

    result = await run_one(ReadTool(), ReadInput(path=path))

    assert result.status == "success"
    assert result.output is not None
    assert "short\n" in result.output.content
    assert "trailer\n" in result.output.content
    assert "…[truncated]" in result.output.content
    # The truncated line is at most ~30k chars long + marker; full 35k must
    # not survive.
    assert "x" * 35_000 not in result.output.content


async def test_read_relative_path_returns_validation(tmp_path: Path) -> None:
    result = await run_one(
        ReadTool(),
        ReadInput(path=Path("relative/path.txt")),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert result.error.retryable is False


async def test_read_nonexistent_path_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.txt"

    result = await run_one(ReadTool(), ReadInput(path=missing))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "not found" in result.error.detail.lower()


async def test_read_directory_path_returns_validation(tmp_path: Path) -> None:
    result = await run_one(ReadTool(), ReadInput(path=tmp_path))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "director" in result.error.detail.lower()


async def test_read_binary_file_returns_internal_for_encoding(tmp_path: Path) -> None:
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01\x02\x03\xff")

    result = await run_one(ReadTool(), ReadInput(path=binary))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "utf-8" in result.error.detail.lower()


async def test_read_cancelled_before_read_returns_cancelled(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "any.txt", "ignored\n")

    result = await run_one(
        ReadTool(),
        ReadInput(path=path),
        cancel=_AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert result.error.retryable is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses chmod permission bits, so the read succeeds",
)
async def test_read_permission_denied_returns_internal(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "locked.txt", "secret\n")
    path.chmod(0o000)
    try:
        result = await run_one(ReadTool(), ReadInput(path=path))
    finally:
        path.chmod(0o644)

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "permission" in result.error.detail.lower()
