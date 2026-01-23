"""Constants for the Homewizard integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.const import Platform

DOMAIN = "homewizard_instant"
PLATFORMS = [
    Platform.SENSOR,
]

LOGGER = logging.getLogger(__package__)

# Platform config.
CONF_PRODUCT_NAME = "product_name"
CONF_PRODUCT_TYPE = "product_type"
CONF_SERIAL = "serial"

UPDATE_INTERVAL = timedelta(seconds=1)
