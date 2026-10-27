"""Fixtures for Newport News Waterworks tests."""

from __future__ import annotations

from datetime import date, timedelta

from homeassistant.components.recorder import Recorder
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.newport_news_water.api import DEFAULT_USER_AGENT
from custom_components.newport_news_water.const import CONF_USER_AGENT, DOMAIN

from .portal import FAKE_PASSWORD, FAKE_USERNAME, FakePortal

TODAY = date(2026, 3, 15)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> None:
    """Enable custom integrations; the integration depends on the recorder."""


@pytest.fixture
def history() -> dict[date, float]:
    """Ninety days of made-up daily gallons ending yesterday."""
    return {TODAY - timedelta(days=offset): float(40 + offset % 7 * 10) for offset in range(1, 91)}


@pytest.fixture
def portal(aioclient_mock: AiohttpClientMocker, history: dict[date, float]) -> FakePortal:
    """A fake portal registered on the HTTP mocker."""
    fake = FakePortal(history=history, today=TODAY)
    fake.register(aioclient_mock)
    return fake


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry for the fake account."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=FAKE_USERNAME,
        unique_id=FAKE_USERNAME,
        data={
            CONF_USERNAME: FAKE_USERNAME,
            CONF_PASSWORD: FAKE_PASSWORD,
            CONF_USER_AGENT: DEFAULT_USER_AGENT,
        },
    )
    entry.add_to_hass(hass)
    return entry
