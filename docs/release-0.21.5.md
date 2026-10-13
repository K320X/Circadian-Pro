# Circadian Pro 0.21.5

## Changes

- The final 1000 K calibration screen now offers **Back** alongside **Finish**. Back retains the draft and ends any active preview before returning to 4000 K; it does not save the profile.
- RGB and white calibration channels offer a colored slider and a synchronized editable number. Invalid numeric drafts remain visible for validation rather than being silently clamped.
- The RGB+CCT white-mixture slider spans the configured warm-white and cold-white temperatures, with warm on the left and cold on the right. A 1 K step permits exact numeric adjustments and both physical endpoints.
- All loaded profiles appear in a table with an individual settings button. **New profile** is centered underneath. Sorting supports recent modification, A–Z and Z–A; catalog updates remain automatic.
- The same controls adapt to desktop and mobile layouts, retaining the mobile sidebar button and opt-in light previews.

## Validation

- Python regression suite, Node frontend regressions, source/translation validation and Ruff error checks.
- Browser checks of slider dragging, number edits, draft retention through the final Back button, profile sorting and layouts at desktop, 390 px and 320 px widths, in light and dark themes.
- Tests use local light doubles. Responsive browser checks do not replace a physical iOS Companion check.

Follow [the release procedure](releasing.md) for package, CI, backup, installation and publication verification. Keep private deployment records outside Git.
