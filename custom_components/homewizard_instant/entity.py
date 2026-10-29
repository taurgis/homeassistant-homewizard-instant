"""Base entity for the HomeWizard integration."""

from __future__ import annotations

from homeassistant.const import ATTR_CONNECTIONS, ATTR_IDENTIFIERS
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HWEnergyDeviceUpdateCoordinator


def meter_identifier(serial: str) -> tuple[str, str]:
    """Return the device registry identifier for a P1 meter serial."""
    return (DOMAIN, f"{DOMAIN}_{serial}")


def meter_device_info(coordinator: HWEnergyDeviceUpdateCoordinator) -> DeviceInfo:
    """Return device info for the P1 meter itself."""
    device = coordinator.data.device
    info = DeviceInfo(
        manufacturer="HomeWizard",
        sw_version=device.firmware_version,
        model_id=device.product_type,
        model=f"{device.model_name} (Instant)",
        serial_number=device.serial,
    )

    if (serial_number := device.serial) is not None:
        # Use a DOMAIN-prefixed identifier to avoid conflicts with the official integration.
        info[ATTR_IDENTIFIERS] = {meter_identifier(serial_number)}
        # Register a MAC-style connection so DHCP registered_devices discovery can map this device.
        info[ATTR_CONNECTIONS] = {(CONNECTION_NETWORK_MAC, serial_number)}

    return info


@callback
def ensure_meter_device_id(coordinator: HWEnergyDeviceUpdateCoordinator) -> str | None:
    """Return the P1 meter's device id, creating the device when it is missing.

    Child devices must link with ``via_device_id``. That id only exists after the
    parent is in the device registry, so external meters register the parent first.
    """
    serial = coordinator.data.device.serial
    if serial is None:
        return None

    hass = coordinator.hass
    entry_id = coordinator.config_entry.entry_id
    identifier = meter_identifier(serial)
    registry = dr.async_get(hass)
    if registry.async_get_device_by_identifier(identifier, entry_id) is None:
        info = meter_device_info(coordinator)
        registry.async_get_or_create(
            config_entry_id=entry_id,
            identifiers=info[ATTR_IDENTIFIERS],
            connections=info[ATTR_CONNECTIONS],
            manufacturer=info["manufacturer"],
            sw_version=info["sw_version"],
            model_id=info["model_id"],
            model=info["model"],
            serial_number=info["serial_number"],
        )

    return dr.async_get_device_id_by_identifier(
        hass, identifier, config_entry_id=entry_id
    )


class HomeWizardEntity(CoordinatorEntity[HWEnergyDeviceUpdateCoordinator]):
    """Defines a HomeWizard entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HWEnergyDeviceUpdateCoordinator) -> None:
        """Initialize the HomeWizard entity."""
        super().__init__(coordinator)
        self._attr_device_info = meter_device_info(coordinator)
