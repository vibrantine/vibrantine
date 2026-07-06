"""The demo agent: construction, transcript rendering, and scripted loop runs."""

from pathlib import Path
from typing import cast

from openai import AsyncOpenAI

from vibrantine.contract import CallContext
from vibrantine.dispatch import dispatch
from vibrantine.examples.ask import AskCommission
from vibrantine.examples.demo.agent import (
    ChatInput,
    ChatTurn,
    DemoAgentCommission,
    demo_agent,
)
from vibrantine.examples.demo.tests.fakes import FIXTURE_MODEL, FakeClient, llm_response
from vibrantine.examples.demo.trace import RecordingBackend, persist_tree, render_trace


def test_demo_agent_toolbox_holds_the_four_examples() -> None:
    agent = demo_agent()
    names = [child.name for child in agent.toolbox]
    assert names == ["ask", "synthesize", "morning_briefing", "recursive_research"]


def test_build_user_message_renders_transcript_then_message() -> None:
    agent = DemoAgentCommission(toolbox=())
    chat = ChatInput(
        message="and now?",
        transcript=[
            ChatTurn(role="user", text="hello"),
            ChatTurn(role="assistant", text="hi, what should we run?"),
        ],
    )
    message = agent.build_user_message(chat, CallContext())
    assert message == "User: hello\nYou: hi, what should we run?\nUser: and now?"


async def test_chat_turn_concludes_with_reply() -> None:
    fake = FakeClient([llm_response(tool_calls=[("c1", "conclude", {"reply": "hello there"})])])
    agent = DemoAgentCommission(toolbox=(), model=FIXTURE_MODEL, client=cast(AsyncOpenAI, fake))

    result = await dispatch(agent, ChatInput(message="hi"), CallContext())

    assert result.status == "success"
    assert result.output is not None and result.output.reply == "hello there"


async def test_chat_turn_triggers_an_example_and_is_traced(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("the demo works", encoding="utf-8")

    ask_fake = FakeClient(
        [llm_response(tool_calls=[("a1", "conclude", {"answer": "It says the demo works."})])]
    )
    ask = AskCommission(model=FIXTURE_MODEL, client=cast(AsyncOpenAI, ask_fake))
    agent_fake = FakeClient(
        [
            llm_response(
                tool_calls=[
                    ("t1", "ask", {"question": "What does it say?", "file_path": str(target)})
                ]
            ),
            llm_response(
                tool_calls=[("t2", "conclude", {"reply": "The file says the demo works."})]
            ),
        ]
    )
    agent = DemoAgentCommission(
        toolbox=(ask,),
        model=FIXTURE_MODEL,
        client=cast(AsyncOpenAI, agent_fake),
    )
    persist_tree(agent)
    backend = RecordingBackend()

    result = await dispatch(
        agent, ChatInput(message="check the file"), CallContext(backend=backend)
    )

    assert result.status == "success"
    assert result.output is not None and "demo works" in result.output.reply
    lines = render_trace(backend.records()).splitlines()
    assert lines[0].startswith("demo_agent  success")
    assert any(line.startswith("  ask  success") for line in lines)
