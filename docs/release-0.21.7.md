# Circadian Pro 0.21.7

## Changes

- Help descriptions preserve line breaks. Brightness modes and automatic sleep options are separated and punctuated, with clearer solar-time and transition explanations in Spanish and English.
- Settings groups have a subtle contrasting background that follows the Home Assistant theme.
- Profile settings buttons have a more visible border, and the three fader handles are slim horizontal lines. Button size and accessible labels are unchanged.
- Sleep color uses one compact row: select RGB or color temperature on the left and edit only the selected value on the right. Both drafts are retained when switching; invalid values stay visible for correction. The row stacks on narrow screens.
- The lights picker uses a centered chevron instead of a font glyph.
- No changes to stored settings, light adaptation, calibration or preview behavior.

## Validation

- Source/translation validation, Ruff, 225 Python tests and 105 frontend tests, including mode switching, draft preservation, invalid values and backwards navigation.
- Browser checks in light and dark themes, on desktop and at mobile width, including multiline descriptions, group contrast and fader controls.
- Installation checks on Home Assistant with backup and unchanged profile configuration and switch states. No light previews or physical iPhone tests.

Follow [the release procedure](releasing.md) for exact-revision CI, package integrity, backup and publication verification.
