# Circadian Pro 0.21.6

## Changes

- Replaced the crowded profile-settings icon with three fine mixer faders, with smaller handles centered on their tracks. The accessible label and 44 px button target remain unchanged.
- Organized general settings into six open groups: adaptation timing, brightness and temperature, sleep mode, sunrise and sunset, manual changes, and compatibility. Light selection stays above the groups.
- Related values share rows on wide panels: minimum/maximum brightness and Kelvin, normal/initial transitions, night schedule, and solar times and bounds. Narrow panels stack these fields without overlapping descriptions.
- Kept every server-provided field, its value and validation. Unknown future fields appear under Other settings. This update does not change light adaptation or calibration behavior.
- Corrected the README's previous descriptions of New profile placement and final-step navigation.

## Validation

- Source/translation checks, Ruff, Python regression tests and Node frontend tests.
- Desktop and narrow-browser checks of field coverage, value retention, paired alignment, text wrapping, and the profile icon in light and dark themes.
- Live Home Assistant checks without saving profile changes or activating light previews. Responsive browser checks do not replace testing on a physical iPhone.

Follow [the release procedure](releasing.md) for exact-revision CI, package verification, backup, installation and publication. Private deployment records stay outside Git.
