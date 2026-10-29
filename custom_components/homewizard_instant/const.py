"""Constants for the Homewizard integration."""

from __future__ import annotations

import logging
from datetime import timedelta

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

# Mark entities unavailable when the meter stops sending measurements while the
# socket itself stays up (for example an unplugged P1 cable). DSMR 2-4 meters
# report every 10 s, DSMR 5 every second.
STALE_AFTER = timedelta(seconds=60)
