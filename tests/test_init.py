"""Tests for setup, statistics import and sensors."""

from __future__ import annotations

from datetime import date, timedelta
from functools import partial

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import Recorder
from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.newport_news_water.const import PORTAL_TZ
from custom_components.newport_news_water.coordinator import day_start, statistic_ids
from custom_components.newport_news_water.cost import GALLONS_PER_HCF, bill_month_for

from .conftest import TODAY
from .portal import FAKE_METER, FakePortal, bills

CONSUMPTION_ID, COST_ID = statistic_ids(FAKE_METER)


@pytest.fixture(autouse=True)
def frozen_today(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Pin 'now' to mid-day on TODAY in Newport News."""
    freezer.move_to("2026-03-15 16:00:00+00:00")
    return freezer


@pytest.fixture(autouse=True)
def us_units(hass: HomeAssistant) -> None:
    """Show sensors in gallons, as a US install would."""
    hass.config.units = US_CUSTOMARY_SYSTEM


async def _stats(hass: HomeAssistant) -> dict:
    await async_wait_recording_done(hass)
    return await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        day_start(date(2020, 1, 1)),
        None,
        {CONSUMPTION_ID, COST_ID},
        "hour",
        None,
        {"state", "sum"},
    )


async def test_setup_backfills_statistics_and_sensors(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    portal: FakePortal,
    config_entry: MockConfigEntry,
    history: dict[date, float],
) -> None:
    """First refresh backfills all history into statistics and fills sensors."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    stats = await _stats(hass)
    consumption = stats[CONSUMPTION_ID]
    assert len(consumption) == len(history)
    first_day = min(history)
    assert consumption[0]["start"] == day_start(first_day).timestamp()
    assert consumption[-1]["start"] == day_start(TODAY - timedelta(days=1)).timestamp()
    assert consumption[-1]["sum"] == pytest.approx(sum(history.values()))
    # Today's 0 placeholder is never imported.
    assert all(row["start"] < day_start(TODAY).timestamp() for row in consumption)

    # Cost is estimated from the bills: each billed period's days add up to its bill.
    fake_bills = bills(history)
    daily_cost = {
        dt_util.utc_from_timestamp(row["start"]).astimezone(PORTAL_TZ).date(): row["state"]
        for row in stats[COST_ID]
    }
    assert set(daily_cost) == set(history)
    for month, (_, dollars) in fake_bills.items():
        period = [v for d, v in daily_cost.items() if bill_month_for(d) == month]
        assert sum(period) == pytest.approx(dollars, abs=0.02 * len(period) + 0.05)
    # Days after the last bill are priced at the last bill's rate.
    last_month = max(fake_bills)
    last_hcf, last_dollars = fake_bills[last_month]
    yesterday = TODAY - timedelta(days=1)
    assert bill_month_for(yesterday) not in fake_bills
    assert daily_cost[yesterday] == pytest.approx(
        history[yesterday] / GALLONS_PER_HCF * last_dollars / last_hcf, abs=0.01
    )
    assert stats[COST_ID][-1]["sum"] == pytest.approx(sum(daily_cost.values()))

    metadata = await hass.async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={CONSUMPTION_ID, COST_ID})
    )
    assert metadata[CONSUMPTION_ID][1]["unit_of_measurement"] == "gal"
    assert metadata[COST_ID][1]["unit_of_measurement"] is None

    def state(key: str) -> str:
        return hass.states.get(f"sensor.newport_news_waterworks_{key}").state

    assert float(state("latest_day_usage")) == history[yesterday]
    assert float(state("latest_day_estimated_cost")) == daily_cost[yesterday]
    assert float(state("last_bill_amount")) == last_dollars
    assert float(state("last_bill_usage")) == pytest.approx(last_hcf * GALLONS_PER_HCF, abs=0.1)
    assert float(state("month_to_date_estimated_cost")) == pytest.approx(
        sum(v for d, v in daily_cost.items() if d >= date(2026, 3, 1)), abs=0.01
    )
    assert float(state("projected_month_cost")) == pytest.approx(
        2000 / GALLONS_PER_HCF * last_dollars / last_hcf, abs=0.01
    )
    assert (
        hass.states.get("sensor.newport_news_waterworks_latest_reading_date").state == "2026-03-14"
    )
    assert float(state("month_to_date_usage")) == 1000
    assert float(state("projected_month_usage")) == 2000


async def test_refresh_rewrites_window_and_continues_sum(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    portal: FakePortal,
    config_entry: MockConfigEntry,
    history: dict[date, float],
    frozen_today: FrozenDateTimeFactory,
    aioclient_mock,
) -> None:
    """Later refreshes only fetch the recent window, pick up revisions and new days."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    # A day inside the window gets revised, and a new day arrives.
    revised_day = TODAY - timedelta(days=5)
    history[revised_day] += 100
    history[TODAY] = 77.0
    portal.today = TODAY + timedelta(days=1)
    portal.usage_calls.clear()
    frozen_today.tick(timedelta(days=1))
    coordinator = config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success, coordinator.last_exception

    # Two 31-day pages cover the 62-day refresh window.
    assert len(portal.usage_calls) == 2
    stats = await _stats(hass)
    consumption = stats[CONSUMPTION_ID]
    assert len(consumption) == len(history)
    assert consumption[-1]["start"] == day_start(TODAY).timestamp()
    assert consumption[-1]["sum"] == pytest.approx(sum(history.values()))
    cost_rows = stats[COST_ID]
    assert len(cost_rows) == len(history)
    assert cost_rows[-1]["sum"] == pytest.approx(sum(r["state"] for r in cost_rows))
    by_start = {row["start"]: row for row in consumption}
    assert by_start[day_start(revised_day).timestamp()]["state"] == history[revised_day]


async def test_code_required_starts_reauth(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    portal: FakePortal,
    config_entry: MockConfigEntry,
) -> None:
    """If the portal stops trusting the device, setup fails into reauth."""
    portal.needs_code = True
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]
    assert portal.codes_sent == 0  # nothing is emailed until the user opens reauth


async def test_portal_down_retries(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    aioclient_mock,
    config_entry: MockConfigEntry,
) -> None:
    """Connection problems retry setup instead of asking for reauth."""
    aioclient_mock.get("https://myservices.nnww.nnva.gov/NNWW/", status=503)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_refresh_fills_long_gap(
    hass: HomeAssistant,
    portal: FakePortal,
    config_entry: MockConfigEntry,
    history: dict[date, float],
    frozen_today: FrozenDateTimeFactory,
) -> None:
    """After a long outage the refresh pages back far enough to close the gap."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    new_today = TODAY + timedelta(days=60)
    for offset in range(60):
        history[TODAY + timedelta(days=offset)] = 30.0
    portal.today = new_today
    frozen_today.tick(timedelta(days=60))
    coordinator = config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success, coordinator.last_exception

    stats = await _stats(hass)
    consumption = stats[CONSUMPTION_ID]
    assert len(consumption) == len(history) == 150
    assert consumption[-1]["sum"] == pytest.approx(sum(history.values()))
    cost_rows = stats[COST_ID]
    assert len(cost_rows) == len(history)
    assert cost_rows[-1]["sum"] == pytest.approx(sum(r["state"] for r in cost_rows))
