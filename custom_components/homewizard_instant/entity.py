"""Base entity for the HomeWizard integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homewizard_energy.models import Device

from .const import DOMAIN
from .coordinator import HWEnergyDeviceUpdateCoordinator


def p1_device_info(device: Device) -> DeviceInfo:
    """Describe the P1 meter; sensors and the external-meter parent share it."""
    info = DeviceInfo(
        manufacturer="HomeWizard",
        sw_version=device.firmware_version,
        model_id=device.product_type,
        model=f"{device.model_name or device.product_type} (Instant)",
        serial_number=device.serial,
    )
    if (serial_number := device.serial) is not None:
        # Use DOMAIN-prefixed identifier to avoid conflicts with official HomeWizard integration
        info["identifiers"] = {(DOMAIN, f"{DOMAIN}_{serial_number}")}
        # Register a MAC-style connection so DHCP registered_devices discovery can map this device.
        info["connections"] = {(CONNECTION_NETWORK_MAC, serial_number)}
    return info


class HomeWizardEntity(CoordinatorEntity[HWEnergyDeviceUpdateCoordinator]):
    """Defines a HomeWizard entity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HWEnergyDeviceUpdateCoordinator) -> None:
        """Initialize the HomeWizard entity."""
        super().__init__(coordinator)
        self._attr_device_info = p1_device_info(coordinator.data.device)
