"""EmailHandler: a route-and-execute LLM-loop Commission (provisional validator).

An incoming email is classified into one of three routes and the route is
executed in the same loop: file as `non_urgent` (conclude directly), `draft` a
templated reply (dispatch the DraftReply tool), or simulate notifying the user
(dispatch the NotifyUser tool). A basic Commission: it rides the default
`_run` over two deterministic stub tools.

This exists to stress the LLM-loop-routing surface of the contract before the
contract is frozen. The handlers are deliberately honest stubs: DraftReply
renders a fixed template at zero cost, and NotifyUser reports a simulation
without claiming an external side effect. It is not part of any supported
surface; it is a consumer that exercises:

  1. deterministic Tools dispatched from an LLM-driven parent;
  2. heterogeneous route outputs flattened into one typed `OutputT`;
  3. a child's typed output round-tripping through the LLM into `conclude`;
  4. the absence of any structural bind between which handler was dispatched
     and the route the conclude output claims.
"""

from datetime import UTC, datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from vibrantine import (
    CallContext,
    Commission,
    CommissionResult,
    CostMetrics,
    Provenance,
)

_SYSTEM_PROMPT = (
    "You handle one incoming email. Decide exactly one route:\n"
    "- `non_urgent`: no action needed now. Call `conclude` directly with "
    "route='non_urgent'.\n"
    "- `draft`: a reply is warranted. Call `draft_reply` with the email, then "
    "call `conclude` with route='draft' and `draft_text` copied from the "
    "draft_reply result.\n"
    "- `notify`: the user must see this. Call `notify_user` with a short "
    "reason, then call `conclude` with route='notify' and "
    "notification_simulated=true.\n"
    "Call at most one handler. Produce output only through `conclude`, and "
    "always include a one-sentence `rationale` for the route you chose."
)


class IncomingEmail(BaseModel):
    """The email being handled."""

    sender: str = Field(description="From address of the email.")
    subject: str = Field(description="Subject line of the email.")
    body: str = Field(description="Plain-text body of the email.")
    thread_id: str | None = Field(
        default=None,
        description="Identifier of the thread this email belongs to, if any.",
    )


class EmailHandlerInput(BaseModel):
    """Inputs for one triage call."""

    email: IncomingEmail = Field(description="The incoming email to triage.")


type Route = Literal["non_urgent", "draft", "notify"]


class EmailHandlerOutput(BaseModel):
    """The triage decision plus whatever the executed route produced.

    Heterogeneous by route: `draft_text` is populated only on the draft route,
    `notification_simulated` only on the notify route. The optional-field
    flattening is the shape the one-typed-output contract forces on a router.
    """

    route: Route = Field(description="The route chosen for this email.")
    rationale: str = Field(description="Why this route was chosen.")
    draft_text: str | None = Field(
        default=None,
        description="The drafted reply; populated iff route == 'draft'.",
    )
    notification_simulated: bool = Field(
        default=False,
        description="Whether the notify route's stub simulation ran; true iff route == 'notify'.",
    )


# --- Stub handler 1: a deterministic reply-template Tool -----------------------


class DraftReplyInput(BaseModel):
    """Inputs for one draft-reply call."""

    email: IncomingEmail = Field(description="The email to draft a reply to.")


class DraftReplyOutput(BaseModel):
    """A drafted reply. Drafting only; sending stays with the human."""

    draft_text: str = Field(description="The drafted reply text.")


