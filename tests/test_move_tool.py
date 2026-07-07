"""Tests for the Move tool."""

from pathlib import Path

from vibrantine.contract import CallContext
from vibrantine.dispatch import dispatch
from vibrantine.tools.move import MoveInput, MoveTool


class _AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


def _make(p: Path, content: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


async def test_move_renames_within_directory(tmp_path: Path) -> None:
    src = _make(tmp_path / "old.txt", "hello")
    dst = tmp_path / "new.txt"

    result = await dispatch(
        MoveTool(),
        MoveInput(source=src, target=dst),
        CallContext(),
    )

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.source == src
    assert result.output.target == dst
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "hello"


async def test_move_across_directories(tmp_path: Path) -> None:
    src = _make(tmp_path / "a" / "f.txt", "content")
    (tmp_path / "b").mkdir()
    dst = tmp_path / "b" / "f.txt"

    result = await dispatch(
        MoveTool(),
        MoveInput(source=src, target=dst),
        CallContext(),
    )

    assert result.status == "success"
    assert dst.read_text(encoding="utf-8") == "content"
    assert not src.exists()


async def test_move_existing_target_without_overwrite_fails(tmp_path: Path) -> None:
    src = _make(tmp_path / "src.txt", "new")
    dst = _make(tmp_path / "dst.txt", "old")

    result = await dispatch(
        MoveTool(),
        MoveInput(source=src, target=dst),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "already exists" in result.error.detail
    # Both files untouched.
    assert src.read_text(encoding="utf-8") == "new"
    assert dst.read_text(encoding="utf-8") == "old"


async def test_move_existing_target_with_overwrite_succeeds(tmp_path: Path) -> None:
    src = _make(tmp_path / "src.txt", "new")
    dst = _make(tmp_path / "dst.txt", "old")

    result = await dispatch(
        MoveTool(),
        MoveInput(source=src, target=dst, overwrite=True),
        CallContext(),
    )

    assert result.status == "success"
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "new"


async def test_move_source_does_not_exist_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-file.txt"

    result = await dispatch(
        MoveTool(),
        MoveInput(source=missing, target=tmp_path / "target.txt"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "does not exist" in result.error.detail


async def test_move_source_is_directory_returns_validation(tmp_path: Path) -> None:
    src_dir = tmp_path / "dir-not-file"
    src_dir.mkdir()

    result = await dispatch(
        MoveTool(),
        MoveInput(source=src_dir, target=tmp_path / "target.txt"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "not a regular file" in result.error.detail


async def test_move_target_parent_missing_returns_validation(tmp_path: Path) -> None:
    src = _make(tmp_path / "src.txt")
    dst = tmp_path / "missing-parent" / "target.txt"

    result = await dispatch(
        MoveTool(),
        MoveInput(source=src, target=dst),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "parent directory" in result.error.detail
    # Source untouched.
    assert src.exists()


async def test_move_relative_source_returns_validation(tmp_path: Path) -> None:
    result = await dispatch(
        MoveTool(),
        MoveInput(source=Path("relative.txt"), target=tmp_path / "t.txt"),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_move_relative_target_returns_validation(tmp_path: Path) -> None:
    src = _make(tmp_path / "src.txt")

    result = await dispatch(
        MoveTool(),
        MoveInput(source=src, target=Path("relative.txt")),
        CallContext(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_move_cancelled_returns_cancelled(tmp_path: Path) -> None:
    src = _make(tmp_path / "src.txt", "intact")

    result = await dispatch(
        MoveTool(),
        MoveInput(source=src, target=tmp_path / "dst.txt"),
        CallContext(cancel=_AlwaysCancelled()),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    # Untouched.
    assert src.read_text(encoding="utf-8") == "intact"
