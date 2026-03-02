"""Zeroconf advertisement helpers for the dummy HomeWizard P1 meter."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, ip_address
import logging
import socket
from typing import Any

try:
    from zeroconf import ServiceInfo, Zeroconf
except ModuleNotFoundError:  # pragma: no cover - optional runtime dependency
    ServiceInfo = None
    Zeroconf = None

LOGGER = logging.getLogger(__name__)

SERVICE_TYPES = (
    "_hwenergy._tcp.local.",
    "_homewizard._tcp.local.",
)


def _host_name_for_mdns(raw_host: str) -> str:
    """Return a local domain hostname for mDNS service registration."""
    if raw_host not in {"0.0.0.0", "::"}:
        host = raw_host
    else:
        host = socket.gethostname() or "dummy-p1"

    if host.endswith("."):
        host = host[:-1]

    if "." not in host:
        host = f"{host}.local"

    return f"{host}."


def _resolve_ipv4_addresses(raw_host: str) -> list[IPv4Address]:
    """Resolve one or more IPv4 addresses to advertise over mDNS."""
    if raw_host not in {"0.0.0.0", "::"}:
        try:
            resolved = ip_address(raw_host)
        except ValueError:
            resolved = None

        if isinstance(resolved, IPv4Address):
            return [resolved]

    addresses: list[IPv4Address] = []
    seen: set[str] = set()
    hostname = socket.gethostname()

    try:
        info = socket.getaddrinfo(hostname, None, family=socket.AF_INET)
    except OSError:
        info = []

    for entry in info:
        ip_text = entry[4][0]
        if ip_text in seen or ip_text.startswith("127."):
            continue

        seen.add(ip_text)
        addresses.append(IPv4Address(ip_text))

    if not addresses:
        addresses.append(IPv4Address("127.0.0.1"))

    return addresses


@dataclass
class ZeroconfPublisher:
    """Advertise the dummy P1 meter for Home Assistant zeroconf discovery."""

    host: str
    port: int
    product_name: str
    product_type: str
    serial: str
    service_types: tuple[str, ...] = SERVICE_TYPES
    _zeroconf: Any | None = field(init=False, default=None)
    _infos: list[Any] = field(init=False, default_factory=list)

    def start(self) -> bool:
        """Register zeroconf services.

        Returns True when at least one service was registered.
        """
        if Zeroconf is None or ServiceInfo is None:
            LOGGER.warning(
                "Zeroconf dependency missing; autodiscovery disabled for dummy meter"
            )
            return False

        self._zeroconf = Zeroconf()

        try:
            mdns_host = _host_name_for_mdns(self.host)
            packed_addresses = [addr.packed for addr in _resolve_ipv4_addresses(self.host)]
            properties = {
                "product_name": self.product_name,
                "product_type": self.product_type,
                "serial": self.serial,
            }

            for service_type in self.service_types:
                info = ServiceInfo(
                    type_=service_type,
                    name=f"HomeWizard P1 {self.serial}.{service_type}",
                    port=self.port,
                    addresses=packed_addresses,
                    properties=properties,
                    server=mdns_host,
                )
                self._zeroconf.register_service(info)
                self._infos.append(info)
        except Exception:
            self.stop()
            LOGGER.exception("Failed to register dummy P1 zeroconf services")
            return False

        return bool(self._infos)

    def stop(self) -> None:
        """Unregister zeroconf services and close the client."""
        if self._zeroconf is None:
            return

        for info in self._infos:
            try:
                self._zeroconf.unregister_service(info)
            except Exception:
                LOGGER.debug("Failed to unregister zeroconf service", exc_info=True)

        self._infos.clear()

        try:
            self._zeroconf.close()
        finally:
            self._zeroconf = None