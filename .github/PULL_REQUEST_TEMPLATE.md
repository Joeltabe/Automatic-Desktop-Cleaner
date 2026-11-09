## What

<!-- One paragraph: what changes and why. Link the issue if there is one. -->

## Safety checklist

- [ ] Nothing new is graded **safe** unless the software regenerates it on its own
- [ ] Anything that removes user data goes through the recycle bin by default
- [ ] New behaviour is previewed before it acts
- [ ] No new core dependency

## Quality checklist

- [ ] `pytest` passes locally
- [ ] `ruff check` passes
- [ ] Tests added or updated for the change
- [ ] `CHANGELOG.md` updated under *Unreleased*
- [ ] Screenshots regenerated if the UI changed (`docs/screenshots/capture.py`)
