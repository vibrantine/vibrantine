"""Tests for the shipped persistence backends.

Backends are tested in isolation against the PersistenceBackend Protocol;
no dispatch helper or Commission machinery involved. The protocol and
pruning tests run against BOTH shipped backends through the `make_backend`
fixture, so behavioral parity is asserted rather than assumed.
Backend-specific behavior (filesystem path safety, SQL queryability) gets
its own tests at the bottom.
"""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from vibrantine.contract import PersistedRecord, PersistenceBackend
from vibrantine.persistence import FilesystemBackend, SqliteBackend

type BackendFactory = Callable[..., PersistenceBackend]


def _record(
    *,
    run_id: str = "r-1",
    parent_run_id: str | None = None,
    mode: str = "dev",
    created_at: datetime | None = None,
    commission_name: str = "demo",
    status: str = "success",
    cost_usd: float = 0.0,
) -> PersistedRecord:
    return PersistedRecord(
        run_id=run_id,
        parent_run_id=parent_run_id,
        commission_name=commission_name,
        mode=mode,  # type: ignore[arg-type]
        created_at=created_at or datetime.now(UTC),
        input={"q": "?"},
        result={"status": status, "cost": {"estimated_usd": cost_usd}},
        ctx_snapshot={"budget_usd": None},
        llm_trace=None,
    )


def _call_row(run_id: str, *, ended_at: datetime | None = None) -> dict[str, Any]:
    ended = ended_at or datetime.now(UTC)
    started = ended - timedelta(seconds=1)
    return {
        "run_id": run_id,
        "commission_name": "demo",
        "model": "fixture/model",
        "model_name": "fixture/model",
        "started_at": started.isoformat(timespec="microseconds"),
        "ended_at": ended.isoformat(timespec="microseconds"),
        "in_tokens": 10,
        "out_tokens": 5,
        "cost_usd": 0.001,
        "grant_usd": 1.0,
        "run_calls_before": 0,
        "run_spend_before_usd": 0.0,
        "status": "completed",
    }


@pytest.fixture(params=["filesystem", "sqlite"])
def make_backend(request: pytest.FixtureRequest, tmp_path: Path) -> BackendFactory:
    """Build a backend of the parametrized flavor, rooted in tmp_path."""

    def _make(**overrides: int) -> PersistenceBackend:
        if request.param == "filesystem":
            return FilesystemBackend(tmp_path, **overrides)
        return SqliteBackend(tmp_path / "runs.db", **overrides)

    return _make


# --- protocol behavior, both backends --------------------------------------


async def test_store_then_load_round_trip(make_backend: BackendFactory) -> None:
    backend = make_backend()
    record = _record(mode="always")

    await backend.store(record)
    loaded = await backend.load(record.run_id)

    assert loaded is not None
    assert loaded.run_id == record.run_id
    assert loaded.commission_name == record.commission_name
    assert loaded.mode == "always"


async def test_load_missing_returns_none(make_backend: BackendFactory) -> None:
    backend = make_backend()
    assert await backend.load("nope") is None


async def test_list_references_filters_by_parent(make_backend: BackendFactory) -> None:
    backend = make_backend()
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


async def test_delete_removes_record(make_backend: BackendFactory) -> None:
    backend = make_backend()
    await backend.store(_record(mode="always"))

    await backend.delete("r-1")

    assert await backend.load("r-1") is None


async def test_delete_older_than_evicts_only_older(make_backend: BackendFactory) -> None:
    backend = make_backend()
    old = datetime.now(UTC) - timedelta(days=10)
    young = datetime.now(UTC) - timedelta(days=1)
    await backend.store(_record(run_id="old", mode="always", created_at=old))
    await backend.store(_record(run_id="young", mode="always", created_at=young))

    cutoff = datetime.now(UTC) - timedelta(days=5)
    evicted = await backend.delete_older_than(cutoff)

    assert evicted == 1
    assert await backend.load("old") is None
    assert await backend.load("young") is not None


