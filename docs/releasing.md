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

For rollback, restore the preserved component and restart Home Assistant Core. Use the native backup if configuration also requires recovery; do not overwrite `.storage` files while Home Assistant is running.
