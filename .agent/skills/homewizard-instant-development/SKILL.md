---
name: homewizard-instant-development
description: Repo-specific guidance for keeping real-time WebSocket updates, sensors, and side-by-side compatibility with the official integration
---

# HomeWizard Instant Development

This repository's purpose is **real-time P1 meter data**: every measurement the meter pushes over the API v2 WebSocket reaches Home Assistant, without a polling interval, while staying safe next to the official HomeWizard integration.

## When to Use

- You change update, reconnect or staleness behaviour
- You're modifying unique IDs / device registry behavior
- You're making changes that might conflict with the official HomeWizard integration

## Project Intent

- The domain is different (`homewizard_instant`) so it can run side-by-side.
- Pairing uses a separate local user (`homewizard-instant#<instance id>`) so the official integration's token stays valid.

## Update Policy

- Updates are pushed; there is no `update_interval`. The meter decides the rate (1 s for DSMR 5, 10 s for older meters).
- `STALE_AFTER` in `const.py` (60 s) marks entities unavailable when the meter goes silent. Keep it above the slowest meter interval.

## Avoiding Conflicts with the Official Integration

- Device registry identifiers are prefixed with `homewizard_instant` in `entity.py` (`p1_device_info`).
- Keep identifiers stable; changing them causes device/entity duplication for users.

## Adding New User-Facing Text

- Add text to `strings.json` and mirror it to `translations/en.json`; keep keys stable.
- Prefer `translation_key` on entity descriptions over hardcoded names.

## Local Dev / Smoke Testing

- `docs/development.md` describes the Docker lab with emulated API v2 meters and fault injection.
- Watch logs for the `custom_components.homewizard_instant` logger.

## Common "gotchas"

- Extra REST calls next to the WebSocket waste the meter's limited connections.
- Entities are created when `has_fn` first becomes true (at setup or later); don't create them unconditionally.
- Unexpected exceptions in the reader must reconnect, not end the task.
- Raise translation-aware `UpdateFailed` instead of raw exceptions.
