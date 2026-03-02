"""CLI entrypoint for the dummy HomeWizard P1 meter server."""

from __future__ import annotations

import argparse
from pathlib import Path
import ssl

from aiohttp import web

from .api import create_app
from .constants import (
    DEFAULT_CERT_FILE,
    DEFAULT_HOST,
    DEFAULT_HOUSEHOLD_YEARLY_KWH,
    DEFAULT_KEY_FILE,
    DEFAULT_LATITUDE,
    DEFAULT_PORT,
    DEFAULT_PV_PEAK_W,
    DEFAULT_SEED,
    DEFAULT_SERIAL,
    DEFAULT_TIMEZONE,
    DEFAULT_TLS_CN,
)
from .discovery import ZeroconfPublisher
from .simulation import P1Simulation
from .tls import ensure_self_signed_cert


def parse_bool(value: str) -> bool:
    """Parse common boolean values."""
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Dummy HomeWizard P1 meter API server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port")
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed for reproducible load/solar profile",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="IANA timezone, e.g. Europe/Amsterdam",
    )
    parser.add_argument(
        "--latitude",
        type=float,
        default=DEFAULT_LATITUDE,
        help="Latitude used for daylight profile",
    )
    parser.add_argument(
        "--pv-peak-w",
        type=float,
        default=DEFAULT_PV_PEAK_W,
        help="Installed PV peak production in watts",
    )
    parser.add_argument(
        "--household-yearly-kwh",
        type=float,
        default=DEFAULT_HOUSEHOLD_YEARLY_KWH,
        help=(
            "Annual household consumption target used for load profile scaling "
            "(default: Belgian average reference)"
        ),
    )
    parser.add_argument(
        "--serial",
        default=DEFAULT_SERIAL,
        help="Device serial returned by /api",
    )
    parser.add_argument(
        "--api-enabled",
        default="true",
        help="Initial API enabled state (true/false)",
    )
    parser.add_argument(
        "--v2-auto-authorize",
        default="true",
        help="Auto-issue v2 token without button press (true/false)",
    )
    parser.add_argument(
        "--cert-file",
        default=str(DEFAULT_CERT_FILE),
        help="Path to TLS certificate PEM file",
    )
    parser.add_argument(
        "--key-file",
        default=str(DEFAULT_KEY_FILE),
        help="Path to TLS private key PEM file",
    )
    parser.add_argument(
        "--tls-common-name",
        default=DEFAULT_TLS_CN,
        help="Common Name used when auto-generating the TLS certificate",
    )
    parser.add_argument(
        "--advertise-zeroconf",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Advertise mDNS/zeroconf services for Home Assistant autodiscovery",
    )
    return parser.parse_args()


def main() -> None:
    """Run simulator HTTPS/WSS server."""
    args = parse_args()

    cert_file = Path(args.cert_file)
    key_file = Path(args.key_file)
    ensure_self_signed_cert(cert_file, key_file, args.tls_common_name)

    simulation = P1Simulation(
        seed=args.seed,
        timezone_name=args.timezone,
        latitude=args.latitude,
        pv_peak_w=args.pv_peak_w,
        household_yearly_kwh=args.household_yearly_kwh,
        serial=args.serial,
        api_enabled=parse_bool(args.api_enabled),
        v2_auto_authorize=parse_bool(args.v2_auto_authorize),
    )
    simulation.start()

    app = create_app(simulation)

    zeroconf_publisher: ZeroconfPublisher | None = None
    if args.advertise_zeroconf:
        device_info = simulation.get_device_v2_payload()
        zeroconf_publisher = ZeroconfPublisher(
            host=args.host,
            port=args.port,
            product_name=str(device_info["product_name"]),
            product_type=str(device_info["product_type"]),
            serial=str(device_info["serial"]),
        )
        if zeroconf_publisher.start():
            print("Autodiscovery: zeroconf advertisement enabled")
        else:
            print("Autodiscovery: zeroconf advertisement unavailable")

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))

    print(
        "Dummy P1 meter listening on "
        f"https://{args.host}:{args.port} "
        f"(seed={args.seed}, timezone={args.timezone}, pv_peak_w={args.pv_peak_w})"
    )
    print("Integration host: dummy-p1:" f"{args.port}")
    print("V2 auth mode: auto-authorize=" f"{simulation.v2_auto_authorize}")

    try:
        web.run_app(
            app,
            host=args.host,
            port=args.port,
            ssl_context=ssl_context,
            access_log=None,
            print=None,
        )
    finally:
        if zeroconf_publisher is not None:
            zeroconf_publisher.stop()
        simulation.stop()
