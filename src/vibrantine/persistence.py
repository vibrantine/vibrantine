"""Persistence backends for Commission runs.

Two shipped implementations of the PersistenceBackend Protocol from
contract.py; external apps can swap in their own:

- `FilesystemBackend` (the default): one JSON file per run under a root
  directory. Simple to inspect, nothing to query.
- `SqliteBackend`: one row per run in a single SQLite file, for when the
  records need querying rather than just keeping.

Both prune on every store, per the record's mode (dev → ring buffer,
on_failure → time TTL, always → never).
"""

import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

from vibrantine.contract import (
    PersistedRecord,
    PersistenceMode,
)

DEV_RING_BUFFER_SIZE: Final[int] = 100
ON_FAILURE_RETENTION_DAYS: Final[int] = 7


class FilesystemBackend:
    """JSON-on-disk persistence backend. One file per run_id."""

    def __init__(
        self,
        root: Path,
        *,
        dev_ring_buffer_size: int = DEV_RING_BUFFER_SIZE,
        on_failure_retention_days: int = ON_FAILURE_RETENTION_DAYS,
    ) -> None:
        self._root = root.resolve()
        self._dev_ring_buffer_size = dev_ring_buffer_size
        self._on_failure_retention_days = on_failure_retention_days
        self._root.mkdir(parents=True, exist_ok=True)

    async def store(self, record: PersistedRecord) -> None:
        await asyncio.to_thread(self._write_sync, record)
        await asyncio.to_thread(self._prune_for_mode_sync, record.mode)

    async def load(self, run_id: str) -> PersistedRecord | None:
        return await asyncio.to_thread(self._read_sync, run_id)

    async def list_references(self, *, parent_run_id: str | None = None) -> list[str]:
        return await asyncio.to_thread(self._list_sync, parent_run_id)

    async def delete(self, run_id: str) -> None:
        await asyncio.to_thread(self._delete_sync, run_id)

    async def delete_older_than(self, cutoff: datetime) -> int:
        return await asyncio.to_thread(self._delete_older_than_sync, cutoff)

    # --- sync helpers run in threadpool ---

    def _path_for(self, run_id: str) -> Path:
        path = (self._root / f"{run_id}.json").resolve()
        if path.parent != self._root:
            raise ValueError(f"run_id must name one record under the persistence root: {run_id!r}")
        return path

    def _write_sync(self, record: PersistedRecord) -> None:
        path = self._path_for(record.run_id)
        path.write_text(record.model_dump_json(), encoding="utf-8")

    def _read_sync(self, run_id: str) -> PersistedRecord | None:
        path = self._path_for(run_id)
        if not path.exists():
            return None
        return PersistedRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def _list_sync(self, parent_run_id: str | None) -> list[str]:
        out: list[str] = []
        for path in self._root.glob("*.json"):
            record = PersistedRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if record.parent_run_id == parent_run_id:
                out.append(record.run_id)
        return out

    def _delete_sync(self, run_id: str) -> None:
        path = self._path_for(run_id)
        if path.exists():
            path.unlink()

    def _delete_older_than_sync(self, cutoff: datetime) -> int:
        count = 0
        for path in self._root.glob("*.json"):
            record = PersistedRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if record.created_at < cutoff:
                path.unlink()
                count += 1
        return count

    # --- pruning -----------------------------------------------------------

    def _prune_for_mode_sync(self, mode: PersistenceMode) -> None:
        if mode == "dev":
            self._prune_dev_ring_buffer()
        elif mode == "on_failure":
            cutoff = datetime.now(UTC) - timedelta(days=self._on_failure_retention_days)
            self._prune_by_mode_and_cutoff("on_failure", cutoff)
        # off and always: no pruning here. "off" never reaches store; "always"
        # leaves retention to the application.

    def _prune_dev_ring_buffer(self) -> None:
        dev_records: list[tuple[datetime, Path]] = []
        for path in self._root.glob("*.json"):
            record = self._safe_load(path)
            if record is not None and record.mode == "dev":
                dev_records.append((record.created_at, path))
        if len(dev_records) <= self._dev_ring_buffer_size:
            return
        # Newest first; everything past the buffer size gets evicted.
        dev_records.sort(key=lambda x: x[0], reverse=True)
        for _created, path in dev_records[self._dev_ring_buffer_size :]:
            path.unlink(missing_ok=True)

    def _prune_by_mode_and_cutoff(self, mode: PersistenceMode, cutoff: datetime) -> None:
        for path in self._root.glob("*.json"):
            record = self._safe_load(path)
            if record is None:
                continue
            if record.mode == mode and record.created_at < cutoff:
                path.unlink(missing_ok=True)

    def _safe_load(self, path: Path) -> PersistedRecord | None:
        """Load a record, returning None on parse failure.

        Pruning skips corrupt records rather than crashing the whole run;
        the alternative (failing pruning) defeats the "don't fill the disk"
        guarantee. Listing and explicit load() still raise.
        """
        try:
            return PersistedRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None


