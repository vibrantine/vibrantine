"""Shared test doubles for the demo package's colocated tests.

Follows the MorningBriefing package's pattern: a fake AsyncOpenAI-shaped
client with a scripted response queue. Colocated tests self-contain their
fakes; the top-level tests/conftest.py does not apply under src/.
"""

import json
from types import SimpleNamespace
from typing import Any

# Stable fixture for pricing math: $0.50/M input, $3.00/M output.
FIXTURE_MODEL = "google/gemini-3-flash-preview"


def llm_response(
    *,
    tool_calls: list[tuple[str, str, dict[str, Any]]] | None = None,
    content: str | None = None,
    in_tokens: int = 100,
    out_tokens: int = 50,
) -> SimpleNamespace:
    """Fake chat.completions response shaped for the LLM loop."""
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
