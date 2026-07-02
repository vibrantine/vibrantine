"""Shared test doubles for the LLM-using commissions.

A fake AsyncOpenAI-shaped client plus a response builder and an
always-cancelled token, hoisted here so the ask / synthesize / email_handler /
deep_research / morning_briefing / llm_tools tests share one copy instead of
each re-declaring it. Imported as `from conftest import FakeClient,
llm_response, AlwaysCancelled` — tests/ is on the path via pytest's import
machinery (and basedpyright's `extraPaths`).
"""

import json
from types import SimpleNamespace
from typing import Any


class AlwaysCancelled:
    """A CancelToken whose `is_cancelled` is always True."""

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
    """Fake chat.completions response. `tool_calls` is a list of (id, name, args).

    The message carries both `content` and `tool_calls` (None when unset), so
    one builder serves loop commissions (which read tool_calls) and direct-call
    ones like Synthesize (which read only content).
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


class FakeCompletions:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)
