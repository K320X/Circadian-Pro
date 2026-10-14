# Circadian Pro 0.21.18

- Emphasized Natural, Linear and Sigmoid names in the mode descriptions and removed the repeated explanation of zero-duration windows.
- Removed the visible rise/fall/duration/temperature summary beneath the graph, retaining a short illustrative-day caption and an accessible description for screen readers.
- Reduced the mobile plot height from 176 to 117 pixels. Desktop chart height and all curve calculations remain unchanged.

Local validation: 273 Python tests and 126 JavaScript tests pass, with no omissions. Source/translation validation, Ruff and whitespace checks pass. Browser inspection confirms bold mode labels, a clean graph caption, 117 px plot height on a 390 px mobile viewport and unchanged 176 px desktop height.

This is a presentation update; profiles, calibrations and lighting behavior are unchanged. Exact-revision CI, installation and publication evidence is recorded separately.
