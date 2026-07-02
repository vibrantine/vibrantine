"""Tests for the ListDir tool."""

import sys
from pathlib import Path

import pytest

from vibrantine.contract import CallContext
from vibrantine.tools.list_dir import ListDirInput, ListDirTool


class _AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


async def test_list_dir_returns_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    (tmp_path / "subdir").mkdir()

    result = await ListDirTool().invoke(ListDirInput(path=tmp_path), CallContext())

    assert result.status == "success"
    assert result.output is not None
    assert result.output.path == tmp_path
    names_kinds = [(e.name, e.kind) for e in result.output.entries]
    assert ("a.txt", "file") in names_kinds
    assert ("b.txt", "file") in names_kinds
    assert ("subdir", "directory") in names_kinds


async def test_list_dir_sorted_by_name(tmp_path: Path) -> None:
    for name in ["zeta", "alpha", "mu"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    result = await ListDirTool().invoke(ListDirInput(path=tmp_path), CallContext())

    assert result.status == "success"
    assert result.output is not None
    names = [e.name for e in result.output.entries]
    assert names == sorted(names)


async def test_list_dir_empty_directory(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    result = await ListDirTool().invoke(ListDirInput(path=empty), CallContext())

    assert result.status == "success"
    assert result.output is not None
    assert result.output.entries == []
    assert result.output.truncated is False
    assert result.output.total_entries == 0


async def test_list_dir_bounds_entries_and_signals_truncation(tmp_path: Path) -> None:
    for name in ["a", "b", "c"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    result = await ListDirTool().invoke(
        ListDirInput(path=tmp_path, max_entries=2),
        CallContext(),
    )

    assert result.status == "success"
    assert result.output is not None
    assert [e.name for e in result.output.entries] == ["a", "b"]  # sorted, capped
    assert result.output.truncated is True
    assert result.output.total_entries == 3


@pytest.mark.skipif(sys.platform == "win32", reason="Symlink creation needs admin on Windows")
async def test_list_dir_classifies_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("hi", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    result = await ListDirTool().invoke(ListDirInput(path=tmp_path), CallContext())

    assert result.status == "success"
    assert result.output is not None
    kinds = {e.name: e.kind for e in result.output.entries}
    assert kinds["real.txt"] == "file"
    assert kinds["link.txt"] == "symlink"


async def test_list_dir_relative_path_returns_validation(tmp_path: Path) -> None:
    result = await ListDirTool().invoke(
        ListDirInput(path=Path("relative")),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_list_dir_nonexistent_path_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"

    result = await ListDirTool().invoke(ListDirInput(path=missing), CallContext())

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "exist" in result.error.detail.lower()


async def test_list_dir_file_path_returns_validation(tmp_path: Path) -> None:
    f = tmp_path / "actually-a-file.txt"
    f.write_text("", encoding="utf-8")

    result = await ListDirTool().invoke(ListDirInput(path=f), CallContext())

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "director" in result.error.detail.lower()


async def test_list_dir_cancelled_returns_cancelled(tmp_path: Path) -> None:
    result = await ListDirTool().invoke(
        ListDirInput(path=tmp_path),
        CallContext(cancel=_AlwaysCancelled()),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
