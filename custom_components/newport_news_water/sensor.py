"""Sensors for Newport News Waterworks.

Daily history goes into long-term statistics (see coordinator). These sensors show
the latest day and the billing-period summary for dashboards and automations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NNWWConfigEntry, NNWWCoordinator, NNWWData

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class NNWWSensorDescription(SensorEntityDescription):
    """Describes a sensor."""

    value_fn: Callable[[NNWWData], float | date | None]


def _gallons(**kwargs) -> dict:
    return {
        "device_class": SensorDeviceClass.WATER,
        "native_unit_of_measurement": UnitOfVolume.GALLONS,
        "suggested_display_precision": 1,
        **kwargs,
    }


SENSORS: tuple[NNWWSensorDescription, ...] = (
    NNWWSensorDescription(
        key="latest_day_usage",
        translation_key="latest_day_usage",
        value_fn=lambda d: d.latest_gallons,
        **_gallons(),
    ),
    NNWWSensorDescription(
        key="latest_reading_date",
        translation_key="latest_reading_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda d: d.latest_day,
    ),
    NNWWSensorDescription(
        key="period_usage",
        translation_key="period_usage",
        value_fn=lambda d: d.period_gallons.so_far if d.period_gallons else None,
        **_gallons(),
    ),
    NNWWSensorDescription(
        key="period_projected_usage",
        translation_key="period_projected_usage",
        value_fn=lambda d: d.period_gallons.projected if d.period_gallons else None,
        **_gallons(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NNWWConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(NNWWSensor(coordinator, description) for description in SENSORS)


class NNWWSensor(CoordinatorEntity[NNWWCoordinator], SensorEntity):
    """A Newport News Waterworks sensor."""

    _attr_has_entity_name = True
    entity_description: NNWWSensorDescription

    def __init__(self, coordinator: NNWWCoordinator, description: NNWWSensorDescription) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="Newport News Waterworks",
            manufacturer="Newport News Waterworks",
            model="Water meter",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://myservices.nnww.nnva.gov/NNWW/",
        )

    @property
    def native_value(self) -> float | date | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
