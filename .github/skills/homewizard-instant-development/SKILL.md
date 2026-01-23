---
name: homewizard-instant-development
description: Repo-specific guidance for maintaining the 1s polling behavior, sensors, and avoiding conflicts with the official integration
---

# HomeWizard Instant Development

This skill focuses on changes that must preserve the intent of this repository: **1 second polling** for P1 meters, while remaining safe and compatible with Home Assistant.

## When to Use

- You want to tweak polling behavior or performance
- You’re modifying unique IDs / device registry behavior
- You’re making changes that might conflict with the official HomeWizard integration

## Project Intent

- The official integration tends to poll slower; this one is tuned for near real-time.
- The domain is different (`homewizard_instant`) so it can run side-by-side.

## Polling Interval Policy

- Polling is set in `custom_components/homewizard_instant/const.py` (`UPDATE_INTERVAL = 1s`).
- Changing this alters the repository’s core purpose; only do so if explicitly requested.

## Avoiding Conflicts with the Official Integration

- Device registry identifiers are prefixed with `homewizard_instant` in `entity.py`.
- Keep identifiers stable; changing them causes device/entity duplication for users.

## Adding New User-Facing Text

- Add text via `translations/en.json` and keep keys stable.
- Prefer `translation_key` on entity descriptions over hardcoded names.

## Local Dev / Smoke Testing

- Use the VS Code task “Start Home Assistant” to run a dev instance.
- Watch logs for the `homewizard_instant` logger.

## Common “gotchas”

- Adding extra HTTP calls per update loop will overload the device at 1 Hz.
- Creating entities without `has_fn` leads to many entities showing unavailable.
- Raising raw exceptions instead of translation-aware `UpdateFailed` degrades UX.
