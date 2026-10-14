# Circadian Pro 0.21.26

Selecting Circadian Pro in Home Assistant's Add integration screen now activates the sidebar panel immediately on a fresh installation, without creating an empty lighting profile or requesting its name. The empty panel offers **Create first profile**, followed by the existing configuration and calibration flow.

A separate panel entry keeps the sidebar available across restarts when there are no lighting profiles. Existing installations acquire that entry automatically. It creates no switches, controls no lights, is excluded from profile duplication and calibration, and does not change existing profiles or entity identifiers.

Downloading an integration still does not activate it in Home Assistant: the initial Add integration selection remains necessary. HACS catalog publication is outside this update.

To return to 0.21.25 or earlier, restore the pre-update native backup. If restoring only the component files, first remove only the new panel entry through Home Assistant while 0.21.26 is still running; older versions cannot distinguish that entry from a lighting profile. See the [rollback procedure](releasing.md).

Validation covers initial activation, concurrent activation, existing-profile migration, panel-only restart setup, last-profile cleanup, duplicate filtering and the first-profile browser flow. Exact-revision CI and Home Assistant installation evidence are recorded separately.
