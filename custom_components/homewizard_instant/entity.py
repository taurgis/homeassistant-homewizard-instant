"""Base entity for the HomeWizard integration."""

from __future__ import annotations

from typing import Literal

from homeassistant.core import callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homewizard_energy.models import Device

from .const import DOMAIN
from .coordinator import HWEnergyDeviceUpdateCoordinator

type Topic = Literal["measurement", "system"]


def p1_device_info(device: Device) -> DeviceInfo:
    """Describe the meter; sensors and the external-meter parent share it."""
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
    _topic: Topic = "measurement"

    def __init__(self, coordinator: HWEnergyDeviceUpdateCoordinator) -> None:
        """Initialize the HomeWizard entity."""
        super().__init__(coordinator)
        self._attr_device_info = p1_device_info(coordinator.data.device)
        self._reported: tuple[object, bool] | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Report only when this entity's topic brings a new frame.

        Each frame the socket accepts is a new model object, including a
        telegram with unchanged values, so identity tells a new report from a
        republished one. Without this, the system reply every minute would
        refresh ``last_reported`` on every power sensor and make a grid
        controller treat an old measurement as new.
        """
        source = getattr(self.coordinator.data, self._topic)
        available = self.available
        if (
            self._reported is not None
            and self._reported[0] is source
            and self._reported[1] == available
        ):
            return
        self._reported = (source, available)
        super()._handle_coordinator_update()
