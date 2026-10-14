# Circadian Pro 0.21.28

This release prepares Circadian Pro for installation and updates through HACS as a custom integration repository.

- Adds HACS metadata and installation/update instructions, including the one-time Home Assistant integration setup that activates the sidebar panel.
- Bundles the existing sun-and-moon icon in Home Assistant's `brand/` directory at normal and high resolution.
- Declares the maintainer in the integration manifest and removes the unsupported manifest `icon` property. The sidebar keeps its existing sun-and-moon icon.
- Adds HACS and Hassfest distribution checks alongside the existing regression tests.

The minimum declared Home Assistant version is 2026.10.0, the current tested release family. This is a conservative support floor, not a claim that earlier versions cannot work.

HACS installs the integration folder from the published version tag. The downloadable `circadian_pro.zip` remains available for manual installation; its wrapper folder is intentionally not used as a HACS ZIP release.

No lighting behavior, profile configuration, calibrations, entity identifiers or switch defaults change in this release. Inclusion in HACS's default catalog is a separate review process; it is not implied by publishing this release.
