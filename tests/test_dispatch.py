"""Tests for the dispatch helper.

Built around stub Commissions that emit a known result. Each test
exercises one slice of dispatch's wrapping behavior: run_id generation,
parent_run_id threading across nested dispatch calls (including under
asyncio.gather), persistence-mode honoring, and overflow_policy
enforcement.
"""

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, cast

import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    CostMetrics,
    ErrorState,
    OverflowPolicy,
    PersistenceMode,
    ProgressEvent,
    Provenance,
)
from vibrantine.dispatch import dispatch
from vibrantine.persistence import FilesystemBackend
from vibrantine.testing import ScriptedLLM, llm_response


class _Input(BaseModel):
    q: str = Field(description="prompt-y thing")


class _Output(BaseModel):
    a: str = Field(description="answer-y thing")


def _success_result(answer: str = "ok") -> CommissionResult[_Output]:
    return CommissionResult[_Output](
        status="success",
        output=_Output(a=answer),
        provenance=Provenance(source="stub", fetched_at=datetime.now(UTC), confidence="grounded"),
        cost=CostMetrics(estimated_usd=0.0),
    )


def _failure_result(detail: str = "nope") -> CommissionResult[_Output]:
    return cast(
        CommissionResult[_Output],
        CommissionResult(
            status="failure",
            error=ErrorState(kind="internal", detail=detail, retryable=False),
            provenance=Provenance(
                source="stub",
                fetched_at=datetime.now(UTC),
                confidence="grounded",
            ),
            cost=CostMetrics(estimated_usd=0.0),
        ),
    )