async def test_delete_older_than_rejects_naive_cutoff(make_backend: BackendFactory) -> None:
    # The Protocol rules out timezone guessing: a naive cutoff is refused
    # with ValueError before any record is touched, identically on both
    # backends (previously one raised TypeError mid-sweep and the other
    # silently read the cutoff as local time).
    backend = make_backend()
    await backend.store(_record(run_id="keep", mode="always", created_at=datetime.now(UTC)))

    naive = datetime.now() - timedelta(days=365)  # noqa: DTZ005 - naive on purpose
    with pytest.raises(ValueError, match="timezone-aware"):
        await backend.delete_older_than(naive)

    assert await backend.load("keep") is not None


async def test_dev_mode_evicts_oldest_when_over_ring_buffer(
    make_backend: BackendFactory,
) -> None:
    # Override the ring buffer size so the test isn't 100 records.
    backend = make_backend(dev_ring_buffer_size=3)

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
    make_backend: BackendFactory,
) -> None:
    backend = make_backend(on_failure_retention_days=7)

    old_failure = datetime.now(UTC) - timedelta(days=10)
    young_failure = datetime.now(UTC) - timedelta(days=1)
    await backend.store(_record(run_id="old", mode="on_failure", created_at=old_failure))
    await backend.store(_record(run_id="young", mode="on_failure", created_at=young_failure))

    # Storing a third on_failure record triggers pruning of the old one.
    await backend.store(_record(run_id="trigger", mode="on_failure"))

    survivors = set(await backend.list_references())
    assert "old" not in survivors
    assert {"young", "trigger"}.issubset(survivors)


async def test_always_mode_never_evicts(make_backend: BackendFactory) -> None:
    backend = make_backend(dev_ring_buffer_size=1)

    very_old = datetime(2020, 1, 1, tzinfo=UTC)
    await backend.store(_record(run_id="ancient", mode="always", created_at=very_old))
    # Several more always records to confirm no eviction happens.
    for i in range(3):
        await backend.store(_record(run_id=f"a-{i}", mode="always"))

    survivors = set(await backend.list_references())
    assert "ancient" in survivors
    assert {"a-0", "a-1", "a-2"}.issubset(survivors)


async def test_dev_pruning_does_not_touch_other_modes(make_backend: BackendFactory) -> None:
    """Cross-mode interference check: storing a dev record shouldn't evict
    on_failure / always records."""
    backend = make_backend(dev_ring_buffer_size=1)

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


# --- FilesystemBackend-specific: path safety --------------------------------


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


# --- SqliteBackend-specific: the file is the query surface ------------------


async def test_sqlite_columns_answer_sql_directly(tmp_path: Path) -> None:
    # The reason this backend exists: no query API in the library, because
    # the database file itself answers questions in plain SQL.
    db = tmp_path / "runs.db"
    backend = SqliteBackend(db)
    await backend.store(_record(run_id="cheap-ok", mode="always", status="success", cost_usd=0.001))
    await backend.store(
        _record(run_id="pricey-fail", mode="always", status="failure", cost_usd=0.09)
    )
    await backend.store(
        _record(run_id="cheap-fail", mode="always", status="failure", cost_usd=0.002)
    )

    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "SELECT run_id FROM records WHERE status = 'failure' AND cost_usd > 0.01"
        ).fetchall()

    assert [row[0] for row in rows] == ["pricey-fail"]


async def test_sqlite_records_survive_reopen(tmp_path: Path) -> None:
    db = tmp_path / "runs.db"
    await SqliteBackend(db).store(_record(run_id="kept", mode="always"))

    reopened = SqliteBackend(db)
    loaded = await reopened.load("kept")

    assert loaded is not None
    assert loaded.run_id == "kept"
    assert loaded.result["status"] == "success"


