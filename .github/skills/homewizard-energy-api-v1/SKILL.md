---
name: homewizard-energy-api-v1
description: How this integration talks to HomeWizard P1 meters via python-homewizard-energy (API v1) and how to handle errors
---

# HomeWizard Energy API v1 (via python-homewizard-energy)

This repository uses the `python-homewizard-energy` library to communicate with HomeWizard devices using their local API.

## When to Use

- You need to change how device data is fetched
- You’re debugging connectivity/API-disabled scenarios
- You’re adding new data points that might require a different library call

## Key Objects & Calls

- Client: `homewizard_energy.HomeWizardEnergyV1`
- Used calls:
  - `await api.device()` (used during config flow validation)
  - `await api.combined()` (used by the coordinator to fetch data)
  - `await api.close()` (always close the client instance)

## Error Handling Contract

The library raises domain-specific exceptions that must be translated into Home Assistant behavior:

- `homewizard_energy.errors.DisabledError`
  - Meaning: Local API disabled in the HomeWizard app (often shows up as HTTP 403).
  - Behavior:
    - During polling: mark entities unavailable via `UpdateFailed` and trigger reauth flow.
    - During config flow: show a clear error instructing the user to enable the local API.

- `homewizard_energy.errors.RequestError`
  - Meaning: network failure, timeout, malformed response.
  - Behavior:
    - In the coordinator: raise `UpdateFailed` with a translation key.

## Concurrency Guidance

HomeWizard edge devices have limited resources.

- Prefer one request per update interval.
- Avoid parallel requests.
- Keep `PARALLEL_UPDATES = 1` for entities.

## Practical Debugging Steps

- If setup fails: config flow validates with `api.device()`.
- If entities become unavailable:
  - Check if the integration is raising `communication_error` or `api_disabled` (translation keys).
  - Verify the user enabled “Local API” in the HomeWizard app.

## When NOT to Use

- Don’t bypass the library with direct `aiohttp` calls unless the library cannot expose a needed endpoint; the library already encapsulates API quirks and models.
