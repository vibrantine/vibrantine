"""Trace consumers: the narrating backend and the cost-tree renderer."""

from datetime import UTC, datetime, timedelta
from typing import Any

from vibrantine import (
    CommissionResult,
    CommissionStatus,
    CostMetrics,
    ErrorState,
    PersistedRecord,
    Provenance,
)
from vibrantine.examples.demo.trace import (
    RecordingBackend,
    record_cost,
    record_status,
    render_trace,
    render_transcript,
    trace_order,
)


def make_record(
    run_id: str,
    *,
    parent: str | None = None,
    name: str = "worker",
    status: CommissionStatus = "success",
    cost: float = 0.01,
    error: ErrorState | None = None,
    created_at: datetime | None = None,
    llm_trace: list[dict[str, Any]] | None = None,
) -> PersistedRecord:
    # The result dict is a real CommissionResult dump, exactly what dispatch
    # persists, so these tests break loudly if the envelope's serialized
    # shape ever drifts from what the renderers assume.
    resolved_error = error
    if status != "success" and resolved_error is None:
        resolved_error = ErrorState(
            kind="internal",
            detail=f"{name} produced a {status} fixture.",
            retryable=False,
        )
    result = CommissionResult[Any](
        status=status,
        output=None if status == "failure" else {"fixture": run_id},
        error=resolved_error,
        provenance=Provenance(
            source=name,
            fetched_at=created_at or datetime.now(UTC),
            confidence="grounded",
        ),
        cost=CostMetrics(estimated_usd=cost),
    )
    return PersistedRecord(
        run_id=run_id,
        parent_run_id=parent,
        commission_name=name,
        mode="always",
        created_at=created_at or datetime.now(UTC),
        input={},
        result=result.model_dump(),
        ctx_snapshot={},
        llm_trace=llm_trace,
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
            error=ErrorState(kind="internal", detail="all sources failed", retryable=True),
        ),
        make_record("root", name="briefing", status="partial", cost=0.0413),
    ]

    rendered = render_trace(records)
    lines = rendered.splitlines()

    assert lines[0].startswith("[1] briefing  partial  $0.0413")
    assert lines[1].startswith("  [2] digest  failure  $0.0020")
    assert "internal: all sources failed" in lines[1]
    assert lines[2].startswith("    [3] fetch  success  $0.0000")
    # Cost rolls up structurally, so the total is the root's cost alone.
    assert lines[-1] == "total  $0.0413"
    # The [n] labels resolve back to records through the same walk.
    assert [r.run_id for r in trace_order(records)] == ["root", "mid", "leaf"]


def test_render_trace_handles_slice_of_larger_session() -> None:
    # A record whose parent is outside the batch renders as a root.
    rendered = render_trace([make_record("orphan", parent="elsewhere", name="ask")])
    assert rendered.splitlines()[0].startswith("[1] ask  success")


def test_render_trace_empty() -> None:
    assert "no records" in render_trace([])


def test_render_transcript_labels_roles_calls_and_clips() -> None:
    record = make_record(
        "r",
        name="ask",
        llm_trace=[
            {"role": "system", "content": "You answer questions about a file."},
            {"role": "user", "content": "What does it say?\nBe brief."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "x.py"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 500},
            {"role": "assistant", "content": "It says hello."},
        ],
    )

    lines = render_transcript(record).splitlines()

    assert lines[0] == "--- ask transcript (5 messages) ---"
    assert lines[1].startswith("system") and "answer questions" in lines[1]
    # Newlines inside a message collapse to keep one line per message.
    assert "What does it say? Be brief." in lines[2]
    assert '-> read_file({"path": "x.py"})' in lines[3]
    assert lines[4].endswith("...") and len(lines[4]) < 200
    assert "It says hello." in lines[5]


def test_render_transcript_multimodal_and_missing() -> None:
    with_parts = make_record(
        "r",
        name="vision",
        llm_trace=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ],
    )
    rendered = render_transcript(with_parts)
    assert "describe this [image_url]" in rendered

    bare = make_record("r2", name="synthesize")
    assert "no LLM transcript recorded for synthesize" in render_transcript(bare)


def test_record_accessors_default_safely() -> None:
    bare = make_record("r")
    bare.result.pop("cost")
    bare.result.pop("status")
    assert record_cost(bare) == 0.0
    assert record_status(bare) == "unknown"
