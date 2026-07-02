"""Default filesystem persistence backend for commission runs.

Stores each PersistedRecord as a JSON file under a configurable root
directory. Pruning happens on every store, per the record's mode (dev →
ring buffer, on_failure → time TTL, always → never). The library ships
this as the default backend; external apps can swap in their own
implementation against the PersistenceBackend Protocol in contract.py.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

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
        self._root = root
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
        return self._root / f"{run_id}.json"

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

        Pruning skips corrupt records rather than crashing the whole run —
        the alternative (failing pruning) defeats the "don't fill the disk"
        guarantee. Listing and explicit load() still raise.
        """
        try:
            return PersistedRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None