async def test_sqlite_call_rows_die_with_the_root_record_only(tmp_path: Path) -> None:
    # A call row belongs to the run, not the node that made it (ruled
    # 2026-07-12): deleting a child record leaves the root's call log
    # whole; deleting the root record removes the run's entire log.
    db = tmp_path / "runs.db"
    backend = SqliteBackend(db)
    await backend.store(_record(run_id="root", mode="always"))
    await backend.store(_record(run_id="child", parent_run_id="root", mode="always"))
    await backend.store_calls("root", [_call_row("root"), _call_row("child")])

    await backend.delete("child")
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT root_run_id, run_id FROM calls ORDER BY run_id").fetchall()
    assert rows == [("root", "child"), ("root", "root")]

    await backend.delete("root")
    with closing(sqlite3.connect(db)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM calls").fetchone()
    assert count == (0,)


async def test_sqlite_ring_buffer_pruning_a_child_keeps_the_roots_log_whole(
    tmp_path: Path,
) -> None:
    # The dev ring buffer evicts per record with no tree awareness; child
    # records age out before their root's. The run's audit log must not be
    # hole-punched by that rotation: call rows ride the root record alone.
    db = tmp_path / "runs.db"
    backend = SqliteBackend(db, dev_ring_buffer_size=2)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await backend.store(_record(run_id="child", parent_run_id="root", mode="dev", created_at=base))
    await backend.store(_record(run_id="root", mode="dev", created_at=base + timedelta(minutes=1)))
    await backend.store_calls("root", [_call_row("root"), _call_row("child")])

    # A later run's record fills the buffer and evicts the oldest ("child").
    await backend.store(_record(run_id="other", mode="dev", created_at=base + timedelta(minutes=2)))

    with closing(sqlite3.connect(db)) as conn:
        records = {row[0] for row in conn.execute("SELECT run_id FROM records").fetchall()}
        calls = conn.execute("SELECT COUNT(*) FROM calls").fetchone()
    assert records == {"root", "other"}
    assert calls == (2,)


async def test_sqlite_age_pruning_keeps_a_retained_records_whole_log(tmp_path: Path) -> None:
    # A run that straddles the cutoff (early calls ended before it, record
    # stored after it) keeps its record AND its whole call log: the orphan
    # sweep only reaps rows whose run never stored a root record.
    db = tmp_path / "runs.db"
    backend = SqliteBackend(db)
    cutoff = datetime.now(UTC) - timedelta(days=5)
    await backend.store(
        _record(run_id="straddler", mode="always", created_at=cutoff + timedelta(hours=1))
    )
    await backend.store_calls(
        "straddler", [_call_row("straddler", ended_at=cutoff - timedelta(hours=1))]
    )

    await backend.delete_older_than(cutoff)

    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT root_run_id FROM calls").fetchall()
    assert rows == [("straddler",)]


async def test_sqlite_age_pruning_removes_call_rows(tmp_path: Path) -> None:
    db = tmp_path / "runs.db"
    backend = SqliteBackend(db)
    old = datetime.now(UTC) - timedelta(days=10)
    young = datetime.now(UTC) - timedelta(days=1)
    await backend.store(_record(run_id="old", mode="always", created_at=old))
    await backend.store(_record(run_id="young", mode="always", created_at=young))
    await backend.store_calls("old", [_call_row("old", ended_at=old)])
    await backend.store_calls("young", [_call_row("young", ended_at=young)])
    await backend.store_calls("call-only", [_call_row("call-only", ended_at=old)])

    await backend.delete_older_than(datetime.now(UTC) - timedelta(days=5))

    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT root_run_id FROM calls").fetchall()
    assert rows == [("young",)]


async def test_sqlite_ring_buffer_pruning_removes_call_rows(tmp_path: Path) -> None:
    db = tmp_path / "runs.db"
    backend = SqliteBackend(db, dev_ring_buffer_size=1)
    old = datetime(2026, 1, 1, tzinfo=UTC)
    await backend.store(_record(run_id="old", mode="dev", created_at=old))
    await backend.store_calls("old", [_call_row("old")])

    await backend.store(_record(run_id="new", mode="dev", created_at=old + timedelta(minutes=1)))

    with closing(sqlite3.connect(db)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM calls").fetchone()
    assert count == (0,)