class _Stub(Commission[_Input, _Output]):
    """Returns a pre-scripted result; records the ctx it saw."""

    name: ClassVar[str] = "stub"
    description: ClassVar[str] = "test stub"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(
        self,
        *,
        result: CommissionResult[_Output] | None = None,
        persistence_mode: PersistenceMode | None = None,
        max_output_tokens: int | None = None,
        overflow_policy: OverflowPolicy = "flag",
    ) -> None:
        super().__init__(
            persistence_mode=persistence_mode,
            max_output_tokens=max_output_tokens,
            overflow_policy=overflow_policy,
        )
        self._scripted = result or _success_result()
        self.seen_ctx: CallContext | None = None

    async def invoke(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        self.seen_ctx = ctx
        return self._scripted


class _Raiser(Commission[_Input, _Output]):
    """Raises from invoke: a Commission that breaks the errors-as-values rule."""

    name: ClassVar[str] = "raiser"
    description: ClassVar[str] = "test stub that raises"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(
        self,
        exc: BaseException,
        *,
        persistence_mode: PersistenceMode | None = None,
    ) -> None:
        super().__init__(persistence_mode=persistence_mode)
        self._exc = exc

    async def invoke(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        raise self._exc


class _Parent(Commission[_Input, _Output]):
    """Calls dispatch on a child; for chain-tracking tests."""

    name: ClassVar[str] = "parent"
    description: ClassVar[str] = "parent that dispatches one child"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(self, child: Commission[_Input, _Output]) -> None:
        super().__init__()
        self._child = child

    async def invoke(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        await dispatch(self._child, input, ctx)
        return _success_result("from-parent")


class _GatherParent(Commission[_Input, _Output]):
    """Dispatches two children concurrently via asyncio.gather."""

    name: ClassVar[str] = "gather_parent"
    description: ClassVar[str] = "parent that fans two children"
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output

    def __init__(
        self,
        child_a: Commission[_Input, _Output],
        child_b: Commission[_Input, _Output],
    ) -> None:
        super().__init__()
        self._a = child_a
        self._b = child_b

    async def invoke(self, input: _Input, ctx: CallContext) -> CommissionResult[_Output]:
        await asyncio.gather(
            dispatch(self._a, input, ctx),
            dispatch(self._b, input, ctx),
        )
        return _success_result("from-gather-parent")


# --- run_id + chain tests --------------------------------------------------


async def test_dispatch_generates_run_id_on_result() -> None:
    stub = _Stub()
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.run_id is not None
    assert len(result.run_id) > 0


async def test_dispatch_parent_run_id_is_none_at_top_level() -> None:
    stub = _Stub()
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.parent_run_id is None


async def test_dispatch_child_sees_parent_run_id_in_ctx() -> None:
    child = _Stub()
    parent = _Parent(child)
    parent_result = await dispatch(parent, _Input(q="?"), CallContext())

    assert child.seen_ctx is not None
    assert child.seen_ctx.parent_run_id == parent_result.run_id


async def test_dispatch_gathered_children_share_correct_parent() -> None:
    a, b = _Stub(), _Stub()
    parent = _GatherParent(a, b)
    parent_result = await dispatch(parent, _Input(q="?"), CallContext())

    assert a.seen_ctx is not None and b.seen_ctx is not None
    assert a.seen_ctx.parent_run_id == parent_result.run_id
    assert b.seen_ctx.parent_run_id == parent_result.run_id


# --- persistence tests -----------------------------------------------------


async def test_dispatch_off_does_not_call_backend(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="off")
    result = await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    assert await backend.list_references() == []
    # run_id still stamped (always free), but no record written.
    assert result.run_id is not None


async def test_dispatch_always_persists_every_run(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="always")
    result = await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_dev_persists_every_run(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="dev")
    result = await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_on_failure_persists_failures(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure", result=_failure_result())
    result = await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    refs = await backend.list_references()
    assert refs == [result.run_id]


async def test_dispatch_on_failure_skips_successes(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure")
    await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    assert await backend.list_references() == []


async def test_dispatch_no_opinion_and_no_ctx_record_stays_off(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub()  # persistence_mode None (no opinion), no ctx.record either
    await dispatch(stub, _Input(q="?"), CallContext(backend=backend))

    assert await backend.list_references() == []


async def test_ctx_record_switches_on_the_whole_tree(tmp_path: Path) -> None:
    # The caller's record= default reaches every no-opinion node, parent and
    # child alike, with no per-node flipping; the record stores the effective
    # mode the node ran under.
    backend = FilesystemBackend(tmp_path)
    child = _Stub()
    parent = _Parent(child)

    parent_result = await dispatch(
        parent, _Input(q="?"), CallContext(backend=backend, record="always")
    )

    assert await backend.list_references() == [parent_result.run_id]
    child_refs = await backend.list_references(parent_run_id=parent_result.run_id)
    assert len(child_refs) == 1
    loaded = await backend.load(child_refs[0])
    assert loaded is not None
    assert loaded.mode == "always"


async def test_explicit_off_vetoes_ctx_record(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="off")
    await dispatch(stub, _Input(q="?"), CallContext(backend=backend, record="always"))

    assert await backend.list_references() == []


async def test_explicit_mode_beats_ctx_record(tmp_path: Path) -> None:
    # Node says on_failure, caller says always: the node's word is kept, so
    # this success is not recorded.
    backend = FilesystemBackend(tmp_path)
    stub = _Stub(persistence_mode="on_failure")
    await dispatch(stub, _Input(q="?"), CallContext(backend=backend, record="always"))

    assert await backend.list_references() == []


async def test_dispatch_no_backend_skips_even_with_active_mode(
    tmp_path: Path,
) -> None:
    stub = _Stub(persistence_mode="always")
    # No backend on ctx → nothing to persist to → result still flows.
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.status == "success"


async def test_dispatch_persisted_record_has_chain_and_payloads(
    tmp_path: Path,
) -> None:
    backend = FilesystemBackend(tmp_path)
    child = _Stub(persistence_mode="always")
    parent = _Parent(child)
    parent_result = await dispatch(parent, _Input(q="ask"), CallContext(backend=backend))

    child_refs = await backend.list_references(parent_run_id=parent_result.run_id)
    assert len(child_refs) == 1
    loaded = await backend.load(child_refs[0])
    assert loaded is not None
    assert loaded.parent_run_id == parent_result.run_id
    assert loaded.commission_name == "stub"
    assert loaded.input == {"q": "ask"}
    assert loaded.result["status"] == "success"


class _ExplodingBackend:
    """A PersistenceBackend whose store always raises."""

    async def store(self, record: object) -> None:
        raise OSError("disk full")

    async def load(self, run_id: str) -> None:
        return None

    async def list_references(self, *, parent_run_id: str | None = None) -> list[str]:
        return []

    async def delete(self, run_id: str) -> None:
        return None

    async def delete_older_than(self, cutoff: datetime) -> int:
        return 0


async def test_dispatch_failing_backend_does_not_destroy_the_result() -> None:
    # Persistence is observability: a backend that raises on store must not
    # break errors-as-values or eat the result it was recording.
    events: list[ProgressEvent] = []
    stub = _Stub(persistence_mode="always")
    result = await dispatch(
        stub,
        _Input(q="?"),
        CallContext(backend=_ExplodingBackend(), on_progress=events.append),
    )

    assert result.status == "success"
    assert result.output is not None
    assert any(e.phase == "persist_failed" and "disk full" in (e.detail or "") for e in events)


# --- overflow tests --------------------------------------------------------


async def test_dispatch_no_max_output_tokens_skips_check() -> None:
    stub = _Stub(max_output_tokens=None)
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.status == "success"
    assert result.output is not None


async def test_dispatch_under_cap_returns_unchanged() -> None:
    stub = _Stub(max_output_tokens=1000, overflow_policy="reject")
    result = await dispatch(stub, _Input(q="?"), CallContext())
    assert result.status == "success"


async def test_dispatch_overflow_reject_clears_output_and_fails() -> None:
    big = "x" * 4000  # ~1000 tokens
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="reject",
    )
    result = await dispatch(stub, _Input(q="?"), CallContext())

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "output_too_large"


async def test_dispatch_overflow_partial_marks_partial_keeps_output() -> None:
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="partial",
    )
    result = await dispatch(stub, _Input(q="?"), CallContext())

    assert result.status == "partial"
    assert result.output is not None  # data still usable
    assert result.error is not None
    assert result.error.kind == "output_too_large"


async def test_dispatch_overflow_flag_emits_progress_event_unchanged() -> None:
    events: list[ProgressEvent] = []
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="flag",
    )
    result = await dispatch(stub, _Input(q="?"), CallContext(on_progress=events.append))

    assert result.status == "success"
    assert result.output is not None and result.output.a == big
    assert any(e.phase == "output_overflow" for e in events)


async def test_dispatch_overflow_truncate_stub_degrades_to_partial() -> None:
    # truncate_with_reference is a frozen-vocabulary policy whose real mechanic
    # (chop + persist-reference) is a near-term TODO. The stub must be
    # non-breaking: degrade to partial, preserve the output, flag it in the
    # jacket.
    big = "x" * 4000
    stub = _Stub(
        result=_success_result(answer=big),
        max_output_tokens=10,
        overflow_policy="truncate_with_reference",
    )
    result = await dispatch(stub, _Input(q="?"), CallContext())

    assert result.status == "partial"
    assert result.output is not None and result.output.a == big  # output preserved
    assert result.error is not None
    assert result.error.kind == "output_too_large"
    assert "stubbed" in result.error.detail


# --- exception-to-failure tests --------------------------------------------


async def test_dispatch_converts_raised_exception_to_failure() -> None:
    result = await dispatch(_Raiser(ValueError("boom")), _Input(q="?"), CallContext())

    assert result.status == "failure"
    assert result.output is None
    assert result.error is not None
    assert result.error.kind == "internal"
    assert result.error.retryable is False
    assert "ValueError" in result.error.detail
    assert "boom" in result.error.detail
    # stamped like any other wrapped call
    assert result.run_id is not None
    assert result.parent_run_id is None


async def test_dispatch_persists_raised_exception_on_failure(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    raiser = _Raiser(RuntimeError("kaboom"), persistence_mode="on_failure")
    result = await dispatch(raiser, _Input(q="?"), CallContext(backend=backend))

    # The gap this closes: a raising Commission still gets its failure recorded.
    assert result.run_id is not None
    refs = await backend.list_references()
    assert refs == [result.run_id]
    loaded = await backend.load(result.run_id)
    assert loaded is not None
    assert loaded.result["status"] == "failure"
    assert loaded.result["error"]["kind"] == "internal"


async def test_dispatch_does_not_swallow_cancellation() -> None:
    # CancelledError is a BaseException, not an Exception: task cancellation must
    # propagate, never be converted to an `internal` failure value.
    with pytest.raises(asyncio.CancelledError):
        await dispatch(_Raiser(asyncio.CancelledError()), _Input(q="?"), CallContext())


# --- stdlib logging tests ---------------------------------------------------


async def test_run_emits_stdlib_logs(caplog: pytest.LogCaptureFixture) -> None:
    # The framework emits through standard logging at its choke points; an
    # application that sets a level sees every call and every LLM turn with
    # zero vibrantine-specific setup. INFO = one line per LLM round-trip
    # (from the loop) plus one line per completed call (from dispatch).
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"a": "done"})])])
    probe = _LoopProbe(client=cast(AsyncOpenAI, fake))

    with caplog.at_level(logging.INFO, logger="vibrantine"):
        await dispatch(probe, _Input(q="hi"), CallContext())

    assert "LLM turn model=" in caplog.text
    assert "loop_probe finished status=success" in caplog.text


