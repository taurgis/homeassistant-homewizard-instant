"""Development CA and device certificates for the emulated P1 meter.

The vendor HomeWizard CA private key is not public. The leaf follows a real
P1 meter: common name ``appliance/p1dongle/<serial>`` and no subjectAltName.
That shape is what the API documentation tells clients to verify, and it is
what a P1 dongle presented in HomeWizard's API discussion 214
(``CN=appliance/p1dongle/<serial>``, empty subjectAltNames).

Some meters instead put only the serial in the common name. Hostname pinning
rejects those certificates. This integration verifies the CA chain and then
the serial from the device, which is what the official Home Assistant
integration does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_CA_CONFIG = """
[req]
distinguished_name = dn
x509_extensions = v3_ca
prompt = no
[dn]
CN = HomeWizard Instant Development CA
[v3_ca]
basicConstraints = critical,CA:true
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
"""

_LEAF_CONFIG = """
[req]
distinguished_name = dn
prompt = no
[dn]
CN = {hostname}
"""

_LEAF_EXT = """
basicConstraints = CA:FALSE
keyUsage = digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
"""


def _openssl(args: list[str]) -> None:
    completed = subprocess.run(args, check=False, capture_output=True)
    if completed.returncode != 0:
        detail = completed.stderr.decode() or completed.stdout.decode()
        raise RuntimeError(detail.strip())


def hostname(serial: str, certificate_type: str = "p1dongle") -> str:
    """Return the documented TLS name; kWh meters use ``energymeter``."""
    return f"appliance/{certificate_type}/{serial}"


def ensure_ca(directory: Path) -> tuple[Path, Path]:
    """Create a development CA if this directory does not have one yet."""
    directory.mkdir(parents=True, exist_ok=True)
    cert = directory / "ca.pem"
    key = directory / "ca.key"
    if cert.exists() and key.exists():
        return cert, key
    config = directory / "ca.cnf"
    config.write_text(_CA_CONFIG, encoding="utf-8")
    _openssl(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "3650",
            "-config",
            str(config),
        ]
    )
    return cert, key


def issue_cert(
    ca_cert: Path,
    ca_key: Path,
    serial: str,
    directory: Path,
    *,
    name: str = "device",
    common_name: str | None = None,
) -> tuple[Path, Path]:
    """Sign a leaf the way a P1 meter does: common name, no subjectAltName.

    ``common_name`` overrides the documented appliance name. Tests use that
    to reproduce meters whose certificate does not verify as
    ``appliance/p1dongle/<serial>``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    identity = common_name or hostname(serial)
    leaf_config = directory / f"{name}.cnf"
    ext_config = directory / f"{name}.ext"
    csr = directory / f"{name}.csr"
    cert = directory / f"{name}.pem"
    key = directory / f"{name}.key"
    leaf_config.write_text(_LEAF_CONFIG.format(hostname=identity), encoding="utf-8")
    ext_config.write_text(_LEAF_EXT, encoding="utf-8")
    _openssl(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(csr),
            "-config",
            str(leaf_config),
        ]
    )
    _openssl(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(ca_cert),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-CAserial",
            str(directory / f"{name}.srl"),
            "-out",
            str(cert),
            "-days",
            "825",
            "-extfile",
            str(ext_config),
        ]
    )
    return cert, key
