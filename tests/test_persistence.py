"""Tests for FilesystemBackend.

Backend is tested in isolation against the PersistenceBackend Protocol;
no dispatch helper or Commission machinery involved. Each test builds
its records by hand, drives the backend directly, and asserts on the
on-disk state.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vibrantine.contract import PersistedRecord
from vibrantine.persistence import FilesystemBackend


def _record(
    *,
    run_id: str = "r-1",
    parent_run_id: str | None = None,
    mode: str = "dev",
    created_at: datetime | None = None,
    commission_name: str = "demo",
) -> PersistedRecord:
    return PersistedRecord(
        run_id=run_id,
        parent_run_id=parent_run_id,
        commission_name=commission_name,
        mode=mode,  # type: ignore[arg-type]
        created_at=created_at or datetime.now(UTC),
        input={"q": "?"},
        result={"status": "success"},
        ctx_snapshot={"budget_usd": None},
        llm_trace=None,
    )


async def test_store_then_load_round_trip(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    record = _record(mode="always")

    await backend.store(record)
    loaded = await backend.load(record.run_id)

    assert loaded is not None
    assert loaded.run_id == record.run_id
    assert loaded.commission_name == record.commission_name
    assert loaded.mode == "always"


async def test_load_missing_returns_none(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    assert await backend.load("nope") is None


async def test_list_references_filters_by_parent(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    await backend.store(_record(run_id="root", mode="always"))
    await backend.store(_record(run_id="child-a", parent_run_id="root", mode="always"))
    await backend.store(_record(run_id="child-b", parent_run_id="root", mode="always"))
    await backend.store(_record(run_id="grandchild", parent_run_id="child-a", mode="always"))

    roots = await backend.list_references()
    children_of_root = await backend.list_references(parent_run_id="root")
    children_of_a = await backend.list_references(parent_run_id="child-a")

    assert roots == ["root"]
    assert set(children_of_root) == {"child-a", "child-b"}
    assert children_of_a == ["grandchild"]


async def test_delete_removes_file(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    await backend.store(_record(mode="always"))

    await backend.delete("r-1")

    assert await backend.load("r-1") is None


async def test_store_rejects_run_id_that_escapes_root(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path / "records")

    with pytest.raises(ValueError, match="run_id"):
        await backend.store(_record(run_id="../outside", mode="always"))

    assert not (tmp_path / "outside.json").exists()


async def test_load_rejects_run_id_that_escapes_root(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path / "records")
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="run_id"):
        await backend.load("../outside")


async def test_delete_rejects_run_id_that_escapes_root(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path / "records")
    outside = tmp_path / "outside.json"
    outside.write_text("keep me", encoding="utf-8")

    with pytest.raises(ValueError, match="run_id"):
        await backend.delete("../outside")

    assert outside.read_text(encoding="utf-8") == "keep me"


async def test_delete_older_than_evicts_only_older(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    old = datetime.now(UTC) - timedelta(days=10)
    young = datetime.now(UTC) - timedelta(days=1)
    await backend.store(_record(run_id="old", mode="always", created_at=old))
    await backend.store(_record(run_id="young", mode="always", created_at=young))

    cutoff = datetime.now(UTC) - timedelta(days=5)
    evicted = await backend.delete_older_than(cutoff)

    assert evicted == 1
    assert await backend.load("old") is None
    assert await backend.load("young") is not None


async def test_dev_mode_evicts_oldest_when_over_ring_buffer(
    tmp_path: Path,
) -> None:
    # Override the ring buffer size so the test isn't 100 records.
    backend = FilesystemBackend(tmp_path, dev_ring_buffer_size=3)

    # Store 5 dev records with distinct timestamps so the oldest two get evicted.
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        await backend.store(
            _record(
                run_id=f"d-{i}",
                mode="dev",
                created_at=base + timedelta(minutes=i),
            )
        )

    # Newest 3 (d-2, d-3, d-4) should survive; d-0 and d-1 evicted.
    survivors = await backend.list_references()
    assert set(survivors) == {"d-2", "d-3", "d-4"}


async def test_on_failure_mode_evicts_records_past_retention(
    tmp_path: Path,
) -> None:
    backend = FilesystemBackend(tmp_path, on_failure_retention_days=7)

    old_failure = datetime.now(UTC) - timedelta(days=10)
    young_failure = datetime.now(UTC) - timedelta(days=1)
    await backend.store(_record(run_id="old", mode="on_failure", created_at=old_failure))
    await backend.store(_record(run_id="young", mode="on_failure", created_at=young_failure))

    # Storing a third on_failure record triggers pruning of the old one.
    await backend.store(_record(run_id="trigger", mode="on_failure"))

    survivors = set(await backend.list_references())
    assert "old" not in survivors
    assert {"young", "trigger"}.issubset(survivors)


async def test_always_mode_never_evicts(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path, dev_ring_buffer_size=1)

    very_old = datetime(2020, 1, 1, tzinfo=UTC)
    await backend.store(_record(run_id="ancient", mode="always", created_at=very_old))
    # Several more always records to confirm no eviction happens.
    for i in range(3):
        await backend.store(_record(run_id=f"a-{i}", mode="always"))

    survivors = set(await backend.list_references())
    assert "ancient" in survivors
    assert {"a-0", "a-1", "a-2"}.issubset(survivors)


async def test_dev_pruning_does_not_touch_other_modes(tmp_path: Path) -> None:
    """Cross-mode interference check: storing a dev record shouldn't evict
    on_failure / always records."""
    backend = FilesystemBackend(tmp_path, dev_ring_buffer_size=1)

    # on_failure record must be within its own retention window or it would
    # be pruned by its own store. always records never self-prune.
    young = datetime.now(UTC) - timedelta(days=1)
    very_old = datetime(2020, 1, 1, tzinfo=UTC)
    await backend.store(_record(run_id="kept-failure", mode="on_failure", created_at=young))
    await backend.store(_record(run_id="kept-always", mode="always", created_at=very_old))

    # Storing 3 dev records with ring_buffer_size=1 evicts 2 dev records,
    # but should leave the other modes untouched.
    for i in range(3):
        await backend.store(_record(run_id=f"d-{i}", mode="dev"))

    survivors = set(await backend.list_references())
    assert "kept-failure" in survivors
    assert "kept-always" in survivors
