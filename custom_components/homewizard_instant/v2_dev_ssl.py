"""Dev-only helpers for connecting to a local v2 simulator over TLS.

The real HomeWizard v2 path validates certificates against HomeWizard's CA.
For local simulator development, this module allows explicit opt-in to
insecure TLS for known local hosts only.
"""

from __future__ import annotations

import asyncio
import os
import ssl

from homewizard_energy import HomeWizardEnergyV2

ALLOW_INSECURE_V2_ENV = "HOMEWIZARD_INSTANT_ALLOW_INSECURE_V2"

_ALLOWED_INSECURE_HOSTS = {
    "dummy-p1",
    "localhost",
    "127.0.0.1",
    "::1",
}


def _host_without_port(host: str) -> str:
    """Normalize hostname and strip port if present."""
    candidate = host.strip().lower()

    if candidate.startswith("[") and "]" in candidate:
        return candidate[1 : candidate.index("]")]

    if ":" not in candidate:
        return candidate

    maybe_host, maybe_port = candidate.rsplit(":", 1)
    if maybe_port.isdigit():
        return maybe_host

    return candidate


def _env_enabled(name: str) -> bool:
    """Return True when an environment flag is enabled."""
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on", "y"}


def allow_insecure_v2_for_host(host: str) -> bool:
    """Return whether insecure v2 TLS is explicitly allowed for host."""
    if not _env_enabled(ALLOW_INSECURE_V2_ENV):
        return False

    return _host_without_port(host) in _ALLOWED_INSECURE_HOSTS


class InsecureHomeWizardEnergyV2(HomeWizardEnergyV2):
    """Dev-only HomeWizardEnergyV2 variant with disabled TLS verification."""

    async def _get_ssl_context(self) -> ssl.SSLContext:
        """Build an insecure SSL context for local simulator use only."""

        def _build_context() -> ssl.SSLContext:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context

        return await asyncio.get_running_loop().run_in_executor(None, _build_context)
