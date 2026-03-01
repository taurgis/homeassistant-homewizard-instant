"""TLS helpers for the dummy P1 meter."""

from __future__ import annotations

from pathlib import Path
import subprocess


def ensure_self_signed_cert(cert_file: Path, key_file: Path, common_name: str) -> None:
    """Create a self-signed TLS certificate if it does not exist."""
    if cert_file.exists() and key_file.exists():
        return

    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.parent.mkdir(parents=True, exist_ok=True)

    san = f"subjectAltName=DNS:{common_name},DNS:localhost,IP:127.0.0.1"

    command = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-days",
        "3650",
        "-nodes",
        "-subj",
        f"/CN={common_name}",
        "-addext",
        san,
        "-keyout",
        str(key_file),
        "-out",
        str(cert_file),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            "Unable to generate TLS certificate with openssl: "
            f"{result.stderr.strip()}"
        )
