"""Tests for the Sample tool."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from vibrantine import run_one
from vibrantine.tools.sample import (
    LINE_CAP_CHARS,
    SampleInput,
    SampleTool,
    _sample_file,  # pyright: ignore[reportPrivateUsage]
)


class _AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


def _make(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_bytes(content.encode("utf-8"))
    return p


async def test_sample_small_file_returns_full_head_and_tail(tmp_path: Path) -> None:
    path = _make(tmp_path, "small.txt", "one\ntwo\nthree\n")
    before = datetime.now(UTC)

    result = await run_one(
        SampleTool(),
        SampleInput(path=path),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.path == path
    assert result.output.size_bytes == len(b"one\ntwo\nthree\n")
    assert result.output.line_count == 3
    assert result.output.head == ["one", "two", "three"]
    assert result.output.tail == ["one", "two", "three"]
    skew = timedelta(seconds=10)
    after = datetime.now(UTC)
    assert before - skew <= result.output.modified_at <= after + skew


async def test_sample_large_file_truncates_to_head_and_tail(tmp_path: Path) -> None:
    content = "".join(f"line{i}\n" for i in range(100))
    path = _make(tmp_path, "many.txt", content)

    result = await run_one(
        SampleTool(),
        SampleInput(path=path, head_lines=3, tail_lines=2),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.line_count == 100
    assert result.output.head == ["line0", "line1", "line2"]
    assert result.output.tail == ["line98", "line99"]


async def test_sample_head_zero_returns_empty_head(tmp_path: Path) -> None:
    path = _make(tmp_path, "doc.txt", "a\nb\nc\nd\n")

    result = await run_one(
        SampleTool(),
        SampleInput(path=path, head_lines=0, tail_lines=2),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.head == []
    assert result.output.tail == ["c", "d"]


async def test_sample_empty_file(tmp_path: Path) -> None:
    path = _make(tmp_path, "empty.txt", "")

    result = await run_one(
        SampleTool(),
        SampleInput(path=path),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.size_bytes == 0
    assert result.output.line_count == 0
    assert result.output.head == []
    assert result.output.tail == []


async def test_sample_relative_path_returns_validation(tmp_path: Path) -> None:
    result = await run_one(
        SampleTool(),
        SampleInput(path=Path("relative.txt")),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_sample_nonexistent_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"

    result = await run_one(
        SampleTool(),
        SampleInput(path=missing),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "not found" in result.error.detail.lower()


async def test_sample_directory_returns_validation(tmp_path: Path) -> None:
    result = await run_one(
        SampleTool(),
        SampleInput(path=tmp_path),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "director" in result.error.detail.lower()


async def test_sample_binary_returns_internal(tmp_path: Path) -> None:
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01")

    result = await run_one(
        SampleTool(),
        SampleInput(path=binary),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "utf-8" in result.error.detail.lower()


async def test_sample_cancelled_returns_cancelled(tmp_path: Path) -> None:
    path = _make(tmp_path, "any.txt", "x")

    result = await run_one(
        SampleTool(),
        SampleInput(path=path),
        cancel=_AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"


# --- streaming sampler: line cap and chunk-boundary fidelity ----------------


async def test_sample_long_line_is_capped_with_marker(tmp_path: Path) -> None:
    # The single-line pathology (minified JS, JSONL): the returned payload
    # stays cheap, the count stays exact, and the cut is visibly marked.
    long_line = "x" * (LINE_CAP_CHARS * 3)
    path = _make(tmp_path, "minified.js", f"{long_line}\nshort\n")

    result = await run_one(SampleTool(), SampleInput(path=path))

    assert result.status == "success"
    assert result.output is not None
    assert result.output.line_count == 2
    assert result.output.head[0] == "x" * LINE_CAP_CHARS + "... [line truncated]"
    assert result.output.head[1] == "short"


async def test_sample_line_at_exact_cap_is_untouched(tmp_path: Path) -> None:
    exact = "y" * LINE_CAP_CHARS
    path = _make(tmp_path, "exact.txt", f"{exact}\n")

    result = await run_one(SampleTool(), SampleInput(path=path))

    assert result.status == "success"
    assert result.output is not None
    assert result.output.head == [exact]


def test_sample_file_matches_splitlines_across_chunk_boundaries(tmp_path: Path) -> None:
    # A CRLF pair and a multibyte character straddling read chunks must not
    # change the result: tiny chunk sizes force every straddle to happen.
    content = "alpha\r\nbé\rgamma delta\nomega"
    path = tmp_path / "endings.txt"
    path.write_bytes(content.encode("utf-8"))
    expected = content.splitlines()

    for chunk_bytes in (1, 2, 3, 7, 64 * 1024):
        head, tail, count = _sample_file(path, 10, 10, chunk_bytes=chunk_bytes)
        assert head == expected, f"chunk_bytes={chunk_bytes}"
        assert tail == expected, f"chunk_bytes={chunk_bytes}"
        assert count == len(expected), f"chunk_bytes={chunk_bytes}"


def test_sample_file_caps_memory_mid_line(tmp_path: Path) -> None:
    # A long line arriving in many small chunks is trimmed while it
    # accumulates (not only at the end), and still counts as one line.
    long_line = "z" * (LINE_CAP_CHARS * 5)
    path = tmp_path / "one-line.txt"
    path.write_bytes(long_line.encode("utf-8"))

    head, tail, count = _sample_file(path, 10, 10, chunk_bytes=512)

    assert count == 1
    assert head == ["z" * LINE_CAP_CHARS + "... [line truncated]"]
    assert tail == head
