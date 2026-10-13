# Circadian Pro 0.21.10

## Changes

- The profile-list header now includes **Go to integration** (**Ir a la integración** in Spanish), opening Circadian Pro's native Home Assistant integration page in the same tab.
- From that page, users can access each profile's devices, switches and entity details.
- The shortcut fits alongside the mobile menu and title. During configuration, the existing **Close without saving** action remains available; the shortcut does not interrupt calibration drafts or previews.

## Validation

Source and translation checks, Ruff, 235 Python tests and 105 frontend tests, including the updated header check. Browser review covers desktop and narrow mobile layouts and the native integration destination. Home Assistant deployment follows the backup and exact-package checks in [the release procedure](releasing.md).
