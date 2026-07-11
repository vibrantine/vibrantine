"""Tests for the EmailHandler Commission (provisional route-and-execute validator).

EmailHandler rides the default _run (the LLM loop) over a sub-Commission
(DraftReply) and a tool (NotifyUser). Tests inject a fake AsyncOpenAI-shaped
client returning scripted tool calls, with the real stub handlers, so routing,
dispatched-child cost rollup, and the heterogeneous-output flattening are all
exercised end-to-end.
"""

from types import SimpleNamespace
from typing import cast

from openai import AsyncOpenAI

from vibrantine import run_one
from vibrantine.examples.email_handler import (
    EmailHandlerCommission,
    EmailHandlerInput,
    IncomingEmail,
)
from vibrantine.models import Model
from vibrantine.testing import FIXTURE_MODEL, AlwaysCancelled, ScriptedLLM, llm_response

_EMAIL_ARGS = {"sender": "a@b.test", "subject": "Quick question", "body": "Body text."}


def _input() -> EmailHandlerInput:
    return EmailHandlerInput(email=IncomingEmail(**_EMAIL_ARGS))


def _commission(
    responses: list[SimpleNamespace],
    *,
    model: str | Model = FIXTURE_MODEL,
) -> tuple[EmailHandlerCommission, ScriptedLLM]:
    fake = ScriptedLLM(responses)
    commission = EmailHandlerCommission(client=cast(AsyncOpenAI, fake), model=model)
    return commission, fake


async def test_email_handler_non_urgent_concludes_directly() -> None:
    commission, fake = _commission(
        [
            llm_response(
                tool_calls=[
                    ("c", "conclude", {"route": "non_urgent", "rationale": "no action needed"})
                ]
            ),
        ]
    )

    result = await run_one(commission, _input())

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.route == "non_urgent"
    # Flattening: the route-specific fields stay at their defaults off-route.
    assert result.output.draft_text is None
    assert result.output.notification_sent is False
    assert len(fake.calls) == 1


async def test_email_handler_draft_route_rolls_up_child_cost() -> None:
    # Unpriced model → own cost is $0, isolating the dispatched DraftReply's
    # cost so the rollup is the only thing the assertion can be measuring.
    commission, fake = _commission(
        [
            llm_response(tool_calls=[("c1", "draft_reply", {"email": _EMAIL_ARGS})]),
            llm_response(
                tool_calls=[
                    (
                        "c2",
                        "conclude",
                        {
                            "route": "draft",
                            "rationale": "a reply is warranted",
                            "draft_text": "Hi, thanks for reaching out.",
                        },
                    )
                ]
            ),
        ],
        model="unregistered/model",
    )

    result = await run_one(commission, _input())

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.route == "draft"
    assert result.output.draft_text == "Hi, thanks for reaching out."
    assert len(fake.calls) == 2
    # DraftReply's stub reports $0.0023; run_llm_loop folds it into the parent's
    # cost. With own cost zeroed by the unpriced model, the total IS the child's.
    assert abs(result.cost.estimated_usd - 0.0023) < 1e-9
    # Dollars roll up; token counts do not. Two parent turns at the
    # llm_response defaults (100 in / 50 out each), child tokens excluded.
    assert result.cost.in_tokens == 200
    assert result.cost.out_tokens == 100


async def test_email_handler_notify_route_dispatches_tool() -> None:
    commission, fake = _commission(
        [
            llm_response(tool_calls=[("c1", "notify_user", {"reason": "needs a human"})]),
            llm_response(
                tool_calls=[
                    (
                        "c2",
                        "conclude",
                        {
                            "route": "notify",
                            "rationale": "urgent, needs attention",
                            "notification_sent": True,
                        },
                    )
                ]
            ),
        ]
    )

    result = await run_one(commission, _input())

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.route == "notify"
    assert result.output.notification_sent is True
    assert result.output.draft_text is None
    assert len(fake.calls) == 2


async def test_email_handler_cancelled_before_loop_makes_no_call() -> None:
    commission, fake = _commission([llm_response(tool_calls=None)])

    result = await run_one(commission, _input(), cancel=AlwaysCancelled())

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert len(fake.calls) == 0
