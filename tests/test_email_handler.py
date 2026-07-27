"""Tests for the EmailHandler Commission (provisional route-and-execute validator).

EmailHandler rides the default _run (the LLM loop) over two deterministic
stub Tools. Tests register a ScriptedLLM fake as the run's model catalog
entry (`scripted_model`), so routing, dispatched-tool receipts, and the
heterogeneous-output flattening are exercised end-to-end.
"""

import json
from types import SimpleNamespace

from vibrantine import run_commission
from vibrantine.examples.email_handler import (
    EmailHandlerCommission,
    EmailHandlerInput,
    IncomingEmail,
    NotifyInput,
    NotifyUserTool,
)
from vibrantine.testing import AlwaysCancelled, ScriptedLLM, llm_response, scripted_model

_EMAIL_ARGS = {"sender": "a@b.test", "subject": "Quick question", "body": "Body text."}


def _input() -> EmailHandlerInput:
    return EmailHandlerInput(email=IncomingEmail(**_EMAIL_ARGS))


def _commission(
    responses: list[SimpleNamespace],
) -> tuple[EmailHandlerCommission, ScriptedLLM]:
    fake = ScriptedLLM(responses)
    commission = EmailHandlerCommission()
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

    result = await run_commission(commission, _input(), models=[scripted_model(fake)])

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.route == "non_urgent"
    # Flattening: the route-specific fields stay at their defaults off-route.
    assert result.output.draft_text is None
    assert result.output.notification_simulated is False
    assert len(fake.calls) == 1


async def test_email_handler_draft_route_dispatches_deterministic_tool() -> None:
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
    )

    dispatches: list[dict[str, object]] = []
    result = await run_commission(
        commission,
        _input(),
        models=[scripted_model(fake, input_usd_per_million=None, output_usd_per_million=None)],
        on_dispatch=dispatches.append,
    )

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.route == "draft"
    assert result.output.draft_text == "Hi, thanks for reaching out."
    assert len(fake.calls) == 2
    assert result.cost.estimated_usd == 0.0
    draft_row = next(row for row in dispatches if row["commission_name"] == "draft_reply")
    assert draft_row["deterministic"] is True
    # Token counts cover the two parent turns; the deterministic child has none.
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
                            "notification_simulated": True,
                        },
                    )
                ]
            ),
        ]
    )

    result = await run_commission(commission, _input(), models=[scripted_model(fake)])

    assert result.status == "success", result.error
    assert result.output is not None
    assert result.output.route == "notify"
    assert result.output.notification_simulated is True
    assert result.output.draft_text is None
    assert len(fake.calls) == 2
    # The dispatch really happened: turn two's transcript carries the notify
    # tool's own result. Without this, a broken NotifyUserTool would fail
    # into the transcript and the scripted conclude would still pass above.
    tool_msg = next(m for m in fake.calls[1]["messages"] if m["role"] == "tool")
    assert json.loads(tool_msg["content"]) == {"simulated": True}


async def test_notify_tool_honors_cancellation_and_logs_as_deterministic() -> None:
    dispatches: list[dict[str, object]] = []

    result = await run_commission(
        NotifyUserTool(),
        NotifyInput(reason="needs a human"),
        cancel=AlwaysCancelled(),
        on_dispatch=dispatches.append,
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert dispatches[0]["deterministic"] is True


async def test_email_handler_cancelled_before_loop_makes_no_call() -> None:
    commission, fake = _commission([llm_response(tool_calls=None)])

    result = await run_commission(
        commission, _input(), models=[scripted_model(fake)], cancel=AlwaysCancelled()
    )

    assert result.status == "failure"
    assert result.error is not None
    assert result.error.kind == "cancelled"
    assert len(fake.calls) == 0
