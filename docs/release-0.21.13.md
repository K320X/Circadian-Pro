# Circadian Pro 0.21.13

## Changes

- Brightness-transition fields identify their position around sunrise and sunset directly: after sunrise / before sunset, and before sunrise / after sunset.
- Each field explains its effect on the morning increase and evening decrease in brightness on separate lines.
- Brightness-mode help explains that Linear transitions last the sum of the two durations, while Sigmoid uses these times for the main part of its S curve. Natural ignores these durations.
- Both duration fields now require at least one minute in the settings UI. Fractional minutes above that minimum remain supported, and new profiles use a one-minute default for the daytime duration.
- Existing durations below one minute appear as one minute when the editor is opened. Stored values are not migrated or changed on opening the editor; the new minimum is applied when the profile is saved.
- Brightness curves, calibrations and entity identifiers remain unchanged. YAML and service values retain their seconds-based format.

## Validation

Source and translation checks, Ruff, 245 Python tests and 105 frontend tests. Regression coverage includes the minimum duration, legacy profile preservation, new and duplicated profiles, conversion, preview validation and options-flow navigation. Home Assistant deployment follows the backup and exact-package checks in [the release procedure](releasing.md).
