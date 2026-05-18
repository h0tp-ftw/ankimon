# Ankimon Release Checklist

Jules, please verify these items before creating the PR.

- [x] **Version Bump**: `src/Ankimon/manifest.json` version matches the intended release.
- [x] **Changelogs Generated**: `assets/changelogs/<version>.md` exists.
- [x] **Discord Changelog Generated**: `assets/changelogs/<version>-discord.md` exists.
- [x] **Placeholders Removed**: All `[JULES_...]` tags have been replaced with real content.
- [x] **Contributor Credits**: `.all-contributorsrc` is updated with new contributors. User is notified to provide nickname and discord id if not present.
- [x] **Nicknames Validated**: All contributors in the changelog have entries in `.github/contributor-nicknames.json`.
- [x] **Integrity Tests**: `pytest tests/test_addon_integrity.py` passes.
- [x] **Asset Paths**: Verify that changelog paths in the PR description are correct.
