"""Tests for AskCommission.

Tests register a ScriptedLLM fake as the run's model catalog entry
(`scripted_model`), so every LLM call is served from a scripted sequence of
tool-call responses while dispatch, cost, and budget math run for real.
ReadTool is real so the dispatch loop exercises end-to-end through to a
deterministic in-process tool.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from vibrantine import CapabilitySet, ProgressEvent, run_commission
from vibrantine.examples.ask import AskCommission, AskInput
from vibrantine.testing import AlwaysCancelled, ScriptedLLM, llm_response, scripted_model
from vibrantine.tools.read import ReadTool


def _commission(
    responses: list[SimpleNamespace],
    *,
    max_iterations: int = 10,
) -> tuple[AskCommission, ScriptedLLM]:
    fake = ScriptedLLM(responses)
    commission = AskCommission(max_iterations=max_iterations)
    return commission, fake


async def test_ask_happy_path_read_then_conclude(tmp_path: Path) -> None:
    file = tmp_path / "fact.txt"
    file.write_text("The capital of France is Paris.\n", encoding="utf-8")

    commission, fake = _commission(
        [
            llm_response(
                tool_calls=[("c1", "read", {"path": str(file), "offset": 0, "limit": 100})]
            ),
            llm_response(tool_calls=[("c2", "conclude", {"answer": "Paris."})]),
        ]
    )

    result = await run_commission(
        commission,
        AskInput(question="What is the capital of France?", file_path=file),
        models=[scripted_model(fake)],
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.answer == "Paris."
    assert len(fake.calls) == 2
    # Two LLM turns at the llm_response defaults (100 in / 50 out each).
    assert result.cost.in_tokens == 200
    assert result.cost.out_tokens == 100


async def test_ask_paginates_when_first_read_is_truncated(tmp_path: Path) -> None:
    file = tmp_path / "long.txt"
    file.write_text("\n".join(f"line {i}" for i in range(10)), encoding="utf-8")

    commission, fake = _commission(
        [
            llm_response(tool_calls=[("c1", "read", {"path": str(file), "offset": 0, "limit": 3})]),
            llm_response(
                tool_calls=[("c2", "read", {"path": str(file), "offset": 3, "limit": 100})]
            ),
            llm_response(tool_calls=[("c3", "conclude", {"answer": "Found 10 lines."})]),
        ]
    )

    result = await run_commission(
        commission,
        AskInput(question="How many lines?", file_path=file),
        models=[scripted_model(fake)],
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.answer == "Found 10 lines."
    assert len(fake.calls) == 3


async def test_ask_exceeds_iteration_cap_returns_internal_failure(tmp_path: Path) -> None:
    file = tmp_path / "f.txt"
    file.write_text("hello", encoding="utf-8")

    # LLM keeps calling read; never concludes.
    responses = [
        llm_response(tool_calls=[("c", "read", {"path": str(file), "offset": 0, "limit": 10})])
        for _ in range(3)
    ]
    commission, fake = _commission(responses, max_iterations=3)

    result = await run_commission(
        commission,
        AskInput(question="?", file_path=file),
        models=[scripted_model(fake)],
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "iteration cap" in result.error.detail


async def test_ask_no_tool_call_returns_internal_failure(tmp_path: Path) -> None:
    file = tmp_path / "f.txt"
    file.write_text("hello", encoding="utf-8")

    # The first free-text reply earns a corrective nudge; only the second
    # fails the run (see run_llm_loop), so two are scripted here.
    commission, fake = _commission(
        [
            llm_response(tool_calls=None, content="I refuse to use a tool."),
            llm_response(tool_calls=None, content="I still refuse."),
        ]
    )

    result = await run_commission(
        commission,
        AskInput(question="?", file_path=file),
        models=[scripted_model(fake)],
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "internal"
    assert "no tool call" in result.error.detail.lower()


async def test_ask_budget_exceeded_after_first_llm_call(tmp_path: Path) -> None:
    file = tmp_path / "f.txt"
    file.write_text("hello", encoding="utf-8")

    # First call uses 10000 in + 1000 out tokens.
    # Cost = (10000*0.50 + 1000*3.00) / 1M = $0.008. Budget $0.001 → the
    # spend fuse trips at settle and the root reports run_halted.
    commission, fake = _commission(
        [
            llm_response(
                tool_calls=[("c", "read", {"path": str(file), "offset": 0, "limit": 10})],
                in_tokens=10000,
                out_tokens=1000,
            ),
            llm_response(tool_calls=[("c2", "conclude", {"answer": "hi"})]),
        ]
    )

    result = await run_commission(
        commission,
        AskInput(question="?", file_path=file),
        models=[scripted_model(fake)],
        budget_usd=0.001,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "run_halted"
    assert "spend fuse tripped" in result.error.detail
    assert len(fake.calls) == 1
    assert result.cost.estimated_usd > 0.001


async def test_ask_cancellation_at_entry_makes_no_llm_call(tmp_path: Path) -> None:
    file = tmp_path / "f.txt"
    file.write_text("hello", encoding="utf-8")

    commission, fake = _commission([llm_response(tool_calls=None)])

    result = await run_commission(
        commission,
        AskInput(question="?", file_path=file),
        models=[scripted_model(fake)],
        cancel=AlwaysCancelled(),
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert len(fake.calls) == 0


async def test_ask_tool_error_is_fed_back_and_llm_can_recover(tmp_path: Path) -> None:
    # LLM calls read on a path that doesn't exist; ReadTool returns ErrorState;
    # loop feeds that back; LLM then concludes with an apologetic answer.
    file = tmp_path / "missing.txt"  # not created

    commission, fake = _commission(
        [
            llm_response(
                tool_calls=[("c1", "read", {"path": str(file), "offset": 0, "limit": 10})]
            ),
            llm_response(tool_calls=[("c2", "conclude", {"answer": "I couldn't read the file."})]),
        ]
    )

    result = await run_commission(
        commission,
        AskInput(question="?", file_path=file),
        models=[scripted_model(fake)],
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert "couldn't" in result.output.answer.lower()
    # The second LLM call must have included the read error as a tool message.
    second_messages = fake.calls[1]["messages"]
    tool_msg = next(m for m in second_messages if m["role"] == "tool")
    assert "error" in tool_msg["content"]


async def test_ask_emits_loop_start_progress_event(tmp_path: Path) -> None:
    file = tmp_path / "f.txt"
    file.write_text("hello", encoding="utf-8")
    events: list[ProgressEvent] = []

    commission, fake = _commission([llm_response(tool_calls=[("c", "conclude", {"answer": "x"})])])

    await run_commission(
        commission,
        AskInput(question="?", file_path=file),
        models=[scripted_model(fake)],
        on_progress=events.append,
    )

    assert any(e.phase == "loop_start" and e.commission_name == "ask" for e in events)


def test_ask_toolbox_holds_injected_read() -> None:
    # Single source of truth: the same ReadTool the loop sees lives in toolbox.
    # The toolbox= kwarg overrides the class-attribute default for DI/tests.
    read = ReadTool()
    ask = AskCommission(toolbox=(read,))

    assert ask.toolbox == (read,)


def _tool_names(call_kwargs: dict[str, Any]) -> set[str]:
    """Names the LLM was offered on a given chat.completions call."""
    return {t["function"]["name"] for t in call_kwargs["tools"]}


async def test_ask_unrestricted_capabilities_offer_read(tmp_path: Path) -> None:
    # Default ctx → capabilities.tools is None → unrestricted.
    commission, fake = _commission([llm_response(tool_calls=[("c", "conclude", {"answer": "x"})])])

    await run_commission(
        commission,
        AskInput(question="?", file_path=tmp_path / "f.txt"),
        models=[scripted_model(fake)],
    )

    assert _tool_names(fake.calls[0]) == {"read", "conclude"}


async def test_ask_capabilities_excluding_read_hide_it_from_the_menu(
    tmp_path: Path,
) -> None:
    # Empty allow-list = deny all; read drops off the menu, conclude stays.
    commission, fake = _commission([llm_response(tool_calls=[("c", "conclude", {"answer": "x"})])])

    await run_commission(
        commission,
        AskInput(question="?", file_path=tmp_path / "f.txt"),
        models=[scripted_model(fake)],
        capabilities=CapabilitySet(tools=frozenset()),
    )

    names = _tool_names(fake.calls[0])
    assert "read" not in names
    assert "conclude" in names  # framework tool is never gated


async def test_ask_forbidden_tool_call_bounces_as_unknown(tmp_path: Path) -> None:
    # If the LLM calls a filtered-out tool anyway, it rides the existing
    # unknown-tool path (no separate gate) and the loop keeps going.
    commission, fake = _commission(
        [
            llm_response(tool_calls=[("c1", "read", {"path": "x", "offset": 0, "limit": 10})]),
            llm_response(tool_calls=[("c2", "conclude", {"answer": "ok"})]),
        ]
    )

    result = await run_commission(
        commission,
        AskInput(question="?", file_path=tmp_path / "f.txt"),
        models=[scripted_model(fake)],
        capabilities=CapabilitySet(tools=frozenset()),
    )

    assert result.status == "success"
    second_call_messages = fake.calls[1]["messages"]
    tool_results = [m for m in second_call_messages if m.get("role") == "tool"]
    assert any("Unknown tool" in m["content"] for m in tool_results)


# --- budget enforceability tests -------------------------------------------


async def test_ask_budget_with_unpriced_model_refuses_before_any_call(
    tmp_path: Path,
) -> None:
    # budget_usd set + a catalog entry registered without USD rates → the
    # budget can't be priced, so the call fails fast rather than running it
    # unenforced.
    file = tmp_path / "f.txt"
    file.write_text("hello", encoding="utf-8")
    commission, fake = _commission(
        [llm_response(tool_calls=[("c", "conclude", {"answer": "x"})])],
    )

    result = await run_commission(
        commission,
        AskInput(question="?", file_path=file),
        models=[scripted_model(fake, input_usd_per_million=None, output_usd_per_million=None)],
        budget_usd=1.0,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "validation"
    assert "USD rates" in result.error.detail
    assert len(fake.calls) == 0
    assert result.cost.estimated_usd == 0.0


async def test_ask_unpriced_model_without_budget_still_runs(tmp_path: Path) -> None:
    # The refusal bites only at budget+unpriced; an unpriced catalog entry
    # with no budget runs normally.
    file = tmp_path / "f.txt"
    file.write_text("hello", encoding="utf-8")
    commission, fake = _commission(
        [llm_response(tool_calls=[("c", "conclude", {"answer": "ok"})])],
    )

    result = await run_commission(
        commission,
        AskInput(question="?", file_path=file),
        models=[scripted_model(fake, input_usd_per_million=None, output_usd_per_million=None)],
    )

    assert result.status == "success", result.error
    assert result.output is not None and result.output.answer == "ok"
    assert len(fake.calls) == 1
