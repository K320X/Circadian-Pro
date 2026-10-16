# Circadian Pro 0.21.29

The settings buttons on Home Assistant's Circadian Pro integration page now open the sidebar panel directly. A loaded profile opens its own configuration wizard; the separate Circadian Pro panel entry opens the profile list, avoiding the previous message telling users to visit the sidebar.

- Registers the panel through Home Assistant's native integration-panel mechanism, without modifying Home Assistant's interface code.
- Handles profile links when opening the panel and during navigation. Links to unavailable or deleted profiles return safely to the profile list.
- Closes the previous configuration wizard without saving its draft and stops its light preview before opening another profile. Stale requests cannot reopen an earlier profile after navigation.

Home Assistant's **Add entry** button and the separate panel entry remain in place. The panel entry keeps the sidebar available after a restart, including when no lighting profiles exist.

This release changes configuration navigation. It does not change light adaptation, stored profile settings, calibrations, entity identifiers or switch defaults.

Local validation: 322 Python tests and 177 frontend tests passed. Exact-revision CI results and installation checks are recorded in the published release and the private deployment receipt.
