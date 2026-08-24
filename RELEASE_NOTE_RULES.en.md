# Release Note Rules

## Purpose

Release notes are shown directly in the app update window and on GitHub Releases. Include only the information users need to decide whether to update.

## CHANGELOG.md

- Include only changes from the current release.
- For a patch release, include only what changed in that patch.
- Do not repeat the full feature description from earlier versions.
- Do not list internal work such as CI, build changes, tests, automated validation, or workflow maintenance.
- Include only user-visible features, fixes, and compatibility changes.

## RELEASE_NOTES.md

- This is the user-facing text shown in the update window.
- If users can skip a feature release and receive a later patch release directly, include both the major user-facing changes from the feature release and the current patch fixes.
- Example: if `1.5.1` follows `1.5.0`, include both `1.5.0 Highlights` and `1.5.1 Fixes`.
- Do not include internal validation, CI, build environment changes, or workflow maintenance.
- Do not include installer filenames, hashes, or test pass status unless users need them to decide whether to update.

## Style

- Write concise English.
- Include the version and release date in the title.
- Phrase items as user-facing outcomes.
- Use clear verbs such as "Fixed", "Added", and "Supported".
- Prioritize what users experience over implementation details.
