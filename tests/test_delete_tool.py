"""Tests for the Delete tool."""

from pathlib import Path

from vibrantine import run_commission
from vibrantine.tools.delete import DeleteInput, DeleteTool


class _AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


async def test_delete_removes_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "to-delete.txt"
    target.write_text("doomed", encoding="utf-8")

    result = await run_commission(DeleteTool(), DeleteInput(path=target))

    assert result.status == "success"
    assert result.error is None
    assert result.output is not None
    assert result.output.path == target
    assert not target.exists()


async def test_delete_nonexistent_returns_validation(tmp_path: Path) -> None:
    missing = tmp_path / "no-such.txt"

    result = await run_commission(DeleteTool(), DeleteInput(path=missing))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "does not exist" in result.error.detail


async def test_delete_directory_returns_validation(tmp_path: Path) -> None:
    d = tmp_path / "actually-a-dir"
    d.mkdir()

    result = await run_commission(DeleteTool(), DeleteInput(path=d))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "director" in result.error.detail.lower()
    # Dir still there.
    assert d.exists()


async def test_delete_relative_path_returns_validation(tmp_path: Path) -> None:
    result = await run_commission(
        DeleteTool(),
        DeleteInput(path=Path("relative.txt")),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"


async def test_delete_cancelled_returns_cancelled(tmp_path: Path) -> None:
    target = tmp_path / "still-here.txt"
    target.write_text("preserved", encoding="utf-8")

    result = await run_commission(
        DeleteTool(),
        DeleteInput(path=target),
        cancel=_AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    # File still there.
    assert target.exists()
