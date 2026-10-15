# README screenshots

These screenshots show the real panel from Circadian Pro 0.21.28, rendered by
`tests/fixtures/calibration-panel.html` with synthetic Home Assistant responses.
They contain no live installation data and were captured on 2026-10-15.

To reproduce the views, serve the fixture, panel module and translation files
locally, preserving their repository-relative paths. Open:

- `/tests/fixtures/calibration-panel.html?lang=en&showcase=1` for the English desktop views.
- `/tests/fixtures/calibration-panel.html?lang=es&showcase=1` for the Spanish mobile view.

Showcase mode uses fictional profiles, a dark theme, a fixed demonstration date
of 2026-06-21 and UTC solar times. The fixture does not connect to Home Assistant
or operate lights. Without `showcase=1`, it retains its normal testing defaults.

| Image | View |
| --- | --- |
| `profiles.jpg` | Profile list, cropped to the card. |
| `daily-curve.jpg` | Living room general settings, transition controls and daily curve. |
| `calibration.jpg` | Living room 4000 K calibration, including RGB and dual-white controls. |
| `mobile-es.jpg` | Spanish profile list at a 390 px viewport width, cropped below the card. |

Captures preserve the panel's rendering; only surrounding empty space is cropped.
Before replacing them, inspect every saved image for readable controls and check
that it contains no real profile names, entity identifiers, location or account data.
