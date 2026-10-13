# Circadian Pro 0.21.11

## Changes

- Physical warm-white and cold-white temperatures share one row on wide panels, with warm on the left and cold on the right.
- Each description stays below its own input. Narrow panels stack the fields using the same responsive layout as general settings.
- RGBW's single-white step, channel detection, configured values and validation remain unchanged.

## Validation

Source and translation checks, Ruff, 235 Python tests and 105 frontend tests. Browser review covers the two-column desktop layout and stacked mobile layout. Home Assistant deployment follows the backup and exact-package checks in [the release procedure](releasing.md).
