"""MorningBriefing's package surface: coordinator, section Commissions, and I/O types."""

from vibrantine.examples.morning_briefing.commission import MorningBriefingCommission
from vibrantine.examples.morning_briefing.subcommissions.news_digest import (
    NewsDigestCommission,
    NewsDigestInput,
    NewsDigestOutput,
)
from vibrantine.examples.morning_briefing.subcommissions.weather import (
    WeatherCommission,
    WeatherInput,
    WeatherOutput,
)
from vibrantine.examples.morning_briefing.types import (
    MorningBriefingInput,
    MorningBriefingOutput,
    SectionReport,
)

__all__ = [
    "MorningBriefingCommission",
    "MorningBriefingInput",
    "MorningBriefingOutput",
    "NewsDigestCommission",
    "NewsDigestInput",
    "NewsDigestOutput",
    "SectionReport",
    "WeatherCommission",
    "WeatherInput",
    "WeatherOutput",
]
