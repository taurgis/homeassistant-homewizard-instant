---
"ha-homewizard-instant-release-tools": patch
---

Repair the test and lint pipeline. The pinned pytest versions resolved to a Home Assistant release that predates APIs the integration imports, so the suite could not even be collected; the discovery tests imported `DhcpServiceInfo` and `ZeroconfServiceInfo` from locations current Home Assistant has removed; the CI matrix still listed Python 3.12, which can no longer install Home Assistant; and the integration had reverted to an exact `python-homewizard-energy` pin that conflicts with Home Assistant core's own HomeWizard integration.
