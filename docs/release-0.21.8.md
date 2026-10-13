# Circadian Pro 0.21.8

## Changes

- Sunrise and sunset offsets are shown and edited in minutes in the Circadian Pro panel and Home Assistant's native configuration form.
- Existing schedules keep their timing automatically: 3600 seconds appears as 60 minutes, and negative values keep their direction. Fractional minutes preserve older values that are not multiples of 60 seconds.
- Saving, backwards navigation and calibration previews convert units at the form boundary. Stored settings, YAML and services retain seconds for compatibility; no profile migration or manual conversion is required.
- Labels use `(min)` and explain that positive values delay the event and negative values bring it forward.

## Validation

Source and translation checks, Ruff, 235 Python tests and 105 frontend tests cover unit conversion, preserved settings, validation and navigation. Browser checks verify the saved offset in desktop and mobile layouts. Home Assistant deployment follows the backup and exact-package checks in [the release procedure](releasing.md).
