---
"ha-homewizard-instant-release-tools": patch
---

Relax the python-homewizard-energy requirement from an exact pin (==10.0.1) to a range (>=10.0.1) so the integration no longer conflicts with Home Assistant core's HomeWizard integration, regardless of the exact version core pins (#7).
