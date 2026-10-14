# Circadian Pro 0.21.15

- Added **Apply also to temperature**, shown only for Linear and Sigmoid modes. It shares curve progress between brightness and Kelvin while retaining their separate ranges and color calibration. It is off by default; Natural keeps its independent temperature curve.
- Allowed zero and fractional minutes in both transition windows. Two zeros produce a step in the target at sunrise and sunset without division by zero. Device transitions and the adaptation interval remain separate settings.
- Preserved existing profiles, including legacy sub-minute values. Explicit sleep mode still takes priority. Shared temperature timing replaces ordinary solar temperature adaptation and pre-sleep color blending only when enabled.
- Updated English and Spanish descriptions to distinguish solar temperature adaptation, calibration, transition windows and device transitions.

Local validation: 263 Python and 116 frontend tests passed, with no skipped tests. Source/translation validation and Ruff E9/F/B checks passed. Coverage includes event boundaries, one or two zero windows, shared curves with fixed brightness, Natural and opt-out compatibility, sleep priority, configuration round trips and conditional checkbox visibility. The panel was also checked at desktop and 390 px mobile widths; zero values advance through the wizard and the checkbox retains its draft when switching modes.

The native Home Assistant options form is static and keeps the checkbox visible, with Natural explicitly documented as ignoring it. The Circadian Pro panel shows and hides it immediately when selecting a mode. Installation and exact-revision CI/publication evidence is recorded separately after verification; these tests do not replace physical light checks.
