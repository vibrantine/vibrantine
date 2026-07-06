"""Demo observability: a narrating in-memory backend, cost-tree and transcript renderers.

A worked example of the application layer consuming the public persistence
contract: `dispatch` writes one `PersistedRecord` per completed sub-call
through `CallContext.backend`, so a backend that narrates each `store` gives
live completion events, and the finished records render as a cost tree.
Nothing here reaches past the public surface; if the trace can't be rendered
from `PersistedRecord`s alone, the record shape is what should improve.
"""

from collections.abc import Callable
from datetime import datetime
from itertools import count
from typing import Any, cast

from vibrantine.contract import PersistedRecord


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


def _tree(
    records: list[PersistedRecord],
) -> tuple[list[PersistedRecord], dict[str | None, list[PersistedRecord]]]:
    """Group records into (roots, children-by-parent) for a depth-first walk.

    Roots are records whose parent is absent from the batch, so a slice of a
    larger session renders as its own subtree.
    """
    known_ids = {record.run_id for record in records}
    children: dict[str | None, list[PersistedRecord]] = {}
    roots: list[PersistedRecord] = []
    for record in records:
        if record.parent_run_id in known_ids:
            children.setdefault(record.parent_run_id, []).append(record)
        else:
            roots.append(record)
    return roots, children


def trace_order(records: list[PersistedRecord]) -> list[PersistedRecord]:
    """Records in the depth-first order `render_trace` prints them.

    Maps the `[n]` labels in a rendered trace back to records (1-based), so a
    caller can offer "show me node n's transcript" against the same numbering
    the user just read.
    """
    roots, children = _tree(records)
    ordered: list[PersistedRecord] = []

    def walk(record: PersistedRecord) -> None:
        ordered.append(record)
        for child in children.get(record.run_id, []):
            walk(child)

    for root in roots:
        walk(root)
    return ordered


def render_trace(records: list[PersistedRecord]) -> str:
    """Render records as an indented tree with per-node cost and a rolled-up total.

    Each node is numbered `[n]` in print order; `trace_order` yields the same
    order, so n resolves back to a record. Because cost rolls up structurally,
    the total is the sum of root costs, not of every node.
    """
    if not records:
        return "(no records persisted for this run)"

    roots, children = _tree(records)
    lines: list[str] = []
    number = count(1)

    def walk(record: PersistedRecord, depth: int) -> None:
        lines.append("  " * depth + f"[{next(number)}] " + _record_line(record))
        for child in children.get(record.run_id, []):
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)

    total = sum(record_cost(root) for root in roots)
    lines.append(f"total  ${total:.4f}")
    return "\n".join(lines)


def _clip(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _content_text(content: Any) -> str:
    """Flatten a message's content (a string, or multimodal parts) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for part in cast("list[Any]", content):
            if not isinstance(part, dict):
                pieces.append("[part]")
                continue
            typed = cast("dict[str, Any]", part)
            if typed.get("type") == "text":
                pieces.append(str(typed.get("text", "")))
            else:
                pieces.append(f"[{typed.get('type', 'part')}]")
        return " ".join(pieces)
    return ""


def render_transcript(record: PersistedRecord) -> str:
    """Render a record's LLM transcript, one clipped role-labelled line per message.

    First consumer of the raw `llm_trace` JSON: whatever structure this
    renderer needs and can't reach is what the trace shape should grow next.
    A record with no trace (a Commission whose invoke never deposited one)
    says so rather than rendering empty.
    """
    if record.llm_trace is None:
        return f"(no LLM transcript recorded for {record.commission_name})"
    lines = [f"--- {record.commission_name} transcript ({len(record.llm_trace)} messages) ---"]
    for message in record.llm_trace:
        role = str(message.get("role", "?"))
        tool_calls = cast("list[dict[str, Any]]", message.get("tool_calls") or [])
        for call in tool_calls:
            function = cast("dict[str, Any]", call.get("function") or {})
            name = str(function.get("name", "?"))
            arguments = _clip(str(function.get("arguments", "")))
            lines.append(f"{role:<9} -> {name}({arguments})")
        text = _content_text(message.get("content"))
        if text:
            lines.append(f"{role:<9} {_clip(text)}")
    return "\n".join(lines)
