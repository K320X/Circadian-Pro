# Circadian Pro 0.21.19

- Added an always-visible orange-to-white color-temperature background to the existing brightness graph, without adding a second curve.
- Independent color follows the runtime daytime solar parabola in the illustrative 12-hour day. Linear and Sigmoid use their selected timing when color-temperature sharing is enabled; Natural always keeps independent color, including when the checkbox was previously checked.
- Retained sharp sunrise/sunset color edges for shared zero-duration windows, readable plot contrast and the compact mobile height.
- Renamed the checkbox and its related descriptions to explicitly refer to color temperature.

Local validation: 273 Python tests and 129 JavaScript tests pass. Source/translation validation, Ruff and whitespace checks pass. Browser review covers independent color, shared Linear/Sigmoid, the return to Natural, light/dark themes and mobile layout. Exact-revision CI, installation and publication evidence is recorded separately.

This is a presentation update. Actual light control, saved profiles, calibrations and switch states are unchanged. The graph remains illustrative; it does not reproduce calibrated LED output or sleep-mode overrides.
