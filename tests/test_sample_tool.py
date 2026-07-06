"""Tests for the Sample tool."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from vibrantine.contract import CallContext
from vibrantine.dispatch import dispatch
from vibrantine.tools.sample import SampleInput, SampleTool


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

    result = await dispatch(SampleTool(), 
        SampleInput(path=path),
        CallContext(),
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

    result = await dispatch(SampleTool(), 
        SampleInput(path=path, head_lines=3, tail_lines=2),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.line_count == 100
    assert result.output.head == ["line0", "line1", "line2"]
    assert result.output.tail == ["line98", "line99"]


async def test_sample_head_zero_returns_empty_head(tmp_path: Path) -> None:
    path = _make(tmp_path, "doc.txt", "a\nb\nc\nd\n")

    result = await dispatch(SampleTool(), 
        SampleInput(path=path, head_lines=0, tail_lines=2),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.head == []
    assert result.output.tail == ["c", "d"]


async def test_sample_empty_file(tmp_path: Path) -> None:
    path = _make(tmp_path, "empty.txt", "")

    result = await dispatch(SampleTool(), 
        SampleInput(path=path),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.size_bytes == 0
    assert result.output.line_count == 0
    assert result.output.head == []
    assert result.output.tail == []


async def test_sample_relative_path_returns_validation(tmp_path: Path) -> None:
    result = await dispatch(SampleTool(), 
        SampleInput(path=Path("relative.txt")),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_sample_nonexistent_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"

    result = await dispatch(SampleTool(), 
        SampleInput(path=missing),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "not found" in result.error.detail.lower()


async def test_sample_directory_returns_validation(tmp_path: Path) -> None:
    result = await dispatch(SampleTool(), 
        SampleInput(path=tmp_path),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "director" in result.error.detail.lower()


async def test_sample_binary_returns_internal(tmp_path: Path) -> None:
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01")

    result = await dispatch(SampleTool(), 
        SampleInput(path=binary),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "utf-8" in result.error.detail.lower()


async def test_sample_cancelled_returns_cancelled(tmp_path: Path) -> None:
    path = _make(tmp_path, "any.txt", "x")

    result = await dispatch(SampleTool(), 
        SampleInput(path=path),
        CallContext(cancel=_AlwaysCancelled()),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
