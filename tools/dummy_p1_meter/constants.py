"""Constants for the dummy HomeWizard P1 meter."""

from __future__ import annotations

from pathlib import Path

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 15510
DEFAULT_TIMEZONE = "Europe/Amsterdam"
DEFAULT_LATITUDE = 52.3676
DEFAULT_PV_PEAK_W = 4200.0
DEFAULT_SERIAL = "P1SIM000001"
DEFAULT_SEED = 424242
DEFAULT_TLS_CN = "dummy-p1"
DEFAULT_CERT_FILE = Path("/tmp/homewizard-dummy-p1-cert.pem")
DEFAULT_KEY_FILE = Path("/tmp/homewizard-dummy-p1-key.pem")
