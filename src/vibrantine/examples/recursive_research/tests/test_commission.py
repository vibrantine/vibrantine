"""Tests for RecursiveResearchCommission.

The recursive research agent is the concrete consumer that forces structural
cost rollup on the LLM-loop path: a tree of LLM-loop nodes must report the
summed cost of the whole subtree, and must enforce the budget against it.

Tests inject one scripted LLM client across every depth, so a single
scripted response queue is consumed in depth-first dispatch order. No network
and no fetches are exercised; `conclude` is always available, so leaves
conclude directly.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from openai import AsyncOpenAI

from vibrantine.contract import CallContext, estimate_tokens
from vibrantine.dispatch import dispatch
from vibrantine.examples.recursive_research import (
    RecursiveResearchCommission,
    RecursiveResearchModelMenu,
    ResearchInput,
)
from vibrantine.examples.recursive_research.types import ResearchClaim, ResearchOutput
from vibrantine.persistence import FilesystemBackend
from vibrantine.testing import ScriptedLLM, llm_response

# One LLM turn at the fixture model (google/gemini-3-flash-preview):
# (100 in * $0.50 + 50 out * $3.00) / 1M.
CALL_COST = (100 * 0.50 + 50 * 3.00) / 1_000_000


def _agent(
    responses: list[SimpleNamespace],
    *,
    max_depth: int,
) -> tuple[RecursiveResearchCommission, ScriptedLLM]:
    fake = ScriptedLLM(responses)
    agent = RecursiveResearchCommission(
        max_depth=max_depth,
        client=cast(AsyncOpenAI, fake),
        model="google/gemini-3-flash-preview",  # stable fixture for pricing math
    )
    return agent, fake


def _tool_names(commission: RecursiveResearchCommission) -> set[str]:
    return {child.name for child in commission.toolbox}


def _conclude(tc_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [(tc_id, "conclude", {"answer": "ok", "claims": []})]


def _delegate(tc_id: str, question: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [(tc_id, "recursive_research", {"question": question})]


def test_leaf_has_no_recurse_tool() -> None:
    # max_depth=0 is the base case: only fetch, so the LLM cannot delegate.
    leaf, _ = _agent([], max_depth=0)
    assert _tool_names(leaf) == {"fetch"}
    assert leaf.max_depth == 0


def test_internal_node_offers_recurse_and_fetch() -> None:
    node, _ = _agent([], max_depth=1)
    assert _tool_names(node) == {"recursive_research", "fetch"}


def test_system_prompt_loads_from_package_resource() -> None:
    assert RecursiveResearchCommission.system_prompt is not None
    assert "You are a research agent." in RecursiveResearchCommission.system_prompt
    assert "`recursive_research` tool" in RecursiveResearchCommission.system_prompt


async def test_rolls_up_child_cost_across_depth() -> None:
    # Depth-2 tree, five LLM turns total in depth-first order:
    #   root delegates -> mid delegates -> leaf concludes
    #   -> mid concludes -> root concludes
    agent, fake = _agent(
        [
            llm_response(tool_calls=_delegate("r1", "q1")),
            llm_response(tool_calls=_delegate("m1", "q1a")),
            llm_response(tool_calls=_conclude("l1")),
            llm_response(tool_calls=_conclude("m2")),
            llm_response(tool_calls=_conclude("r2")),
        ],
        max_depth=2,
    )

    result = await dispatch(agent, ResearchInput(question="q1"), CallContext())

    assert result.status == "success", result.error
    assert len(fake.calls) == 5
    # Root cost is the whole subtree: all five turns, not just the root's two.
    assert abs(result.cost.estimated_usd - 5 * CALL_COST) < 1e-9


async def test_model_menu_seats_reach_their_levels() -> None:
    # Three turns in depth-first order: root delegates, child concludes,
    # root concludes. The shared fake records model= per turn, so the seat
    # assignment is observable: calls 0 and 2 are the root (researcher
    # seat), call 1 is the delegated child (subresearcher seat).
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=_delegate("r1", "sub")),
            llm_response(tool_calls=_conclude("c1")),
            llm_response(tool_calls=_conclude("r2")),
        ]
    )
    menu = RecursiveResearchModelMenu(researcher="root-model", subresearcher="leaf-model")
    agent = RecursiveResearchCommission(max_depth=1, client=cast(AsyncOpenAI, fake), models=menu)

    result = await dispatch(agent, ResearchInput(question="q"), CallContext())

    assert result.status == "success", result.error
    assert [c["model"] for c in fake.calls] == [
        "root-model",
        "leaf-model",
        "root-model",
    ]


async def test_model_menu_default_fills_unnamed_seats() -> None:
    # Only the researcher seat is named; the delegated level falls back to
    # the menu default.
    fake = ScriptedLLM(
        [
            llm_response(tool_calls=_delegate("r1", "sub")),
            llm_response(tool_calls=_conclude("c1")),
            llm_response(tool_calls=_conclude("r2")),
        ]
    )
    menu = RecursiveResearchModelMenu(default="menu-default", researcher="root-model")
    agent = RecursiveResearchCommission(max_depth=1, client=cast(AsyncOpenAI, fake), models=menu)

    result = await dispatch(agent, ResearchInput(question="q"), CallContext())

    assert result.status == "success", result.error
    assert [c["model"] for c in fake.calls] == [
        "root-model",
        "menu-default",
        "root-model",
    ]


def test_model_and_menu_together_rejected() -> None:
    with pytest.raises(ValueError, match="not both"):
        RecursiveResearchCommission(
            model="some/model",
            models=RecursiveResearchModelMenu(default="other/model"),
        )


async def test_oversized_sub_answer_reaches_parent_flagged_partial() -> None:
    # The child's conclude blows past its 4000-token output cap. Without a
    # persistence backend, truncate_with_reference degrades to `partial`
    # without trimming: the parent's next turn receives the full answer
    # wrapped as partial_output with an output_too_large error, so the
    # parent's LLM sees the size warning without losing the child's work.
    long_answer = "x" * 20_000  # ~5000 estimated tokens, over the 4000 cap
    agent, fake = _agent(
        [
            llm_response(tool_calls=_delegate("r1", "sub")),
            llm_response(tool_calls=[("c1", "conclude", {"answer": long_answer, "claims": []})]),
            llm_response(tool_calls=_conclude("r2")),
        ],
        max_depth=1,
    )

    result = await dispatch(agent, ResearchInput(question="q"), CallContext())

    assert result.status == "success", result.error
    # Depth-first turn order: root delegates (0), child concludes (1), root
    # concludes (2). The root's second turn carries the child's tool result.
    tool_messages = [m for m in fake.calls[2]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload["error"]["kind"] == "output_too_large"
    assert payload["partial_output"]["answer"] == long_answer


async def test_oversized_sub_answer_chopped_when_backend_wired(tmp_path: Path) -> None:
    # With a persistence backend, truncate_with_reference does its real work:
    # the parent's context receives a chopped sub-answer instead of the full
    # 20k characters, and the full version is persisted, reachable by the
    # run_id the error detail names.
    backend = FilesystemBackend(tmp_path)
    long_answer = "x" * 20_000  # ~5000 estimated tokens, over the 4000 cap
    agent, fake = _agent(
        [
            llm_response(tool_calls=_delegate("r1", "sub")),
            llm_response(tool_calls=[("c1", "conclude", {"answer": long_answer, "claims": []})]),
            llm_response(tool_calls=_conclude("r2")),
        ],
        max_depth=1,
    )

    result = await dispatch(agent, ResearchInput(question="q"), CallContext(backend=backend))

    assert result.status == "success", result.error
    tool_messages = [m for m in fake.calls[2]["messages"] if m.get("role") == "tool"]
    payload = json.loads(tool_messages[0]["content"])
    assert payload["error"]["kind"] == "output_too_large"
    chopped = payload["partial_output"]["answer"]
    assert len(chopped) < len(long_answer)
    assert chopped.endswith("…[truncated]")

    # Only the forced record exists (nobody asked for recording): the child's
    # full result, under the run_id the parent's error detail names.
    refs = await backend.list_references(parent_run_id=result.run_id)
    assert len(refs) == 1
    assert refs[0] in payload["error"]["detail"]
    record = await backend.load(refs[0])
    assert record is not None
    assert record.result["output"]["answer"] == long_answer


def _claims(count: int) -> list[ResearchClaim]:
    return [
        ResearchClaim(
            value=f"claim {index}",
            source_urls=["https://example.com/src"],
            confidence="grounded",
        )
        for index in range(count)
    ]


def test_truncate_output_keeps_claims_over_prose() -> None:
    agent, _ = _agent([], max_depth=0)
    output = ResearchOutput(answer="a" * 20_000, claims=_claims(3))

    chopped = agent.truncate_output(output, 500)

    assert chopped is not None
    assert estimate_tokens(chopped.model_dump_json()) <= 500
    assert chopped.claims == output.claims  # the receipts survive
    assert chopped.answer.endswith("…[truncated]")


def test_truncate_output_drops_claims_once_answer_is_gone() -> None:
    agent, _ = _agent([], max_depth=0)
    output = ResearchOutput(answer="short", claims=_claims(40))

    chopped = agent.truncate_output(output, 100)

    assert chopped is not None
    assert estimate_tokens(chopped.model_dump_json()) <= 100
    assert 0 < len(chopped.claims) < 40  # trailing claims dropped, not all


def test_truncate_output_declines_an_absurd_cap() -> None:
    # A cap below even an empty output: the hook returns None and the policy
    # degrades to partial at the dispatch layer.
    agent, _ = _agent([], max_depth=0)
    output = ResearchOutput(answer="anything", claims=[])

    assert agent.truncate_output(output, 1) is None


async def test_budget_ceiling_counts_children() -> None:
    # Budget $0.0005 = 2.5 turns. The child succeeds (one turn, $0.0002), but
    # its cost rolled into the parent pushes the parent over after its second
    # turn: own $0.0004 + child $0.0002 = $0.0006 > $0.0005.
    agent, fake = _agent(
        [
            llm_response(tool_calls=_delegate("r1", "sub")),
            llm_response(tool_calls=_conclude("c1")),
            llm_response(tool_calls=_conclude("r2")),
        ],
        max_depth=1,
    )

    result = await dispatch(agent, ResearchInput(question="q"), CallContext(budget_usd=0.0005))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "budget_exceeded"
    assert len(fake.calls) == 3
    assert abs(result.cost.estimated_usd - 3 * CALL_COST) < 1e-9
