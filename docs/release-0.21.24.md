# Circadian Pro 0.21.24

Numeric settings and calibration controls now show their existing limits next to their titles with compact spacing. Bounds come from the same Home Assistant schema as each input, including the configured warm/cold white temperatures used for the white-mix slider. One-sided limits show only the known bound; unbounded fields do not display an invented range.

Brightness labels retain their compact two-row layout. Other fields keep their explanations below the control, and accessible descriptions include both the range and any help text. Limits, stored values, calibrations and light-control behavior are unchanged.

Validation covers JavaScript regression tests for schema bounds, dynamic white-channel ranges, accessibility and slider synchronization, source checks, exact-revision CI and desktop/mobile inspection. Installation and recovery-retention evidence is recorded separately.
