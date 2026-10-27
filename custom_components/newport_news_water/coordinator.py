"""Coordinator: fetches daily usage and writes it to long-term statistics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging

import aiohttp
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util, slugify
from homeassistant.util.unit_conversion import VolumeConverter

from .api import (
    DEFAULT_USER_AGENT,
    InvalidAuth,
    NNWWClient,
    NNWWError,
    SessionExpired,
    Unit,
    UsageSummary,
    VerificationRequired,
)
from .const import (
    CONF_METER_NUMBER,
    CONF_USER_AGENT,
    DOMAIN,
    MAX_BACKFILL_DAYS,
    PORTAL_TZ,
    REFRESH_WINDOW_DAYS,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type NNWWConfigEntry = ConfigEntry[NNWWCoordinator]


@dataclass(frozen=True, slots=True)
class NNWWData:
    """What the sensors show."""

    meter_number: str
    latest_day: date | None
    latest_gallons: float | None
    period_gallons: UsageSummary | None


def day_start(day: date) -> datetime:
    """Local midnight in Newport News for ``day``, as an aware datetime."""
    return datetime.combine(day, time.min, tzinfo=PORTAL_TZ)


def statistic_ids(meter_number: str) -> tuple[str, str]:
    """Return (consumption, cost) external statistic ids for a meter."""
    base = f"{DOMAIN}:{slugify(meter_number)}"
    return f"{base}_water_consumption", f"{base}_water_cost"


class NNWWCoordinator(DataUpdateCoordinator[NNWWData]):
    """Fetches from the portal every few hours."""

    config_entry: NNWWConfigEntry

    def __init__(self, hass: HomeAssistant, entry: NNWWConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        # Own cookie jar: the portal session must not mix with other integrations.
        session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
        self.client = NNWWClient(
            session,
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            entry.data.get(CONF_USER_AGENT, DEFAULT_USER_AGENT),
        )
        self.meter_number: str = entry.data.get(CONF_METER_NUMBER, "")

    async def _login(self) -> None:
        try:
            await self.client.login()
        except VerificationRequired as err:
            raise ConfigEntryAuthFailed(
                "The portal is asking for an emailed verification code"
            ) from err
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed("Invalid username or password") from err

    async def _async_update_data(self) -> NNWWData:
        try:
            await self._login()
            try:
                return await self._fetch()
            except SessionExpired:
                await self._login()
                return await self._fetch()
        except SessionExpired as err:
            raise UpdateFailed("Portal session expired right after login") from err
        except NNWWError as err:
            raise UpdateFailed(str(err)) from err

    async def _fetch(self) -> NNWWData:
        if not self.meter_number:
            meters = await self.client.get_meters()
            if not meters:
                raise UpdateFailed("No water meter found on the account")
            self.meter_number = meters[0].meter_number

        consumption_id, cost_id = statistic_ids(self.meter_number)
        yesterday = dt_util.now(PORTAL_TZ).date() - timedelta(days=1)

        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, consumption_id, True, {"sum"}
        )
        if last.get(consumption_id):
            last_day = (
                dt_util.utc_from_timestamp(last[consumption_id][0]["start"])
                .astimezone(PORTAL_TZ)
                .date()
            )
            # Re-import the recent window (late/revised reads) and any gap since the
            # last imported day (e.g. Home Assistant was off for a while).
            since = min(
                last_day + timedelta(days=1),
                yesterday - timedelta(days=REFRESH_WINDOW_DAYS - 1),
            )
        else:
            _LOGGER.debug("No statistics yet; backfilling up to %s days", MAX_BACKFILL_DAYS)
            since = yesterday - timedelta(days=MAX_BACKFILL_DAYS)

        # Today is excluded: the portal shows a 0 placeholder until the day is read.
        gallons, gallon_summary = await self.client.get_daily_history(
            Unit.GALLONS, since, yesterday, self.meter_number
        )
        # The portal's daily "dollar" view actually reports cubic feet (gallons / 7.48),
        # so it can't be used for cost. Cost is not imported until there's a reliable
        # source (the monthly dollar view looks correct).
        cost: dict[date, float] = {}

        if gallons:
            await self._import_statistics(consumption_id, cost_id, gallons, cost)

        latest_day = max(gallons) if gallons else None
        return NNWWData(
            meter_number=self.meter_number,
            latest_day=latest_day,
            latest_gallons=gallons.get(latest_day) if latest_day else None,
            period_gallons=gallon_summary,
        )

    async def _import_statistics(
        self,
        consumption_id: str,
        cost_id: str,
        gallons: dict[date, float],
        cost: dict[date, float],
    ) -> None:
        """Rewrite every fetched day, continuing the running sum from before it."""
        first_start = day_start(min(gallons))
        prior = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            first_start - timedelta(days=MAX_BACKFILL_DAYS + 366),
            first_start,
            {consumption_id, cost_id},
            "hour",
            None,
            {"sum"},
        )

        def last_sum(statistic_id: str) -> float:
            rows = prior.get(statistic_id) or []
            return float(rows[-1].get("sum") or 0.0) if rows else 0.0

        meter = self.meter_number
        for statistic_id, values, name, unit_class, unit in (
            (
                consumption_id,
                gallons,
                f"Newport News Water {meter} consumption",
                VolumeConverter.UNIT_CLASS,
                UnitOfVolume.GALLONS,
            ),
            (cost_id, cost, f"Newport News Water {meter} cost", None, None),
        ):
            if not values:
                continue
            running = last_sum(statistic_id)
            stats: list[StatisticData] = []
            for day in sorted(values):
                running += values[day]
                stats.append(StatisticData(start=day_start(day), state=values[day], sum=running))
            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=name,
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_class=unit_class,
                unit_of_measurement=unit,
            )
            _LOGGER.debug("Importing %s days into %s", len(stats), statistic_id)
            async_add_external_statistics(self.hass, metadata, stats)
