---
name: dummy-p1-development
description: Develop and validate the dummy HomeWizard P1 simulator with realistic hourly and seasonal Belgian household load behavior
---

# Dummy P1 Development

Use this skill when changing the dummy P1 meter simulator in `tools/dummy_p1_meter/`.

## When to Use

- You are updating load, solar, or randomness behavior in `simulation.py`.
- You need realistic hourly and seasonal behavior for local dev/testing.
- You are adding simulator endpoints or debug fields in `api.py`.
- You are adjusting CLI options in `cli.py`.

## When Not to Use

- Changes only in `custom_components/homewizard_instant/` integration runtime.
- Pure translation or documentation edits unrelated to the simulator.

## Workflow

1. Confirm realism baseline from official/authoritative references.
2. Keep simulator behavior deterministic for a given seed.
3. Keep API compatibility for existing test flows (`/api/v1/data`, `/api`, `/api/measurement`, `/api/ws`).
4. Add or update tests in `tests/test_dummy_p1_meter.py` for behavior changes.
5. Run verification:

```bash
pytest tests/ -q
python3 -m mypy --strict custom_components/homewizard_instant
```

## Profile Baseline

- Annual household target range: roughly `2,662` to `3,500` kWh/year.
- Default simulator reference target: around `3,055` kWh/year.
- Hourly shape: low overnight, stronger morning/late-day demand, softer weekend profile.
- Seasonal shape: winter uplift and summer reduction, interpolated by day-of-year.

See: [Belgian profile references](references/PROFILE_BASELINES.md)

## Design Rules

- Prefer profile factors over hard-coded one-off peaks.
- Interpolate across hour and month boundaries to avoid abrupt jumps.
- Keep short appliance spikes additive and stochastic.
- Avoid unrealistic sustained spikes that dominate the base profile.
- Keep debug state transparent (`/sim/state`) for rapid auditing.

## Quick Commands

```bash
# Run simulator
python3 .devcontainer/dummy_p1_meter.py --host 0.0.0.0 --port 15510 --seed 424242

# Run simulator with custom annual demand and no PV
python3 .devcontainer/dummy_p1_meter.py --household-yearly-kwh 3500 --pv-peak-w 0

# Test simulator behavior
pytest tests/test_dummy_p1_meter.py -q
```

## References

- Detailed assumptions and links: `references/PROFILE_BASELINES.md`
- API routes and contract: `tools/dummy_p1_meter/api.py`
- Simulation model: `tools/dummy_p1_meter/simulation.py`
