"""The demo agent: a chat front door that can trigger the example Commissions.

An LLM-loop Commission whose toolbox holds the other worked examples, so the
demo's chat surface is itself a demonstration: descriptions written for LLM
callers get a real LLM caller, and one chat turn becomes a traced tree of
typed sub-calls. Conversation history is application state; the runner owns
the transcript and passes it in the typed input, so the agent stays a pure
function of what it is handed.
"""

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from vibrantine.contract import CallContext, Commission

_SYSTEM_PROMPT = (
    "You are the interactive front door of the Vibrantine demo. Vibrantine "
    "is a component model for AI agents: each Commission is a typed, "
    "contracted unit of LLM-driven work, and your tools are four worked "
    "example Commissions.\n"
    "\n"
    "- `ask`: answer a question about one file on this machine. Only use "
    "file paths the user has given you; never invent a path.\n"
    "- `synthesize`: merge two or more source texts the user has provided "
    "into a summary with cited claims.\n"
    "- `morning_briefing`: compile a weather-plus-news briefing and write it "
    "to a markdown file. Pass a relative `output_path` such as "
    "'morning-briefing-demo.md' unless the user names one.\n"
    "- `recursive_research`: answer a research question by fetching and "
    "citing web sources, recursively. It takes minutes; call it only when "
    "the user clearly asked for research, otherwise offer it in your reply.\n"
    "\n"
    "Chat naturally. When the user's request matches a tool, call it and "
    "ground your reply in its result, including failures: report a failed or "
    "partial result plainly, it is normal and part of what the demo shows. "
    "When no tool fits, just reply; explain what the demo can do and how the "
    "examples compose. The user message contains the conversation so far, "
    "oldest turn first, ending with the current message. When you have your "
    "reply, call `conclude` with a single `reply` field. Do not produce "
    "free-form text outside of tool calls."
)


class ChatTurn(BaseModel):
    """One prior turn of the conversation, as remembered by the caller."""

    role: Literal["user", "assistant"] = Field(description="Who spoke this turn.")
    text: str = Field(description="What was said.")


class ChatInput(BaseModel):
    """Inputs for one chat turn: the message plus the caller-owned history."""

    message: str = Field(description="The user's current chat message.")
    transcript: list[ChatTurn] = Field(
        default_factory=list,
        description="Prior turns, oldest first. Owned and threaded by the caller.",
    )


class ChatOutput(BaseModel):
    """Result of one chat turn."""

    reply: str = Field(description="The agent's reply, ready to show the user.")


class DemoAgentCommission(Commission[ChatInput, ChatOutput]):
    """Chat about the demo and trigger example Commissions by tool call."""

    name: ClassVar[str] = "demo_agent"
    description: ClassVar[str] = (
        "Hold one turn of demo conversation, triggering example Commissions "
        "when the request calls for one.\n"
        "\n"
        "Usage:\n"
        "- Call this with the user's `message` and the `transcript` so far; "
        "it replies conversationally and may dispatch its example tools.\n"
        "\n"
        "Returns a `reply` ready to show the user."
    )
    input_type: ClassVar[type] = ChatInput
    output_type: ClassVar[type] = ChatOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT

    def build_user_message(self, input: ChatInput, ctx: CallContext) -> str:
        lines: list[str] = []
        for turn in input.transcript:
            speaker = "User" if turn.role == "user" else "You"
            lines.append(f"{speaker}: {turn.text}")
        lines.append(f"User: {input.message}")
        return "\n".join(lines)


def demo_agent(model: str | None = None) -> DemoAgentCommission:
    """Build the agent over demo-configured instances of the four examples."""
    from vibrantine.examples.demo.catalog import (
        make_ask,
        make_morning_briefing,
        make_recursive_research,
        make_synthesize,
    )

    return DemoAgentCommission(
        model=model,
        toolbox=(
            make_ask(model),
            make_synthesize(model),
            make_morning_briefing(model),
            make_recursive_research(model),
        ),
    )