class DraftReplyTool(Commission[DraftReplyInput, DraftReplyOutput]):
    """Render a deterministic reply template for one email."""

    name: ClassVar[str] = "draft_reply"
    description: ClassVar[str] = (
        "Render a short reply template for an email. Drafting only; it never sends.\n"
        "\n"
        "Usage:\n"
        "- Call this with the `email` to reply to when a written response is "
        "warranted; it fills a fixed template for a human to review and send.\n"
        "\n"
        "Returns `draft_text`: the proposed reply."
    )
    input_type: ClassVar[type] = DraftReplyInput
    output_type: ClassVar[type] = DraftReplyOutput
    deterministic: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def _run(
        self,
        input: DraftReplyInput,
        ctx: CallContext,
    ) -> CommissionResult[DraftReplyOutput]:
        provenance = Provenance(
            source="draft_reply:stub",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        )
        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled before drafting began.",
                retryable=False,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=0.0),
            )
        return CommissionResult[DraftReplyOutput](
            status="success",
            output=DraftReplyOutput(
                draft_text=(
                    f'Hi,\n\nThanks for your email about "{input.email.subject}". '
                    f"I'll get back to you shortly.\n\nBest"
                ),
            ),
            provenance=provenance,
            cost=CostMetrics(estimated_usd=0.0),
        )


# --- Stub handler 2: a deterministic notification simulation Tool -------------


class NotifyInput(BaseModel):
    """Inputs for one notify call."""

    reason: str = Field(description="Short reason the user is being notified.")


class NotifyOutput(BaseModel):
    """Result of the notification simulation."""

    simulated: bool = Field(description="True when the notification simulation ran.")


class NotifyUserTool(Commission[NotifyInput, NotifyOutput]):
    """Simulate notifying the user without performing external I/O."""

    name: ClassVar[str] = "notify_user"
    description: ClassVar[str] = (
        "Simulate notifying the user that an email needs personal attention.\n"
        "\n"
        "Usage:\n"
        "- Call this with a short `reason` when an email can't be handled "
        "automatically. This provisional example performs no external I/O.\n"
        "\n"
        "Returns `simulated`: whether the simulation ran."
    )
    input_type: ClassVar[type] = NotifyInput
    output_type: ClassVar[type] = NotifyOutput
    deterministic: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def _run(
        self,
        input: NotifyInput,
        ctx: CallContext,
    ) -> CommissionResult[NotifyOutput]:
        provenance = Provenance(
            source="notify_user:stub",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        )
        if ctx.cancel.is_cancelled:
            return self._fail(
                "cancelled",
                "Cancelled before the notification simulation began.",
                retryable=False,
                provenance=provenance,
                cost=CostMetrics(estimated_usd=0.0),
            )
        return CommissionResult[NotifyOutput](
            status="success",
            output=NotifyOutput(simulated=True),
            provenance=provenance,
            cost=CostMetrics(estimated_usd=0.0),
        )


# --- The triage: a basic Commission routing over two deterministic Tools -------


class EmailHandlerCommission(Commission[EmailHandlerInput, EmailHandlerOutput]):
    """Triage an incoming email by routing it to one of three handlers."""

    name: ClassVar[str] = "email_handler"
    description: ClassVar[str] = (
        "Triage one incoming email into exactly one route: file it as "
        "non-urgent, draft a reply, or notify the user.\n"
        "\n"
        "Usage:\n"
        "- Call this with one `email`; it decides the route and executes it "
        "(drafting via `draft_reply` or alerting via `notify_user`), then "
        "reports what it did.\n"
        "\n"
        "Returns the chosen `route`, the `rationale`, and the route's product: "
        "`draft_text` on the draft route, `notification_simulated` on the "
        "notify route."
    )
    input_type: ClassVar[type] = EmailHandlerInput
    output_type: ClassVar[type] = EmailHandlerOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT

    def __init__(
        self,
        *,
        draft: DraftReplyTool | None = None,
        notify: NotifyUserTool | None = None,
        model: str | None = None,
    ) -> None:
        # model forwarded to the base so the default loop's catalog choice is
        # injectable for DI and tests (the constructor-injection convention;
        # see docs/authoring.md, Step 4: The Toolbox).
        super().__init__(
            toolbox=(draft or DraftReplyTool(), notify or NotifyUserTool()),
            model=model,
        )

    def build_user_message(self, input: EmailHandlerInput, ctx: CallContext) -> str:
        e = input.email
        return f"From: {e.sender}\nSubject: {e.subject}\n\n{e.body}"
