"""Run an emulated HomeWizard P1 meter."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger("mock_device")

from .meter import Meter
from .server import MeterServer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emulated HomeWizard P1 meter (API v2)")
    parser.add_argument("--serial", default=os.environ.get("METER_SERIAL", "aabbccddeeff"))
    parser.add_argument("--profile", default=os.environ.get("METER_PROFILE", "3phase"))
    parser.add_argument("--host", default=os.environ.get("BIND_HOST", "0.0.0.0"))
    parser.add_argument("--https-port", type=int, default=int(os.environ.get("HTTPS_PORT", "443")))
    parser.add_argument(
        "--control-port", type=int, default=int(os.environ.get("CONTROL_PORT", "8080"))
    )
    parser.add_argument("--ca-dir", type=Path, default=Path(os.environ.get("CA_DIR", "/certs")))
    parser.add_argument(
        "--work-dir", type=Path, default=Path(os.environ.get("WORK_DIR", "/tmp/p1-meter"))
    )
    parser.add_argument(
        "--push-interval", type=float, default=float(os.environ.get("PUSH_INTERVAL", "1"))
    )
    parser.add_argument(
        "--init-ca",
        action="store_true",
        default=os.environ.get("MOCK_MODE") == "ca",
        help="Write the development CA and exit",
    )
    return parser


async def _serve(args: argparse.Namespace) -> None:
    meter = Meter(
        args.serial,
        args.profile,
        args.push_interval,
        token_path=args.work_dir / f"{args.serial}.tokens.json",
    )
    server = MeterServer(meter, args.ca_dir, args.work_dir)
    await server.start(args.host, args.https_port, args.control_port)
    await asyncio.Event().wait()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parser().parse_args()
    if args.init_ca:
        from .certs import ensure_ca

        cert, _key = ensure_ca(args.ca_dir)
        LOGGER.info("development CA ready at %s", cert)
        return
    asyncio.run(_serve(args))


if __name__ == "__main__":
    main()
