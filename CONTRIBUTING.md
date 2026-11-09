# Contributing to Desktop Cleaner

Thanks for taking the time. Bug reports, detector ideas, translations and pull requests are all welcome.

## Ground rules

- **Safety first.** Anything that deletes must be graded correctly: *safe* is only for data that software regenerates on its own; everything else goes to the recycle bin. When in doubt, grade it *review*.
- **No new core dependencies.** The engine and CLI run on the standard library. The GUI's only dependency is pywebview.
- **Preview before action.** New features that change files must show the user what will happen first.

## Setting up

```bash
git clone https://github.com/Joeltabe/Automatic-Desktop-Cleaner.git
cd Automatic-Desktop-Cleaner
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # macOS / Linux
pip install -e ".[dev]"
pytest
```

Run the GUI with `python -m desktop_cleaner gui`. For front-end work, serve `desktop_cleaner/gui/web/` with any static server and open `index.html?clean=1&analysis=1#space`; the page runs against a mock API with demo data, no Python needed.

## Adding a junk detector

1. Add a function in `desktop_cleaner/space/junk.py` that takes the `_Ctx` and returns a `JunkGroup` (or `None` when nothing was found).
2. Collect candidate paths, then call `ctx.items_for([(path, detail, kind, min_bytes), ...])`. Sizing runs on a thread pool and paths already claimed by another detector are skipped automatically.
3. Pick the confidence: `safe` (regenerates), `review` (recreatable or probably unwanted), `manual` (report only, give `hint` and `hint_url`).
4. Register it in `DETECTORS` with a short stable id.
5. Add a test in `tests/test_space.py` that builds a fake layout in `tmp_path` and uses `analyze(cfg, only=["your-id"])`.

## Pull requests

- One topic per pull request. Small is good.
- Run `pytest` before pushing. CI runs the suite on Windows, macOS and Linux.
- Update `CHANGELOG.md` under *Unreleased*.
- Screenshots in the README are generated with `docs/screenshots/capture.py`; regenerate them if you change the UI.

## Reporting bugs

Open an issue with the output of `desktop-cleaner --version`, your OS, and the relevant lines from `app.log` (next to the config file, `desktop-cleaner config path` tells you where). Never paste file contents you would not want public.
