"""Development CA and device certificates for the emulated P1 meter.

The vendor HomeWizard CA private key is not public. These certificates follow
the same shape the real meter uses: a private CA and a leaf whose common name
and DNS SAN are ``appliance/p1dongle/<serial>``.
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
subjectAltName = DNS:{hostname}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
"""


def _openssl(args: list[str]) -> None:
    completed = subprocess.run(args, check=False, capture_output=True)
    if completed.returncode != 0:
        detail = completed.stderr.decode() or completed.stdout.decode()
        raise RuntimeError(detail.strip())


def hostname(serial: str) -> str:
    """Return the TLS identity HomeWizard uses for a P1 meter."""
    return f"appliance/p1dongle/{serial}"


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
) -> tuple[Path, Path]:
    """Sign a leaf certificate for this meter's appliance hostname."""
    directory.mkdir(parents=True, exist_ok=True)
    identity = hostname(serial)
    leaf_config = directory / f"{name}.cnf"
    ext_config = directory / f"{name}.ext"
    csr = directory / f"{name}.csr"
    cert = directory / f"{name}.pem"
    key = directory / f"{name}.key"
    leaf_config.write_text(_LEAF_CONFIG.format(hostname=identity), encoding="utf-8")
    ext_config.write_text(_LEAF_EXT.format(hostname=identity), encoding="utf-8")
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
