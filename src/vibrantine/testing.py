"""Supported test doubles: script the LLM, keep every other part of the run real.

Every Commission constructor accepts `client=`, and whatever is passed there
is what the LLM machinery calls `chat.completions.create(...)` on. That makes
testing a Commission cheap and deterministic: inject a `ScriptedLLM` whose
"model" is a queue of responses you wrote, and the full machinery (dispatch
wrapping, tool execution, conclude validation, cost and budget math) runs for
real against it. No network, no API key, no spend. The model's intelligence
is never what a contract test proves; the Commission's behavior around the
model's responses is.

This module is supported public surface, the testing half of the `client=`
injection seam:

- `ScriptedLLM(responses)`: the injectable client. Pops one response per LLM
  call, in order, and records every request it received in `calls`.
- `llm_response(...)`: builds one scripted reply, either tool calls or plain
  text, with token counts so pricing runs for real.
- `AlwaysCancelled`: a `CancelToken` that is already cancelled, for testing
  cancellation paths.

The one test worth writing first:

    from vibrantine import CallContext, run_one
    from vibrantine.testing import ScriptedLLM, llm_response

    async def test_concludes() -> None:
        scripted = ScriptedLLM([
            llm_response(tool_calls=[("t1", "conclude", {"answer": "42"})]),
        ])
        commission = MyCommission(client=cast(AsyncOpenAI, scripted))
        result = await run_one(commission, MyInput(question="?"))
        assert result.status == "success"
"""

import json
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any


class AlwaysCancelled:
    """A CancelToken whose `is_cancelled` is always True.

    Pass as `CallContext(cancel=AlwaysCancelled())` to prove a Commission
    checks for cancellation before doing the work.
    """

    @property
    def is_cancelled(self) -> bool:
        return True


def llm_response(
    *,
    tool_calls: list[tuple[str, str, dict[str, Any]]] | None = None,
    content: str | None = None,
    in_tokens: int = 100,
    out_tokens: int = 50,
) -> SimpleNamespace:
    """Build one scripted LLM reply for a `ScriptedLLM` queue.

    `tool_calls` is a list of (id, tool_name, arguments) tuples; use the name
    "conclude" to end an LLM loop with a typed result. `content` is plain
    assistant text (a reply with no tool call, or the body a direct-call
    Commission reads). One builder serves both: the reply carries whichever
    of the two you set (both None models an empty reply). Token counts feed
    the real cost and budget math; override them when a test asserts on spend.
    """
    tcs = None
    if tool_calls is not None:
        tcs = [
            SimpleNamespace(
                id=tc_id,
                type="function",
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for tc_id, name, args in tool_calls
        ]
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=in_tokens, completion_tokens=out_tokens),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tcs))],
    )


class _ScriptedCompletions:
    """The `chat.completions` endpoint of a ScriptedLLM. Internal."""

    def __init__(self, responses: list[SimpleNamespace], calls: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._calls = calls

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self._calls.append(kwargs)
        if not self._responses:
            raise AssertionError(
                f"ScriptedLLM ran out of scripted responses on call "
                f"{len(self._calls)}; the code under test made more LLM "
                f"calls than the script anticipated."
            )
        return self._responses.pop(0)


class ScriptedLLM:
    """An LLM whose turns follow a script: the injectable fake for `client=`.

    Construct with the responses the "model" should give, in order (build
    each with `llm_response`). Each LLM call the Commission makes pops the
    next response off the queue; running past the end fails the test loudly
    rather than hanging or looping. `calls` records the keyword arguments of
    every request received (model, messages, tools), so a test can assert on
    exactly what the Commission sent.

    Inject with `MyCommission(client=cast(AsyncOpenAI, ScriptedLLM([...])))`;
    the cast is honest, as only the `chat.completions.create` surface the
    framework touches is provided.
    """

    def __init__(self, responses: Sequence[SimpleNamespace]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=_ScriptedCompletions(list(responses), self.calls))
