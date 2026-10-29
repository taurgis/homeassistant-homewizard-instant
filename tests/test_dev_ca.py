"""Development CA hook used by the Docker lab."""

from __future__ import annotations

import homewizard_energy.v2 as v2
import homewizard_energy.v2.cacert as cacert_mod

from custom_components.homewizard_instant import websocket as ws

_PEM = "-----BEGIN CERTIFICATE-----\nTESTDATA\n-----END CERTIFICATE-----"


def test_development_ca_is_optional_and_appended_once(tmp_path, monkeypatch) -> None:
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

        good = tmp_path / "ca.pem"
        good.write_text(_PEM, encoding="utf-8")
        monkeypatch.setenv("HOMEWIZARD_DEV_CA", str(good))
        ws.trust_development_ca()
        assert _PEM in cacert_mod.CACERT
        assert v2.CACERT == cacert_mod.CACERT
        assert ws.CACERT == cacert_mod.CACERT
        snapshot = cacert_mod.CACERT
        ws.trust_development_ca()
        assert cacert_mod.CACERT == snapshot
    finally:
        cacert_mod.CACERT = original
        v2.CACERT = original
        ws.CACERT = original
        ws._APPLIED_DEV_CA = applied
