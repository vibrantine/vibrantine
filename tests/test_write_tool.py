"""Tests for the Write tool."""

from collections.abc import Callable
from pathlib import Path
from typing import IO, Any, cast

import pytest

from vibrantine import run_commission
from vibrantine.testing import AlwaysCancelled
from vibrantine.tools.write import WriteInput, WriteTool


async def test_write_creates_new_file(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"

    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content="hello world\n"),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.path == path
    assert result.output.bytes_written == 12
    assert path.read_text(encoding="utf-8") == "hello world\n"
    assert result.cost.estimated_usd == 0.0


async def test_write_overwrites_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "exists.txt"
    path.write_text("old content\n", encoding="utf-8")

    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content="new content\n"),
    )

    assert result.status == "success"
    assert path.read_text(encoding="utf-8") == "new content\n"


async def test_write_create_only_fails_when_target_exists(tmp_path: Path) -> None:
    path = tmp_path / "exists.txt"
    path.write_text("don't clobber me\n", encoding="utf-8")

    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content="new", create_only=True),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "create_only" in result.error.detail
    assert path.read_text(encoding="utf-8") == "don't clobber me\n"


async def test_write_create_only_succeeds_when_target_missing(tmp_path: Path) -> None:
    path = tmp_path / "fresh.txt"

    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content="brand new", create_only=True),
    )

    assert result.status == "success"
    assert path.read_text(encoding="utf-8") == "brand new"


async def test_write_create_only_loses_atomic_race_without_overwriting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "raced.txt"
    real_open = cast(Callable[[Path, str], IO[Any]], Path.open)

    def racing_open(self: Path, mode: str = "r") -> IO[Any]:
        if self == path and mode == "xb":
            with real_open(self, "wb") as competitor:
                competitor.write(b"competitor")
        return real_open(self, mode)

    monkeypatch.setattr(Path, "open", racing_open)
    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content="ours", create_only=True),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert path.read_bytes() == b"competitor"


async def test_write_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "file.txt"

    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content="deep"),
    )

    assert result.status == "success"
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "deep"


async def test_write_unicode_bytes_count_matches_utf8_encoding(tmp_path: Path) -> None:
    path = tmp_path / "unicode.txt"
    content = "héllo 🌍\n"

    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content=content),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.bytes_written == len(content.encode("utf-8"))


async def test_write_relative_path_returns_validation(tmp_path: Path) -> None:
    result = await run_commission(
        WriteTool(),
        WriteInput(path=Path("relative.txt"), content="x"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_write_directory_path_returns_validation(tmp_path: Path) -> None:
    result = await run_commission(
        WriteTool(),
        WriteInput(path=tmp_path, content="x"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "director" in result.error.detail.lower()


async def test_write_cancelled_before_write_returns_cancelled(tmp_path: Path) -> None:
    path = tmp_path / "never.txt"

    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content="should not land"),
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert not path.exists()


async def test_write_provenance_matches_target_path(tmp_path: Path) -> None:
    path = tmp_path / "prov.txt"

    result = await run_commission(
        WriteTool(),
        WriteInput(path=path, content="x"),
    )

    assert result.provenance.source == str(path)
    assert result.provenance.confidence == "grounded"
