"""Trace consumers: the narrating backend and the cost-tree renderer."""

from datetime import UTC, datetime, timedelta
from typing import Any

from vibrantine.contract import PersistedRecord
from vibrantine.examples.ask import AskCommission
from vibrantine.examples.demo.trace import (
    RecordingBackend,
    persist_tree,
    record_cost,
    record_status,
    render_trace,
)


def make_record(
    run_id: str,
    *,
    parent: str | None = None,
    name: str = "worker",
    status: str = "success",
    cost: float = 0.01,
    error: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> PersistedRecord:
    return PersistedRecord(
        run_id=run_id,
        parent_run_id=parent,
        commission_name=name,
        mode="always",
        created_at=created_at or datetime.now(UTC),
        input={},
        result={"status": status, "cost": {"estimated_usd": cost}, "error": error},
        ctx_snapshot={},
    )


async def test_backend_stores_in_arrival_order_and_narrates() -> None:
    seen: list[str] = []
    backend = RecordingBackend(on_store=lambda r: seen.append(r.run_id))

    await backend.store(make_record("child", parent="root"))
    await backend.store(make_record("root", name="parent"))

    assert seen == ["child", "root"]
    assert [r.run_id for r in backend.records()] == ["child", "root"]
    loaded = await backend.load("child")
    assert loaded is not None and loaded.parent_run_id == "root"
    assert await backend.list_references(parent_run_id="root") == ["child"]
    assert await backend.list_references() == ["root"]


async def test_backend_delete_and_delete_older_than() -> None:
    backend = RecordingBackend()
    old = datetime.now(UTC) - timedelta(days=2)
    await backend.store(make_record("old", created_at=old))
    await backend.store(make_record("new"))

    await backend.delete("missing")  # no-op, no raise
    deleted = await backend.delete_older_than(datetime.now(UTC) - timedelta(days=1))

    assert deleted == 1
    assert [r.run_id for r in backend.records()] == ["new"]


def test_render_trace_nests_children_and_totals_roots_only() -> None:
    records = [
        make_record("leaf", parent="mid", name="fetch", cost=0.0),
        make_record(
            "mid",
            parent="root",
            name="digest",
            status="failure",
            cost=0.002,
            error={"kind": "internal", "detail": "all sources failed"},
        ),
        make_record("root", name="briefing", status="partial", cost=0.0413),
    ]

    rendered = render_trace(records)
    lines = rendered.splitlines()

    assert lines[0].startswith("briefing  partial  $0.0413")
    assert lines[1].startswith("  digest  failure  $0.0020")
    assert "internal: all sources failed" in lines[1]
    assert lines[2].startswith("    fetch  success  $0.0000")
    # Cost rolls up structurally, so the total is the root's cost alone.
    assert lines[-1] == "total  $0.0413"


def test_render_trace_handles_slice_of_larger_session() -> None:
    # A record whose parent is outside the batch renders as a root.
    rendered = render_trace([make_record("orphan", parent="elsewhere", name="ask")])
    assert rendered.splitlines()[0].startswith("ask  success")


def test_render_trace_empty() -> None:
    assert "no records" in render_trace([])


def test_record_accessors_default_safely() -> None:
    bare = make_record("r")
    bare.result.pop("cost")
    bare.result.pop("status")
    assert record_cost(bare) == 0.0
    assert record_status(bare) == "unknown"


def test_persist_tree_switches_on_whole_toolbox() -> None:
    commission = AskCommission()
    assert commission.persistence_mode == "off"

    persist_tree(commission)

    assert commission.persistence_mode == "always"
    assert all(child.persistence_mode == "always" for child in commission.toolbox)
