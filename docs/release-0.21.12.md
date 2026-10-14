# Circadian Pro 0.21.12

## Changes

- Both daytime and nighttime brightness-transition durations are entered in minutes in the settings panel and the native options flow.
- Existing timings convert automatically without rounding their duration. Fractional minutes remain editable, including values originally set to one second.
- The two inputs stay together in the brightness settings group, side by side on wide panels and stacked on narrow screens.
- Stored settings, YAML and service values retain their existing seconds-based format. Brightness curves and calibration behavior remain unchanged.

## Validation

Source and translation checks, Ruff, 242 Python tests and 105 frontend tests. Regression coverage includes conversion, fractional timings, invalid input and options-flow navigation. Home Assistant deployment follows the backup and exact-package checks in [the release procedure](releasing.md).
