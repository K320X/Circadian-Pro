# Circadian Pro 0.21.21

- The daily graph now includes automatic sleep mode on the same brightness curve and color background. Solar and scheduled windows use the configured sleep brightness and RGB or color-temperature target.
- Entry and exit illustrate the general transition duration. Compact expandable information explains adaptation-cycle delay and device-dependent interpolation; manual sleep-switch changes are not predicted.
- Scheduled night start/end have dotted red lines, their names and local times below the plot. Elapsed-time day/night midpoint references appear above as clock times only, separate from the astronomical noon/midnight used by the curves.
- Sleep schedules crossing midnight and daylight-saving changes retain their actual wall-clock behavior. Draft schedule edits refresh read-only context; brightness, color and transition edits redraw locally without saving profiles or controlling lights.
- Automatic sleep mode uses a 0–100% brightness scale, including profiles with equal normal minimum/maximum brightness. The normalized daytime graph remains available when automatic sleep mode is disabled. Mobile keeps the compact curve and separates nearby schedule labels.

Local validation: 301 Python tests and 150 JavaScript tests pass, with no skips. Source/translation validation, Ruff and whitespace checks pass. Regressions cover exact five-second fades, zero-duration edges, interrupted and midnight-crossing transitions, return to intermediate linear/sigmoid targets, all-day night, RGB/CCT, repeated/skipped DST times and read-only draft isolation. Desktop/mobile visual checks cover label placement, brightness scaling and automatic-mode changes. Exact-revision CI, installation and publication evidence is recorded separately.

This release changes the graph, its read-only context and transition descriptions only; existing light-control behavior, saved profiles, calibrations, entity identifiers and switch states are preserved. Colors and fades are illustrative, not a measurement or a prediction of calibrated LED output.
