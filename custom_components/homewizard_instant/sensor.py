"""Creates HomeWizard sensor entities."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from homewizard_energy.models import ExternalDevice

from homeassistant.components import sensor as sensor_platform
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import ATTR_VIA_DEVICE, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import StateType

if TYPE_CHECKING:
    from homeassistant.helpers.entity_platform import (
        AddEntitiesCallback as AddConfigEntryEntitiesCallback,
    )
else:
    try:
        from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    except ImportError:  # pragma: no cover - fallback for older HA versions
        from homeassistant.helpers.entity_platform import (
            AddEntitiesCallback as AddConfigEntryEntitiesCallback,
        )

from .const import DOMAIN
from .coordinator import HomeWizardConfigEntry, HWEnergyDeviceUpdateCoordinator
from .entity import HomeWizardEntity
from .sensor_descriptions import (
    EXTERNAL_SENSORS,
    SENSORS,
    HomeWizardExternalSensorEntityDescription,
    HomeWizardSensorEntityDescription,
    to_percentage,
    uptime_to_datetime,
)

SENSOR_DEVICE_CLASS_UNITS = cast(
    "dict[SensorDeviceClass, set[str]]",
    getattr(sensor_platform, "DEVICE_CLASS_UNITS"),
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HomeWizardConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Initialize sensors."""

    # Initialize default sensors
    entities: list[SensorEntity] = [
        HomeWizardSensorEntity(entry.runtime_data, description)
        for description in SENSORS
        if description.has_fn(entry.runtime_data.data)
    ]

    # Initialize external devices (gas meters, water meters connected to P1)
    measurement = entry.runtime_data.data.measurement
    if measurement.external_devices is not None:
        for unique_id, device in measurement.external_devices.items():
            if device.type is not None and (
                description := EXTERNAL_SENSORS.get(device.type)
            ):
                # Add external device
                entities.append(
                    HomeWizardExternalSensorEntity(
                        entry.runtime_data, description, unique_id
                    )
                )

    async_add_entities(entities)


class HomeWizardSensorEntity(HomeWizardEntity, SensorEntity):
    """Representation of a HomeWizard Sensor."""

    entity_description: HomeWizardSensorEntityDescription

    def __init__(
        self,
        coordinator: HWEnergyDeviceUpdateCoordinator,
        description: HomeWizardSensorEntityDescription,
    ) -> None:
        """Initialize Sensor Domain."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{description.key}"
        if not description.enabled_fn(self.coordinator.data):
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> StateType | datetime | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return availability of meter."""
        return super().available and self.native_value is not None


class HomeWizardExternalSensorEntity(HomeWizardEntity, SensorEntity):
    """Representation of externally connected HomeWizard Sensor."""

    def __init__(
        self,
        coordinator: HWEnergyDeviceUpdateCoordinator,
        description: HomeWizardExternalSensorEntityDescription,
        device_unique_id: str,
    ) -> None:
        """Initialize Externally connected HomeWizard Sensors."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_unique_id
        self._suggested_device_class = description.suggested_device_class
        # Scope external device IDs to the parent config entry to avoid
        # collisions when multiple P1 meters expose the same external ID.
        parent_unique_id = (
            coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
        )
        scoped_external_id = f"{parent_unique_id}_{device_unique_id}"
        self._attr_unique_id = scoped_external_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, scoped_external_id)},
            name=description.device_name,
            manufacturer="HomeWizard",
            model=coordinator.data.device.product_type,
            serial_number=device_unique_id,
        )
        if coordinator.data.device.serial is not None:
            self._attr_device_info[ATTR_VIA_DEVICE] = (
                DOMAIN,
                f"{DOMAIN}_{coordinator.data.device.serial}",
            )

    @property
    def native_value(self) -> float | int | str | None:
        """Return the sensor value."""
        return self.device.value if self.device is not None else None

    @property
    def device(self) -> ExternalDevice | None:
        """Return ExternalDevice object."""
        return (
            self.coordinator.data.measurement.external_devices.get(self._device_id)
            if self.coordinator.data.measurement.external_devices is not None
            else None
        )

    @property
    def available(self) -> bool:
        """Return availability of meter."""
        return super().available and self.device is not None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return unit of measurement based on device unit."""
        if (device := self.device) is None:
            return None

        # API returns 'm3' but we expect m3.
        if device.unit == "m3":
            return UnitOfVolume.CUBIC_METERS

        return device.unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Validate unit of measurement and set device class."""
        if (
            self.native_unit_of_measurement
            not in SENSOR_DEVICE_CLASS_UNITS[self._suggested_device_class]
        ):
            return None

        return self._suggested_device_class


__all__ = [
    "EXTERNAL_SENSORS",
    "SENSORS",
    "PARALLEL_UPDATES",
    "HomeWizardExternalSensorEntity",
    "HomeWizardExternalSensorEntityDescription",
    "HomeWizardSensorEntity",
    "HomeWizardSensorEntityDescription",
    "async_setup_entry",
    "to_percentage",
    "uptime_to_datetime",
]
