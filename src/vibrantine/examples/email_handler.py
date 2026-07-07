"""EmailHandler: a route-and-execute LLM-loop Commission (provisional validator).

An incoming email is classified into one of three routes and the route is
executed in the same loop: file as `non_urgent` (conclude directly), `draft` a
reply (dispatch the DraftReply sub-Commission), or `notify` the user (dispatch
the NotifyUser tool). A basic Commission: it rides the default `invoke`, with
its toolbox holding a *sub-Commission* and a *tool* rather than leaf tools only.

This exists to stress the LLM-loop-routing surface of the contract before the
contract is frozen. The handlers are stubs: DraftReply returns canned text but
reports a deliberately non-zero cost (so the loop's cost-rollup behavior is
observable without spending), and NotifyUser records instead of notifying. It
is not part of any supported surface; it is a consumer that exercises:

  1. a Commission used as an LLM tool (DraftReply), dispatched mid-loop;
  2. heterogeneous route outputs flattened into one typed `OutputT`;
  3. a child's typed output round-tripping through the LLM into `conclude`;
  4. the absence of any structural bind between which handler was dispatched
     (the real side effect) and the route the conclude output claims.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, Field

from vibrantine.contract import (
    CallContext,
    Commission,
    CommissionResult,
    CostMetrics,
    Provenance,
)
from vibrantine.models import Model

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_SYSTEM_PROMPT = (
    "You handle one incoming email. Decide exactly one route:\n"
    "- `non_urgent`: no action needed now. Call `conclude` directly with "
    "route='non_urgent'.\n"
    "- `draft`: a reply is warranted. Call `draft_reply` with the email, then "
    "call `conclude` with route='draft' and `draft_text` copied from the "
    "draft_reply result.\n"
    "- `notify`: the user must see this. Call `notify_user` with a short "
    "reason, then call `conclude` with route='notify' and notification_sent=true.\n"
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
    `notification_sent` only on the notify route. The optional-field flattening
    is the shape the one-typed-output contract forces on a router.
    """

    route: Route = Field(description="The route chosen for this email.")
    rationale: str = Field(description="Why this route was chosen.")
    draft_text: str | None = Field(
        default=None,
        description="The drafted reply; populated iff route == 'draft'.",
    )
    notification_sent: bool = Field(
        default=False,
        description="Whether a notification was dispatched; true iff route == 'notify'.",
    )


# --- Stub handler 1: a sub-Commission (LLM judgment), stubbed body ------------


class DraftReplyInput(BaseModel):
    """Inputs for one draft-reply call."""

    email: IncomingEmail = Field(description="The email to draft a reply to.")


class DraftReplyOutput(BaseModel):
    """A drafted reply. Drafting only; sending stays with the human."""

    draft_text: str = Field(description="The drafted reply text.")


class DraftReplyCommission(Commission[DraftReplyInput, DraftReplyOutput]):
    """Draft a reply to an email (stub: canned text, non-zero cost probe)."""

    name: ClassVar[str] = "draft_reply"
    description: ClassVar[str] = (
        "Draft a reply to an email. Drafting only; it never sends.\n"
        "\n"
        "Usage:\n"
        "- Call this with the `email` to reply to when a written response is "
        "warranted; it returns proposed text for a human to review and send.\n"
        "\n"
        "Returns `draft_text`: the proposed reply."
    )
    input_type: ClassVar[type] = DraftReplyInput
    output_type: ClassVar[type] = DraftReplyOutput

    async def invoke(
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
            # Deliberately non-zero so the loop's cost-rollup behavior is
            # observable: run_llm_loop folds this into children_cost on dispatch.
            cost=CostMetrics(estimated_usd=0.0023),
        )


# --- Stub handler 2: a leaf tool (side effect), stubbed -----------------------


class NotifyInput(BaseModel):
    """Inputs for one notify call."""

    reason: str = Field(description="Short reason the user is being notified.")


class NotifyOutput(BaseModel):
    """Result of a notify dispatch."""

    delivered: bool = Field(description="True if the notification was delivered.")


class NotifyUserTool(Commission[NotifyInput, NotifyOutput]):
    """Notify the user about an email (stub: records instead of sending)."""

    name: ClassVar[str] = "notify_user"
    description: ClassVar[str] = (
        "Notify the user that an email needs their personal attention.\n"
        "\n"
        "Usage:\n"
        "- Call this with a short `reason` when an email can't be handled "
        "automatically and the user must see it. Use it sparingly: for "
        "genuinely urgent or judgment-needing mail.\n"
        "\n"
        "Returns `delivered`: whether the notification went out."
    )
    input_type: ClassVar[type] = NotifyInput
    output_type: ClassVar[type] = NotifyOutput

    def __init__(self) -> None:
        super().__init__(max_input_tokens=None)

    async def invoke(
        self,
        input: NotifyInput,
        ctx: CallContext,
    ) -> CommissionResult[NotifyOutput]:
        provenance = Provenance(
            source="notify_user:stub",
            fetched_at=datetime.now(UTC),
            confidence="grounded",
        )
        return CommissionResult[NotifyOutput](
            status="success",
            output=NotifyOutput(delivered=True),
            provenance=provenance,
            cost=CostMetrics(estimated_usd=0.0),
        )


# --- The triage: a basic Commission routing over a sub-Commission + a tool ----


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
        "`draft_text` on the draft route, `notification_sent` on the notify "
        "route."
    )
    input_type: ClassVar[type] = EmailHandlerInput
    output_type: ClassVar[type] = EmailHandlerOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT

    def __init__(
        self,
        *,
        draft: DraftReplyCommission | None = None,
        notify: NotifyUserTool | None = None,
        client: "AsyncOpenAI | None" = None,
        model: str | Model | None = None,
    ) -> None:
        # client/model forwarded to the base so the default loop is injectable
        # for DI and tests (the constructor-injection convention; see
        # docs/authoring.md, Step 4: The Toolbox).
        super().__init__(
            toolbox=(draft or DraftReplyCommission(), notify or NotifyUserTool()),
            client=client,
            model=model,
        )

    def build_user_message(self, input: EmailHandlerInput, ctx: CallContext) -> str:
        e = input.email
        return f"From: {e.sender}\nSubject: {e.subject}\n\n{e.body}"
