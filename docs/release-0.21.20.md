# Circadian Pro 0.21.20

- The graph now represents the current local day in Home Assistant, using the profile's effective sunrise, sunset, solar noon and midnight instead of an illustrative 12-hour day.
- Sunrise and sunset markers show their local time underneath. Fixed times, offsets and earliest/latest limits are reflected, including unsaved edits. The footer shows the date and time zone.
- The graph refreshes when opened, at the next local midnight, and when the panel resumes or reconnects. Brightness sliders redraw locally without additional solar requests.
- Added read-only admin access to solar context, request cancellation/timeout handling and an explicit retry when solar data is unavailable. Invalid or unavailable schedules never fall back to invented hours.
- Preserved the compact mobile graph, independent/shared color background, existing profile settings and light-control behavior. Sleep-mode overlays are not part of this update.

Local validation: 289 Python tests and 137 JavaScript tests pass. Source/translation validation, Ruff and whitespace checks pass. Independent comparison of 18,804 graph samples against the runtime covered 16 astronomical/manual contexts, daylight-saving changes and zero/asymmetric windows. Browser review covers desktop/mobile layout and local-time labels. Exact-revision CI, installation and publication evidence is recorded separately.

The graph displays normalized brightness and illustrative color temperature; it does not reproduce calibrated LED output or sleep-mode overrides.
