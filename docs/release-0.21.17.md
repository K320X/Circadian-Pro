# Circadian Pro 0.21.17

- One interactive curve replaces the two static diagrams and follows the selected Natural, Linear or Sigmoid formula. Symmetric and asymmetric windows and simultaneous zeros are represented explicitly.
- Synchronized sliders and numeric controls accept whole minutes from 0 to 240 per active window. All minute fields, including solar offsets, omit decimals.
- Natural hides both unused duration controls and the shared-temperature checkbox while retaining their drafts when switching modes.
- Existing second-based values are displayed rounded to the nearest minute. Unchanged fields retain their exact stored seconds; editing a different whole-minute value updates only that setting. No profile migration or automatic clipping occurs.
- The graph uses normalized minimum/maximum bounds and an explicitly described example with 12 hours of daylight and 12 hours of night. It does not represent the actual astronomical schedule or measured light output.

Local validation: 273 Python tests and 123 JavaScript tests pass, with no skipped tests. Source/translation validation, Ruff and whitespace checks pass. Independent checks cover 30,744 graph samples against the production Python calculation and 952 minute-conversion cases. Desktop/mobile browser checks verify whole-minute slider steps, validation, graph updates and hidden Natural controls.

Installation and exact-revision CI/publication evidence is kept separately; no light preview or profile changes are required for this update.
