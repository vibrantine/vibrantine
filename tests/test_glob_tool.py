"""Tests for the Glob tool."""

from pathlib import Path

from vibrantine import run_commission
from vibrantine.testing import AlwaysCancelled
from vibrantine.tools.glob import GlobInput, GlobTool


def _populate(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.md").write_text("", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.py").write_text("", encoding="utf-8")
    (sub / "nested.md").write_text("", encoding="utf-8")


async def test_glob_simple_pattern_matches_top_level(tmp_path: Path) -> None:
    _populate(tmp_path)

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*.py", base=tmp_path),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    names = sorted(p.name for p in result.output.matches)
    assert names == ["a.py", "b.py"]


async def test_glob_recursive_pattern_matches_nested(tmp_path: Path) -> None:
    _populate(tmp_path)

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="**/*.py", base=tmp_path),
    )

    assert result.status == "success"
    assert result.output is not None
    names = sorted(p.name for p in result.output.matches)
    assert names == ["a.py", "b.py", "nested.py"]


async def test_glob_bounds_matches_and_signals_truncation(tmp_path: Path) -> None:
    _populate(tmp_path)  # two top-level .py files

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*.py", base=tmp_path, max_matches=1),
    )

    assert result.status == "success"
    assert result.output is not None
    assert [p.name for p in result.output.matches] == ["a.py"]  # sorted, capped
    assert result.output.truncated is True
    assert result.output.total_matches == 2


async def test_glob_under_cap_is_not_truncated(tmp_path: Path) -> None:
    _populate(tmp_path)

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*.py", base=tmp_path),
    )

    assert result.output is not None
    assert result.output.truncated is False
    assert result.output.total_matches == 2


async def test_glob_returns_files_only_not_directories(tmp_path: Path) -> None:
    _populate(tmp_path)
    # Pattern that would match the `sub` directory too.
    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*", base=tmp_path),
    )

    assert result.status == "success"
    assert result.output is not None
    # `sub` is a directory; it must be filtered out.
    names = {p.name for p in result.output.matches}
    assert "sub" not in names
    assert {"a.py", "b.py", "c.md"} <= names


async def test_glob_empty_match_returns_empty_list(tmp_path: Path) -> None:
    _populate(tmp_path)

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*.rs", base=tmp_path),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.matches == []


async def test_glob_relative_base_returns_validation(tmp_path: Path) -> None:
    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*.py", base=Path("relative")),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_glob_nonexistent_base_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*.py", base=missing),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_glob_file_as_base_returns_validation(tmp_path: Path) -> None:
    f = tmp_path / "not-a-dir.txt"
    f.write_text("", encoding="utf-8")

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*", base=f),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_glob_matches_sorted(tmp_path: Path) -> None:
    for name in ["zebra.py", "alpha.py", "mango.py"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="*.py", base=tmp_path),
    )

    assert result.status == "success"
    assert result.output is not None
    paths = result.output.matches
    assert paths == sorted(paths)


async def test_glob_cancelled_returns_cancelled(tmp_path: Path) -> None:
    _populate(tmp_path)

    result = await run_commission(
        GlobTool(),
        GlobInput(pattern="**/*.py", base=tmp_path),
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
