"""Tests for WeatherCommission: the smallest leaf in the MorningBriefing tree."""

import pytest

from vibrantine.contract import CallContext
from vibrantine.examples.morning_briefing.subcommissions.weather import (
    WeatherInput,
)
from vibrantine.examples.morning_briefing.tests.fakes import TURN_COST, make_weather
from vibrantine.orchestrator import run_one


def test_construction_requires_a_source_url() -> None:
    with pytest.raises(ValueError, match="source_url"):
        make_weather(source_url="  ")


def test_toolbox_offers_only_fetch() -> None:
    weather, _, _ = make_weather()
    assert {child.name for child in weather.toolbox} == {"fetch"}


def test_user_message_carries_date_and_configured_source() -> None:
    weather, _, _ = make_weather(source_url="https://weather.test/city")
    message = weather.build_user_message(
        WeatherInput(briefing_date="Monday 06 July 2026"), CallContext()
    )
    assert "Monday 06 July 2026" in message
    assert "https://weather.test/city" in message


async def test_success_returns_report_and_own_cost() -> None:
    weather, _, entry = make_weather(report="Mild and overcast all day.")
    result = await run_one(
        weather, WeatherInput(briefing_date="Monday 06 July 2026"), models=[entry]
    )

    assert result.status == "success"
    assert result.output is not None
    assert result.output.report == "Mild and overcast all day."
    assert abs(result.cost.estimated_usd - TURN_COST) < 1e-12
