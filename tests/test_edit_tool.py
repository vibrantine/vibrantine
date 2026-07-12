"""Tests for the Edit tool."""

from pathlib import Path

from vibrantine import run_commission
from vibrantine.tools.edit import EditInput, EditTool


class _AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


def _make_file(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_bytes(content.encode("utf-8"))
    return p


async def test_edit_single_replacement_succeeds(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "doc.txt", "hello world")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="world", new_string="earth"),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.path == path
    assert result.output.replacements_made == 1
    assert path.read_text(encoding="utf-8") == "hello earth"
    assert result.cost.estimated_usd == 0.0


async def test_edit_replace_all_replaces_every_occurrence(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "doc.txt", "foo bar foo baz foo")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="foo", new_string="XX", replace_all=True),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.replacements_made == 3
    assert path.read_text(encoding="utf-8") == "XX bar XX baz XX"


async def test_edit_multiple_matches_without_replace_all_fails(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "doc.txt", "foo bar foo baz")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="foo", new_string="X"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "matched 2 times" in result.error.detail
    # File untouched.
    assert path.read_text(encoding="utf-8") == "foo bar foo baz"


async def test_edit_no_matches_returns_validation(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "doc.txt", "hello world")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="missing", new_string="x"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "did not match" in result.error.detail
    assert path.read_text(encoding="utf-8") == "hello world"


async def test_edit_empty_old_string_returns_validation(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "doc.txt", "anything")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="", new_string="x"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "non-empty" in result.error.detail


async def test_edit_same_old_and_new_succeeds_unchanged(tmp_path: Path) -> None:
    # Idempotent edit: same string in and out. Should still report success.
    path = _make_file(tmp_path, "doc.txt", "stable")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="stable", new_string="stable"),
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.replacements_made == 1
    assert path.read_text(encoding="utf-8") == "stable"


async def test_edit_preserves_exact_bytes_around_match(tmp_path: Path) -> None:
    # Whitespace and line endings around the match must survive.
    original = "line1\n  spaced  \nline3\n"
    path = _make_file(tmp_path, "doc.txt", original)

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="spaced", new_string="changed"),
    )

    assert result.status == "success"
    # The surrounding whitespace/newlines are byte-identical.
    assert path.read_bytes() == b"line1\n  changed  \nline3\n"


async def test_edit_preserves_crlf_line_endings(tmp_path: Path) -> None:
    # Editing one word in a CRLF file must not rewrite the file's endings.
    path = tmp_path / "doc.txt"
    path.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="beta", new_string="BETA"),
    )

    assert result.status == "success"
    assert path.read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


async def test_edit_old_string_containing_crlf_matches(tmp_path: Path) -> None:
    # A multi-line old_string with CRLF endings must match a CRLF file.
    path = tmp_path / "doc.txt"
    path.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="beta\r\ngamma", new_string="merged"),
    )

    assert result.status == "success"
    assert path.read_bytes() == b"alpha\r\nmerged\r\n"


async def test_edit_relative_path_returns_validation(tmp_path: Path) -> None:
    result = await run_commission(
        EditTool(),
        EditInput(path=Path("relative.txt"), old_string="x", new_string="y"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_edit_nonexistent_file_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-file.txt"

    result = await run_commission(
        EditTool(),
        EditInput(path=missing, old_string="x", new_string="y"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "not found" in result.error.detail.lower()


async def test_edit_directory_returns_validation(tmp_path: Path) -> None:
    result = await run_commission(
        EditTool(),
        EditInput(path=tmp_path, old_string="x", new_string="y"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "director" in result.error.detail.lower()


async def test_edit_binary_file_returns_internal_for_encoding(tmp_path: Path) -> None:
    binary = tmp_path / "binary.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01\xff")

    result = await run_commission(
        EditTool(),
        EditInput(path=binary, old_string="anything", new_string="x"),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "utf-8" in result.error.detail.lower()


async def test_edit_cancelled_before_edit_returns_cancelled(tmp_path: Path) -> None:
    path = _make_file(tmp_path, "doc.txt", "hello world")

    result = await run_commission(
        EditTool(),
        EditInput(path=path, old_string="world", new_string="earth"),
        cancel=_AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    # File untouched.
    assert path.read_text(encoding="utf-8") == "hello world"
