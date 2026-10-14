# Release procedure

Completed updates should be reflected in GitHub as part of the normal project workflow requested by the owner. The release must identify the exact source revision and package that was validated.

1. Implement changes on a development branch. Keep the manifest version, README and release notes consistent; use a new version for changes to an already published runtime.
2. Run the applicable checks from the README and inspect the changes. Push the branch and wait for successful GitHub Actions on the exact revision being released. Never use an older commit's CI result as proof for a new revision.
3. Build the installation ZIP with a single `circadian_pro/` root containing the integration files, including `NOTICE` and the accompanying license. Exclude caches, temporary files and sync-conflict copies. Compare every packaged file with the release commit and record the ZIP SHA-256.
4. Before updating Home Assistant, create a native backup that includes configuration and retain a separate copy of the installed component. Record the profile configuration and switch states privately. Install the complete directory, validate the configuration, and restart Home Assistant Core when required by the update.
5. Verify the installed version, loaded profiles, preserved settings/states, panel asset and relevant logs. Check the changed UI paths without saving configuration or activating light previews unless that behavior is part of the authorized test. Describe any physical-device checks that remain unperformed.
6. Update the release documentation. Fast-forward `main` to the validated revision when possible; inspect any intervening upstream changes instead of forcing the branch. If source changes are required to resolve a divergence, validate the resulting revision before continuing.
7. Create the immutable `vX.Y.Z` tag at that revision. Publish a GitHub release with the installation asset named `circadian_pro.zip`, concise change notes, validation scope and the package SHA-256. Preserve the same ZIP bytes that were installed when publishing that installed version. Mark the release stable/latest only when that is the intended publication.
8. Read back `main`, the tag, release status and asset name/size/digest from GitHub. Confirm the published ZIP matches the verified package. Keep the previous release available for rollback and provide the new release link to the user.

Keep private backups, Home Assistant credentials, local installation records and live configuration out of Git. Do not rewrite existing release tags or replace an older version's ZIP with changed runtime code.

## HACS distribution

HACS uses `hacs.json` and downloads `custom_components/circadian_pro` from the published release tag. Keep the integration manifest version equal to the tag version without its `v` prefix. Run the HACS and Hassfest workflows on the exact candidate revision along with code checks.

Do not enable `zip_release` against the manual `circadian_pro.zip`: that ZIP includes a `circadian_pro/` wrapper, while HACS ZIP extraction expects files at the integration root. Keep the manual ZIP and tagged component byte-identical, including `brand/`, license and notice files.

A new release is how HACS discovers an update; no separate HACS upload is needed. Default-catalog inclusion requires a reviewed submission to `hacs/default` and is independent of installation by custom repository URL.

For rollback, restore the preserved component and restart Home Assistant Core. Use the native backup if configuration also requires recovery; do not overwrite `.storage` files while Home Assistant is running.

When rolling back from 0.21.26 or later to 0.21.25 or earlier, the older code does not recognize the panel-only entry. Prefer restoring the native backup made before this update. For a component-only rollback, first identify and remove only the entry marked `entry_type: panel` through Home Assistant's configuration-entry UI/API while the newer code is still running, then restore the older component and restart. Record its entry ID in the private installation receipt. Do not remove lighting profiles or edit `.storage` directly. Leaving the panel entry in place would make older versions treat it as an extra lighting profile.

## Deployment retention

Keep the active verified version and exactly two distinct previous verified versions for local/Home Assistant recovery. Count successful installed versions, not upload attempts or candidate builds. Before pruning, verify the retained packages/components, their versions and hashes, and the matching configuration/state snapshots. Never remove the active component directory.

Create the normal pre-update backup first. Only rotate recovery versions after the new installation passes the checks above. If installation fails or rollback occurs, keep the last known good version and its recovery data; do not prune during failure recovery.

After successful verification, remove older Circadian Pro component backups, uploaded ZIPs, staging folders and duplicate candidate copies. Retain one native backup for each of the two recovery points when available. Delete only identified Circadian Pro deployment backups; leave unrelated Home Assistant backups and its general backup schedule unchanged. Apply the same three-version limit to local installation packages while retaining small verification/publication records.

Inventory exact paths and native-backup IDs before deletion. Keep operations confined to the verified deployment directories; do not use broad name matching as the deletion target. Record retained and removed versions privately. Published GitHub releases, tags and Git history are not deployment clutter and remain immutable.
