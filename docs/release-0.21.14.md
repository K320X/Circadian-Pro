# Circadian Pro 0.21.14

## Changes

- Each brightness-transition duration now has an explanatory diagram beneath it. Solar markers and highlighted ramps show whether the field controls the portion inside or outside daytime.
- Diagrams use illustrative minimum and maximum brightness, independent of the profile values, and explicitly identify themselves as examples. They do not simulate the saved settings or control lights.
- Accessible Spanish and English descriptions, theme colors and responsive sizing keep the diagrams readable on desktop and mobile.
- Duration values, the one-minute form minimum, calibrations and profile behavior remain unchanged.

## Validation

Source and translation checks, Ruff, 245 Python tests and 108 frontend tests. Diagram coverage checks ramp placement, accessible text, control preservation and absence from unrelated fields. Browser review covers desktop and mobile layouts. Home Assistant deployment follows the backup and exact-package checks in [the release procedure](releasing.md).
