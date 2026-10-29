"""Development CA hook used by the Docker lab."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import homewizard_energy.v2 as v2
import homewizard_energy.v2.cacert as cacert_mod

from custom_components.homewizard_instant import websocket as ws


def _self_signed_pem(directory: Path) -> str:
    if shutil.which("openssl") is None:
        pytest.skip("openssl is required to mint a development CA")
    directory.mkdir(parents=True, exist_ok=True)
    cert = directory / "ca.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(directory / "ca.key"),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=HomeWizard Instant Development CA",
        ],
        check=True,
        capture_output=True,
    )
    return cert.read_text(encoding="utf-8")


def test_development_ca_is_optional_and_appended_once(tmp_path, monkeypatch, caplog) -> None:
    original = cacert_mod.CACERT
    applied = ws._APPLIED_DEV_CA
    try:
        monkeypatch.delenv("HOMEWIZARD_DEV_CA", raising=False)
        ws._APPLIED_DEV_CA = None
        ws.trust_development_ca()
        assert cacert_mod.CACERT == original

        monkeypatch.setenv("HOMEWIZARD_DEV_CA", str(tmp_path / "missing.pem"))
        ws.trust_development_ca()
        assert cacert_mod.CACERT == original

        monkeypatch.setenv("HOMEWIZARD_DEV_CA", str(tmp_path))
        ws.trust_development_ca()
        assert cacert_mod.CACERT == original

        empty = tmp_path / "empty.pem"
        empty.write_text("not a certificate", encoding="utf-8")
        monkeypatch.setenv("HOMEWIZARD_DEV_CA", str(empty))
        ws.trust_development_ca()
        assert cacert_mod.CACERT == original

        forged = tmp_path / "forged.pem"
        forged.write_text(
            "-----BEGIN CERTIFICATE-----\nTESTDATA\n-----END CERTIFICATE-----\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("HOMEWIZARD_DEV_CA", str(forged))
        ws.trust_development_ca()
        assert cacert_mod.CACERT == original

        good = tmp_path / "real"
        pem = _self_signed_pem(good)
        monkeypatch.setenv("HOMEWIZARD_DEV_CA", str(good / "ca.pem"))
        with caplog.at_level("WARNING"):
            ws.trust_development_ca()
        assert pem.strip() in cacert_mod.CACERT
        assert v2.CACERT == cacert_mod.CACERT
        assert ws.CACERT == cacert_mod.CACERT
        assert "development CA" in caplog.text
        assert "TESTDATA" not in cacert_mod.CACERT
        snapshot = cacert_mod.CACERT
        ws.trust_development_ca()
        assert cacert_mod.CACERT == snapshot
    finally:
        cacert_mod.CACERT = original
        v2.CACERT = original
        ws.CACERT = original
        ws._APPLIED_DEV_CA = applied
