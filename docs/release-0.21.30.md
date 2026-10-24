# Circadian Pro 0.21.30

Time-zone files are no longer loaded synchronously on Home Assistant's event loop. This addresses the four locations identified during the HACS review.

- The solar-chart WebSocket handler resolves the configured time zone with `await dt_util.async_get_time_zone(...)`. An unavailable zone returns an explicit error instead of silently using a different zone.
- Solar profile settings reuse Home Assistant's already loaded default time zone. Automatic sleep checks use Home Assistant's local clock without constructing `ZoneInfo`.
- Local schedules, daylight-saving changes, solar offsets, calibrations, entity identifiers and switch defaults retain their existing behavior. No configuration migration is needed.

Regression coverage includes asynchronous chart resolution, unavailable zones, local sunrise times across daylight-saving changes, repeated clock hours, daytime and overnight sleep windows, and configuration after a time-zone change.

Local validation: 330 Python tests and 177 frontend tests passed, along with source checks and Ruff. Exact-revision CI results and Home Assistant installation status are recorded separately in the published release and private deployment receipt.
