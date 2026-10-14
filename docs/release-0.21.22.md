# Circadian Pro 0.21.22

- Minimum/maximum brightness show 1–100 next to their titles, without redundant descriptions. The night-brightness help retains its range below the control. Paired fields preserve their two/three-row layout without overlapping text. An invalid value such as zero produces a specific graph message and keeps the entered value visible for correction; entering a valid value restores the chart without saving or reloading.
- The short IN/OUT night labels share a row, with their times aligned underneath. Responsive positioning avoids overlaps without needlessly staggering the normal start/end pair.
- Profile values and light-control behavior are unchanged. Zero remains outside the existing brightness configuration range; a 0% graph axis is only a scale reference.

Local validation: 155 JavaScript tests pass, including brightness-range recovery and constrained label layout. Source/translation validation, Ruff and whitespace checks pass. Python behavior is unchanged; the 302-test suite is also run in exact-revision CI. Desktop/mobile visual review covers zero correction and the 00:00–05:00 schedule. Installation/publication evidence is recorded separately.