def _timestamp(moment: datetime) -> str:
    """UTC ISO-8601 with fixed precision, so text comparison is time order."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


class SqliteBackend:
    """SQLite persistence backend: one row per run; the file is the query surface.

    The plain columns (run_id, parent_run_id, commission_name, mode, status,
    cost_usd, created_at) exist as query handles; the `record` column holds
    the full PersistedRecord JSON and is the source of truth. Ask questions
    with any SQL tool; the library deliberately ships no query API on top,
    because SQL already is one.
    """

    def __init__(
        self,
        path: Path,
        *,
        dev_ring_buffer_size: int = DEV_RING_BUFFER_SIZE,
        on_failure_retention_days: int = ON_FAILURE_RETENTION_DAYS,
    ) -> None:
        self._path = path
        self._dev_ring_buffer_size = dev_ring_buffer_size
        self._on_failure_retention_days = on_failure_retention_days
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS records (
                    run_id TEXT PRIMARY KEY,
                    parent_run_id TEXT,
                    commission_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cost_usd REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    record TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_records_parent ON records(parent_run_id)")

    async def store(self, record: PersistedRecord) -> None:
        await asyncio.to_thread(self._store_sync, record)

    async def load(self, run_id: str) -> PersistedRecord | None:
        return await asyncio.to_thread(self._load_sync, run_id)

    async def list_references(self, *, parent_run_id: str | None = None) -> list[str]:
        return await asyncio.to_thread(self._list_sync, parent_run_id)

    async def delete(self, run_id: str) -> None:
        await asyncio.to_thread(self._delete_sync, run_id)

    async def delete_older_than(self, cutoff: datetime) -> int:
        return await asyncio.to_thread(self._delete_older_than_sync, cutoff)

    # --- sync helpers run in threadpool ---

    def _connect(self) -> sqlite3.Connection:
        # A connection per operation: no shared connection to guard across
        # the threadpool, and SQLite's own file lock serializes writers.
        return sqlite3.connect(self._path)

    def _store_sync(self, record: PersistedRecord) -> None:
        cost = cast("dict[str, Any]", record.result.get("cost") or {})
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO records
                    (run_id, parent_run_id, commission_name, mode,
                     status, cost_usd, created_at, record)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.parent_run_id,
                    record.commission_name,
                    record.mode,
                    str(record.result.get("status", "unknown")),
                    float(cost.get("estimated_usd", 0.0)),
                    _timestamp(record.created_at),
                    record.model_dump_json(),
                ),
            )
            self._prune_for_mode_sync(conn, record.mode)

    def _load_sync(self, run_id: str) -> PersistedRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT record FROM records WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return PersistedRecord.model_validate_json(cast("str", row[0]))

    def _list_sync(self, parent_run_id: str | None) -> list[str]:
        # `IS ?` instead of `= ?` so a None parent matches NULL rows (roots).
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT run_id FROM records WHERE parent_run_id IS ? ORDER BY created_at",
                (parent_run_id,),
            ).fetchall()
        return [cast("str", row[0]) for row in rows]

    def _delete_sync(self, run_id: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM records WHERE run_id = ?", (run_id,))

    def _delete_older_than_sync(self, cutoff: datetime) -> int:
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                "DELETE FROM records WHERE created_at < ?",
                (_timestamp(cutoff),),
            )
            return cursor.rowcount

    def _prune_for_mode_sync(self, conn: sqlite3.Connection, mode: PersistenceMode) -> None:
        if mode == "dev":
            conn.execute(
                """
                DELETE FROM records WHERE mode = 'dev' AND run_id NOT IN (
                    SELECT run_id FROM records WHERE mode = 'dev'
                    ORDER BY created_at DESC LIMIT ?
                )
                """,
                (self._dev_ring_buffer_size,),
            )
        elif mode == "on_failure":
            cutoff = datetime.now(UTC) - timedelta(days=self._on_failure_retention_days)
            conn.execute(
                "DELETE FROM records WHERE mode = 'on_failure' AND created_at < ?",
                (_timestamp(cutoff),),
            )
        # off and always: no pruning here. "off" never reaches store; "always"
        # leaves retention to the application.