async def test_conclude_validation_failure_logs_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The live-debugging lesson: a conclude that keeps failing validation must
    # be visible without persistence set up.
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("c1", "conclude", {"wrong_field": 1})]),
            llm_response(tool_calls=[("c2", "conclude", {"a": "ok"})]),
        ]
    )
    probe = _LoopProbe(client=cast(AsyncOpenAI, fake))

    with caplog.at_level(logging.WARNING, logger="vibrantine"):
        result = await dispatch(probe, _Input(q="hi"), CallContext())

    assert result.status == "success"
    assert "conclude args failed to validate as _Output" in caplog.text


# --- llm_trace mailbox tests ------------------------------------------------


class _LoopProbe(Commission[_Input, _Output]):
    """A basic LLM-loop Commission for trace tests; toolbox set per instance."""

    name: ClassVar[str] = "loop_probe"
    description: ClassVar[str] = "Test probe riding the default LLM loop."
    input_type: ClassVar[type[BaseModel]] = _Input
    output_type: ClassVar[type[BaseModel]] = _Output
    system_prompt: ClassVar[str | None] = "Conclude."

    def build_user_message(self, input: _Input, ctx: CallContext) -> str:
        return input.q


async def test_loop_trace_lands_in_the_record(tmp_path: Path) -> None:
    backend = FilesystemBackend(tmp_path)
    fake = ScriptedLLM([llm_response(tool_calls=[("c1", "conclude", {"a": "done"})])])
    probe = _LoopProbe(client=cast(AsyncOpenAI, fake), persistence_mode="always")

    result = await dispatch(probe, _Input(q="hello"), CallContext(backend=backend))

    assert result.run_id is not None
    loaded = await backend.load(result.run_id)
    assert loaded is not None
    assert loaded.llm_trace is not None
    assert [m["role"] for m in loaded.llm_trace[:2]] == ["system", "user"]
    assert loaded.llm_trace[1]["content"] == "hello"
    # The concluding assistant turn is part of the transcript.
    assert any(m.get("tool_calls") for m in loaded.llm_trace)


