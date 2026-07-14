"""Tests for the Grep tool."""

import re
from pathlib import Path

from vibrantine import run_commission
from vibrantine.testing import AlwaysCancelled
from vibrantine.tools.grep import (
    GrepInput,
    GrepTool,
    _grep_file,  # pyright: ignore[reportPrivateUsage]
)


def _make(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


async def test_grep_single_file_returns_matching_lines(tmp_path: Path) -> None:
    path = _make(tmp_path, "doc.txt", "alpha\nbeta\nALPHA\ngamma\n")

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"alpha", path=path),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.truncated is False
    assert len(result.output.matches) == 1
    assert result.output.matches[0].path == path
    assert result.output.matches[0].line_number == 1
    assert result.output.matches[0].line == "alpha"


async def test_grep_directory_walk_finds_matches_recursively(tmp_path: Path) -> None:
    _make(tmp_path, "top.txt", "hit here\nno\n")
    _make(tmp_path, "sub/nested.txt", "no\nhit again\n")
    _make(tmp_path, "sub/other.txt", "nothing here\n")

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"hit", path=tmp_path),
    )

    assert result.status == "success"
    assert result.output is not None
    assert len(result.output.matches) == 2
    lines = sorted(m.line for m in result.output.matches)
    assert lines == ["hit again", "hit here"]


async def test_grep_max_matches_truncates_with_flag(tmp_path: Path) -> None:
    content = "".join(f"hit-{i}\n" for i in range(20))
    path = _make(tmp_path, "many.txt", content)

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"hit", path=path, max_matches=5),
    )

    assert result.status == "success"
    assert result.output is not None
    assert len(result.output.matches) == 5
    assert result.output.truncated is True


async def test_grep_ignore_case(tmp_path: Path) -> None:
    path = _make(tmp_path, "case.txt", "FOO\nfoo\nFoo\nbar\n")

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"foo", path=path, ignore_case=True),
    )

    assert result.status == "success"
    assert result.output is not None
    assert len(result.output.matches) == 3


async def test_grep_invalid_regex_returns_validation(tmp_path: Path) -> None:
    path = _make(tmp_path, "any.txt", "content\n")

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"unclosed[group", path=path),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "invalid regex" in result.error.detail.lower()


async def test_grep_relative_path_returns_validation(tmp_path: Path) -> None:
    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"x", path=Path("relative")),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_grep_nonexistent_path_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-place"

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"x", path=missing),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_grep_binary_file_in_directory_walk_is_skipped(tmp_path: Path) -> None:
    _make(tmp_path, "text.txt", "match me\nnope\n")
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe\x00\x01\xff")

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"match", path=tmp_path),
    )

    # The binary file should be skipped silently; the text match still lands.
    assert result.status == "success"
    assert result.output is not None
    assert len(result.output.matches) == 1
    assert result.output.matches[0].line == "match me"


def test_grep_file_vanished_mid_walk_is_skipped(tmp_path: Path) -> None:
    # A file can vanish between the walk listing it and the read (a race);
    # the walk must skip it like any other unreadable entry, not abort the
    # whole grep. The race can't be reproduced deterministically through
    # the public surface, so the read-and-classify helper is tested directly.
    ghost = tmp_path / "vanished.txt"
    matches, err = _grep_file(ghost, re.compile("x"), surface_read_errors=False)
    assert matches == []
    assert err is None


def test_grep_file_missing_as_direct_path_surfaces_validation(tmp_path: Path) -> None:
    ghost = tmp_path / "vanished.txt"
    matches, err = _grep_file(ghost, re.compile("x"), surface_read_errors=True)
    assert matches == []
    assert err is not None
    assert err[0] == "validation"


async def test_grep_binary_file_as_direct_path_returns_internal(tmp_path: Path) -> None:
    binary = tmp_path / "direct-binary.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01\xff")

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"anything", path=binary),
    )

    # Direct hit on a binary file is an error, not a silent skip.
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "utf-8" in result.error.detail.lower()


async def test_grep_empty_match_returns_empty_list(tmp_path: Path) -> None:
    path = _make(tmp_path, "doc.txt", "alpha\nbeta\n")

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"missing", path=path),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.matches == []
    assert result.output.truncated is False


async def test_grep_cancelled_returns_cancelled(tmp_path: Path) -> None:
    _make(tmp_path, "any.txt", "stuff\n")

    result = await run_commission(
        GrepTool(),
        GrepInput(pattern=r"x", path=tmp_path),
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
