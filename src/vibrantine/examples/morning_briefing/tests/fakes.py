"""Shared test doubles for the MorningBriefing package's colocated tests.

Colocated tests self-contain their fakes (the top-level tests/conftest.py
does not apply under src/), following the RecursiveResearch package's
pattern: a fake AsyncOpenAI-shaped client with a scripted response queue,
plus an httpx.MockTransport factory for the real FetchTool.
"""

import json
from types import SimpleNamespace
from typing import Any, cast

import httpx
from openai import AsyncOpenAI

from vibrantine.examples.morning_briefing.subcommissions.news_digest import (
    NewsDigestCommission,
)
from vibrantine.examples.morning_briefing.subcommissions.weather import WeatherCommission
from vibrantine.examples.summarize import SummarizeCommission
from vibrantine.examples.synthesize import SynthesizeCommission
from vibrantine.tools.fetch import FetchTool

# Stable fixture for pricing math: $0.50/M input, $3.00/M output.
FIXTURE_MODEL = "google/gemini-3-flash-preview"

# One default LLM turn (100 in, 50 out) at the fixture model.
TURN_COST = (100 * 0.50 + 50 * 3.00) / 1_000_000
# One default two-pass Synthesize run (1000+500 in, 200+100 out).
SYNTH_COST = (1500 * 0.50 + 300 * 3.00) / 1_000_000


def llm_response(
    *,
    tool_calls: list[tuple[str, str, dict[str, Any]]] | None = None,
    content: str | None = None,
    in_tokens: int = 100,
    out_tokens: int = 50,
) -> SimpleNamespace:
    """Fake chat.completions response, shaped for both the loop and Synthesize."""

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


class AlwaysCancelled:
    @property
    def is_cancelled(self) -> bool:
        return True


def url_transport(responses: dict[str, tuple[int, str]]) -> httpx.MockTransport:
    """Map URL -> (status, body). Unknown URLs raise to surface test bugs loudly."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) not in responses:
            raise AssertionError(f"unexpected URL in test: {request.url}")
        status, body = responses[str(request.url)]
        return httpx.Response(status, text=body, headers={"content-type": "text/plain"})

    return httpx.MockTransport(handler)


def structured_payload(claims: list[dict[str, Any]], summary: str = "Synthesized.") -> str:
    """JSON body Synthesize's structured pass expects from the LLM."""
    return json.dumps({"summary_text": summary, "claims": claims})


def make_weather(
    *,
    report: str = "Cold and clear, light rain after noon.",
    source_url: str = "https://weather.test/today",
    fails: bool = False,
) -> tuple[WeatherCommission, FakeClient]:
    """Weather instance with a scripted conclude, or a scripted loop failure.

    A failure is two consecutive free-text replies: the loop nudges once,
    then fails on the second slip.
    """
    if fails:
        fake = FakeClient(
            [llm_response(content="prose, no tool call"), llm_response(content="still prose")]
        )
    else:
        fake = FakeClient([llm_response(tool_calls=[("w1", "conclude", {"report": report})])])
    weather = WeatherCommission(
        source_url=source_url,
        model=FIXTURE_MODEL,
        client=cast(AsyncOpenAI, fake),
    )
    return weather, fake


def make_digest(
    *,
    field: str,
    pages: dict[str, tuple[int, str]],
    claims: list[dict[str, Any]],
    summary: str = "Digest summary.",
    synth_payload: str | None = None,
) -> tuple[NewsDigestCommission, FakeClient]:
    """NewsDigest instance over a mock transport with a scripted Synthesize."""
    payload = synth_payload if synth_payload is not None else structured_payload(claims, summary)
    fake = FakeClient(
        [
            llm_response(content="Free-form synthesis.", in_tokens=1000, out_tokens=200),
            llm_response(content=payload, in_tokens=500, out_tokens=100),
        ]
    )
    digest = NewsDigestCommission(
        field=field,
        sources=list(pages),
        fetch=FetchTool(transport=url_transport(pages)),
        synthesize=SynthesizeCommission(client=cast(AsyncOpenAI, fake), model=FIXTURE_MODEL),
    )
    return digest, fake


def make_summarize(
    *,
    summary: str = "The morning in brief.",
    fails: bool = False,
) -> tuple[SummarizeCommission, FakeClient]:
    """Summarize instance with a scripted conclude, or a scripted loop failure."""
    if fails:
        fake = FakeClient(
            [llm_response(content="prose, no tool call"), llm_response(content="still prose")]
        )
    else:
        fake = FakeClient([llm_response(tool_calls=[("s1", "conclude", {"summary": summary})])])
    summarize = SummarizeCommission(model=FIXTURE_MODEL, client=cast(AsyncOpenAI, fake))
    return summarize, fake