async def test_loop_trace_survives_a_failed_run(tmp_path: Path) -> None:
    # Failure traces are the ones worth autopsying: two free-text replies end
    # the run as an internal failure, and the transcript (including the
    # corrective nudge) must still reach the record.
    backend = FilesystemBackend(tmp_path)
    fake = ScriptedLLM([llm_response(content="prose"), llm_response(content="more prose")])
    probe = _LoopProbe(client=cast(AsyncOpenAI, fake), persistence_mode="on_failure")

    result = await dispatch(probe, _Input(q="hi"), CallContext(backend=backend))

    assert result.status == "failure"
    assert result.run_id is not None
    loaded = await backend.load(result.run_id)
    assert loaded is not None
    assert loaded.llm_trace is not None
    nudges = [
        m for m in loaded.llm_trace if m["role"] == "user" and "conclude" in str(m["content"])
    ]
    assert nudges, "the corrective nudge should appear in the recorded transcript"


async def test_nested_traces_stay_with_their_own_records(tmp_path: Path) -> None:
    # A parent loop dispatches a child loop mid-run. Each record must carry
    # exactly its own transcript: the mailbox re-hangs the parent's box after
    # the child call, so nothing merges or crosses.
    backend = FilesystemBackend(tmp_path)
    child_fake = ScriptedLLM([llm_response(tool_calls=[("cc", "conclude", {"a": "child-done"})])])
    child = _LoopProbe(client=cast(AsyncOpenAI, child_fake), persistence_mode="always")
    parent_fake = ScriptedLLM(
        [
            llm_response(tool_calls=[("p1", "loop_probe", {"q": "child-q"})]),
            llm_response(tool_calls=[("p2", "conclude", {"a": "parent-done"})]),
        ]
    )
    parent = _LoopProbe(
        client=cast(AsyncOpenAI, parent_fake),
        toolbox=(child,),
        persistence_mode="always",
    )

    result = await dispatch(parent, _Input(q="parent-q"), CallContext(backend=backend))

    assert result.status == "success", result.error
    parent_record = await backend.load(result.run_id or "")
    assert parent_record is not None and parent_record.llm_trace is not None
    child_ids = await backend.list_references(parent_run_id=result.run_id)
    assert len(child_ids) == 1
    child_record = await backend.load(child_ids[0])
    assert child_record is not None and child_record.llm_trace is not None

    # Child transcript: system, user, concluding assistant. Its user turn is
    # the tool-call args the parent sent, never the parent's own message.
    assert len(child_record.llm_trace) == 3
    assert child_record.llm_trace[1]["content"] == "child-q"
    assert "parent-q" not in str(child_record.llm_trace)

    # Parent transcript: system, user, delegating assistant, tool result,
    # concluding assistant. The child's *result* appears (that's the envelope
    # crossing the boundary); the child's interior turns do not.
    assert len(parent_record.llm_trace) == 5
    assert parent_record.llm_trace[1]["content"] == "parent-q"
    assert parent_record.llm_trace[3]["role"] == "tool"
