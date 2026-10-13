# Circadian Pro 0.21.9

## Changes

- RGB calibration sliders fade from black at 0 to full red, green or blue at 255.
- White-intensity sliders fade from black to white across their existing 0–100% range.
- Dual-white mixture uses a warm-to-white-to-cool-blue gradient. The configured warm and cold white temperatures remain the slider limits; the colors are a visual guide.
- Channel dots, numeric inputs, keyboard controls and optional previews remain available in desktop and mobile layouts. This visual update does not change calibration values or light control.

## Validation

Source and translation checks, Ruff, 235 Python tests and 105 frontend tests. Browser review covers the calibration controls in desktop and mobile layouts. Home Assistant deployment follows the backup and exact-package checks in [the release procedure](releasing.md).
