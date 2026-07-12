"""Domain-specific test builders for the MorningBriefing package's colocated tests.

The generic doubles (the scripted LLM provider, its response builder, and the
scripted catalog entry) come from `vibrantine.testing`; this module holds only
what is specific to briefing tests: an httpx.MockTransport factory for the
real FetchTool, the fixture model's cost constants, and pre-scripted section
factories. Each factory names its Commission's model with a distinct fixture
id and returns `(commission, fake, entry)`: the fake for call assertions, and
the matching `scripted_model` catalog entry for the test to register in
`run_commission(models=[...])`.
"""

import json
from typing import Any

import httpx

from vibrantine.examples.morning_briefing.subcommissions.news_digest import (
    NewsDigestCommission,
)
from vibrantine.examples.morning_briefing.subcommissions.weather import WeatherCommission
from vibrantine.examples.summarize import SummarizeCommission
from vibrantine.examples.synthesize import SynthesizeCommission
from vibrantine.models import Model
from vibrantine.testing import ScriptedLLM, llm_response, scripted_model
from vibrantine.tools.fetch import FetchTool

# One default LLM turn (100 in, 50 out) at the fixture pricing.
TURN_COST = (100 * 0.50 + 50 * 3.00) / 1_000_000
# One default two-pass Synthesize run (1000+500 in, 200+100 out).
SYNTH_COST = (1500 * 0.50 + 300 * 3.00) / 1_000_000


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
) -> tuple[WeatherCommission, ScriptedLLM, Model]:
    """Weather instance with a scripted conclude, or a scripted loop failure.

    A failure is two consecutive free-text replies: the loop nudges once,
    then fails on the second slip.
    """
    if fails:
        fake = ScriptedLLM(
            [llm_response(content="prose, no tool call"), llm_response(content="still prose")]
        )
    else:
        fake = ScriptedLLM([llm_response(tool_calls=[("w1", "conclude", {"report": report})])])
    weather = WeatherCommission(source_url=source_url, model="fixture/weather")
    return weather, fake, scripted_model(fake, id="fixture/weather")


def make_digest(
    *,
    field: str,
    pages: dict[str, tuple[int, str]],
    claims: list[dict[str, Any]],
    summary: str = "Digest summary.",
    synth_payload: str | None = None,
) -> tuple[NewsDigestCommission, ScriptedLLM, Model]:
    """NewsDigest instance over a mock transport with a scripted Synthesize."""
    payload = synth_payload if synth_payload is not None else structured_payload(claims, summary)
    fake = ScriptedLLM(
        [
            llm_response(content="Free-form synthesis.", in_tokens=1000, out_tokens=200),
            llm_response(content=payload, in_tokens=500, out_tokens=100),
        ]
    )
    # The field label keeps two digests in one run on distinct catalog entries.
    model_id = f"fixture/digest-{field.strip()}"
    digest = NewsDigestCommission(
        field=field,
        sources=list(pages),
        fetch=FetchTool(transport=url_transport(pages)),
        synthesize=SynthesizeCommission(model=model_id),
    )
    return digest, fake, scripted_model(fake, id=model_id)


def make_summarize(
    *,
    summary: str = "The morning in brief.",
    fails: bool = False,
) -> tuple[SummarizeCommission, ScriptedLLM, Model]:
    """Summarize instance with a scripted conclude, or a scripted loop failure."""
    if fails:
        fake = ScriptedLLM(
            [llm_response(content="prose, no tool call"), llm_response(content="still prose")]
        )
    else:
        fake = ScriptedLLM([llm_response(tool_calls=[("s1", "conclude", {"summary": summary})])])
    summarize = SummarizeCommission(model="fixture/summarize")
    return summarize, fake, scripted_model(fake, id="fixture/summarize")
