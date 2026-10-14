# Circadian Pro 0.21.16

- Replaced the two static timing illustrations with one live curve using the selected Natural, Linear or Sigmoid formula. Equal daytime/nighttime windows center the transition on sunrise and sunset; asymmetric windows and simultaneous zeros are represented explicitly.
- Added synchronized sliders and editable minute values from 0 to 240 in each active transition window. The graph updates locally without changing lights or saving settings.
- Natural now hides both unused duration controls and the shared-temperature checkbox, while retaining their drafts when switching back to Linear or Sigmoid.
- The graph is normalized between minimum and maximum and uses a clearly identified example with 12-hour daylight and night. It shows the curve shape even for profiles with fixed brightness; it is not a live astronomical or photometric measurement.
- Existing profile configuration is preserved. Saved durations above 240 minutes are not automatically reduced, and canonical YAML/service settings retain their previous validation.

Local validation: 268 Python tests and 122 JavaScript tests pass, with no skipped tests. Source/translation validation, Ruff and whitespace checks pass. Independent comparison of 30,744 graph samples with the production Python calculation passed. Desktop and mobile browser checks cover mode changes, preserved drafts, live slider/number synchronization and hiding unused controls in Natural mode.

Installation and exact-revision CI/publication evidence is kept separately; browser checks do not replace physical device verification. No light preview or profile changes are required for this update.
