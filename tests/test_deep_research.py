"""Tests for DeepResearchCommission.

The recursive research agent is the concrete consumer that forces structural
cost rollup on the LLM-loop path: a tree of LLM-loop nodes must report the
summed cost of the whole subtree, and must enforce the budget against it.

Tests inject one shared fake AsyncOpenAI-shaped client across every depth, so a
single scripted response queue is consumed in depth-first dispatch order. No
network and no fetches are exercised — `conclude` is always available, so leaves
conclude directly.
"""

from types import SimpleNamespace
from typing import Any, cast

from conftest import FakeClient, llm_response
from openai import AsyncOpenAI

from vibrantine.commissions.deep_research import (
    DeepResearchCommission,
    ResearchInput,
)
from vibrantine.contract import CallContext

# One LLM turn at the fixture model (google/gemini-3-flash-preview):
# (100 in * $0.50 + 50 out * $3.00) / 1M.
CALL_COST = (100 * 0.50 + 50 * 3.00) / 1_000_000


def _agent(
    responses: list[SimpleNamespace],
    *,
    max_depth: int,
) -> tuple[DeepResearchCommission, FakeClient]:
    fake = FakeClient(responses)
    agent = DeepResearchCommission(
        max_depth=max_depth,
        client=cast(AsyncOpenAI, fake),
        model="google/gemini-3-flash-preview",  # stable fixture for pricing math
    )
    return agent, fake


def _tool_names(commission: DeepResearchCommission) -> set[str]:
    return {child.name for child in commission.toolbox}


def _conclude(tc_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [(tc_id, "conclude", {"answer": "ok", "claims": []})]


def _delegate(tc_id: str, question: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [(tc_id, "deep_research", {"question": question})]


def test_leaf_has_no_recurse_tool() -> None:
    # max_depth=0 is the base case: only fetch, so the LLM cannot delegate.
    leaf, _ = _agent([], max_depth=0)
    assert _tool_names(leaf) == {"fetch"}
    assert leaf.max_depth == 0


def test_internal_node_offers_recurse_and_fetch() -> None:
    node, _ = _agent([], max_depth=1)
    assert _tool_names(node) == {"deep_research", "fetch"}


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

    result = await agent.invoke(ResearchInput(question="q1"), CallContext())

    assert result.status == "success", result.error
    assert len(fake.completions.calls) == 5
    # Root cost is the whole subtree: all five turns, not just the root's two.
    assert abs(result.cost.estimated_usd - 5 * CALL_COST) < 1e-9


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

    result = await agent.invoke(ResearchInput(question="q"), CallContext(budget_usd=0.0005))

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "budget_exceeded"
    assert len(fake.completions.calls) == 3
    assert abs(result.cost.estimated_usd - 3 * CALL_COST) < 1e-9
