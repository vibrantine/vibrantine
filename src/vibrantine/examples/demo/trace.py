"""Demo observability: an in-memory narrating backend and a cost-trace renderer.

A worked example of the application layer consuming the public persistence
contract: `dispatch` writes one `PersistedRecord` per completed sub-call
through `CallContext.backend`, so a backend that narrates each `store` gives
live completion events, and the finished records render as a cost tree.
Nothing here reaches past the public surface; if the trace can't be rendered
from `PersistedRecord`s alone, the record shape is what should improve.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from vibrantine.contract import Commission, PersistedRecord


def persist_tree(commission: Commission[Any, Any]) -> None:
    """Switch persistence on for a Commission and everything in its toolbox.

    `persistence_mode` defaults to "off"; instance assignment is the
    documented override path, and the toolbox is the public composition
    surface, so a consumer opts a whole tree into record-keeping without
    framework help.
    """
    commission.persistence_mode = "always"
    for child in commission.toolbox:
        persist_tree(child)


class RecordingBackend:
    """In-memory `PersistenceBackend` that can narrate each record as it lands."""

    def __init__(self, on_store: Callable[[PersistedRecord], None] | None = None) -> None:
        self._records: dict[str, PersistedRecord] = {}
        self._order: list[str] = []
        self._on_store = on_store

    async def store(self, record: PersistedRecord) -> None:
        if record.run_id not in self._records:
            self._order.append(record.run_id)
        self._records[record.run_id] = record
        if self._on_store is not None:
            self._on_store(record)

    async def load(self, run_id: str) -> PersistedRecord | None:
        return self._records.get(run_id)

    async def list_references(self, *, parent_run_id: str | None = None) -> list[str]:
        return [
            run_id for run_id in self._order if self._records[run_id].parent_run_id == parent_run_id
        ]

    async def delete(self, run_id: str) -> None:
        if run_id in self._records:
            del self._records[run_id]
            self._order.remove(run_id)

    async def delete_older_than(self, cutoff: datetime) -> int:
        stale = [run_id for run_id in self._order if self._records[run_id].created_at < cutoff]
        for run_id in stale:
            await self.delete(run_id)
        return len(stale)

    def records(self) -> list[PersistedRecord]:
        """All stored records in arrival order (children complete before parents)."""
        return [self._records[run_id] for run_id in self._order]


def record_status(record: PersistedRecord) -> str:
    return str(record.result.get("status", "unknown"))


def record_cost(record: PersistedRecord) -> float:
    cost: dict[str, Any] = record.result.get("cost") or {}
    return float(cost.get("estimated_usd", 0.0))


def _record_line(record: PersistedRecord) -> str:
    line = f"{record.commission_name}  {record_status(record)}  ${record_cost(record):.4f}"
    error: dict[str, Any] | None = record.result.get("error")
    if error is not None:
        detail = str(error.get("detail", ""))
        if len(detail) > 100:
            detail = detail[:97] + "..."
        line += f"  ({error.get('kind', 'unknown')}: {detail})"
    return line


def render_trace(records: list[PersistedRecord]) -> str:
    """Render records as an indented tree with per-node cost and a rolled-up total.

    Roots are records whose parent is absent from the batch, so a slice of a
    larger session renders as its own subtree. Because cost rolls up
    structurally, the total is the sum of root costs, not of every node.
    """
    if not records:
        return "(no records persisted for this run)"

    known_ids = {record.run_id for record in records}
    children: dict[str | None, list[PersistedRecord]] = {}
    roots: list[PersistedRecord] = []
    for record in records:
        if record.parent_run_id in known_ids:
            children.setdefault(record.parent_run_id, []).append(record)
        else:
            roots.append(record)

    lines: list[str] = []

    def walk(record: PersistedRecord, depth: int) -> None:
        lines.append("  " * depth + _record_line(record))
        for child in children.get(record.run_id, []):
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)

    total = sum(record_cost(root) for root in roots)
    lines.append(f"total  ${total:.4f}")
    return "\n".join(lines)
