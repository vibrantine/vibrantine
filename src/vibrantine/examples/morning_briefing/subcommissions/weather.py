"""Weather Commission: report today's weather for a morning briefing.

A basic LLM-loop Commission and the smallest leaf in the MorningBriefing
tree: one configured source URL, one fetch, thin judgment to phrase the
result. The source is capacity, fixed at construction like a subscription;
the input carries only the briefing date, so each run is the standing work
order "report today's weather".
"""

from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibrantine.contract import DEFAULT_MAX_ITERATIONS, CallContext, Commission
from vibrantine.models import Model
from vibrantine.tools.fetch import FetchTool

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_SYSTEM_PROMPT = (
    "You report the weather for a morning briefing. The user message gives "
    "you today's date and one weather source URL. Fetch the source with the "
    "`fetch` tool, extract the current conditions and the outlook for the "
    "day, and phrase them in one or two plain sentences a reader can scan "
    "over coffee. Report only what the source supports; if the source does "
    "not load or contains no usable weather data, say so plainly in the "
    "report instead of guessing. When you have the report, call `conclude` "
    "with a single `report` field. Do not produce free-form text outside of "
    "tool calls."
)


class WeatherInput(BaseModel):
    """Inputs for one weather report."""

    briefing_date: str = Field(
        description="Human-readable date of the briefing, e.g. 'Sunday 05 July 2026'.",
    )


class WeatherOutput(BaseModel):
    """Result of one weather report."""

    report: str = Field(
        description="One or two plain sentences of current conditions and outlook.",
    )


class WeatherCommission(Commission[WeatherInput, WeatherOutput]):
    """Report today's weather from a configured source via an LLM loop."""

    name: ClassVar[str] = "weather"
    description: ClassVar[str] = (
        "Report today's weather in one or two plain sentences, grounded in a "
        "configured source.\n"
        "\n"
        "Usage:\n"
        "- Call this with the `briefing_date`; the instance fetches its "
        "configured source URL and phrases the day's conditions.\n"
        "- The source is fixed at construction; for a different place or "
        "provider, construct another instance.\n"
        "\n"
        "Returns a `report`: one or two sentences of conditions and outlook."
    )
    input_type: ClassVar[type] = WeatherInput
    output_type: ClassVar[type] = WeatherOutput
    system_prompt: ClassVar[str | None] = _SYSTEM_PROMPT

    def __init__(
        self,
        *,
        source_url: str,
        fetch: FetchTool | None = None,
        model: str | Model | None = None,
        client: "AsyncOpenAI | None" = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> None:
        if not source_url.strip():
            raise ValueError("weather requires a source_url at construction.")
        resolved_fetch = fetch or FetchTool()
        super().__init__(
            toolbox=(resolved_fetch,),
            model=model,
            client=client,
            max_iterations=max_iterations,
        )
        self._source_url = source_url

    def build_user_message(self, input: WeatherInput, ctx: CallContext) -> str:
        return f"Date: {input.briefing_date}\nWeather source to fetch: {self._source_url}"
