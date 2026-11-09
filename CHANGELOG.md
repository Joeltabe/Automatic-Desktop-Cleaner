# Changelog

All notable changes to Desktop Cleaner are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [2.1.0] - 2026-11-09

The "free up space" release. Desktop Cleaner is now a disk space cleaner as well as a file organiser.

### Added
- **Space screen**: one-click junk analysis across 13 detectors (temp files, browser caches, Electron app caches, thumbnails, crash dumps, package manager caches, empty folders, recycle bin, project build folders, dependency stores, old installers, large forgotten downloads, protected system areas). Every group is graded *safe*, *review* or *manual* and the confirmation dialog shows exactly what will be deleted and what goes to the recycle bin.
- **Storage map**: squarified treemap of any folder with breadcrumbs, folder list, files in the current folder, largest files, breakdown by type and by age.
- **Duplicates**: byte-identical file finder (size, partial hash, full hash), keeps the oldest copy by default, sends the rest to the recycle bin.
- **Dashboard**: drive fill meters and a "Free up space" card with the reclaimable total.
- **CLI**: `space`, `junk [--clean] [--review] [--group ...]`, `scan <folder>`, `dupes [--remove]`, all with `--json`.
- **Settings**: idle project days, installer age, large file size, forgotten download age, duplicate minimum size, project roots, recycle bin policy.
- Cross-platform recycle bin support without third-party packages (Windows shell API through ctypes, Finder on macOS, freedesktop trash on Linux).
- Clean-up log (`space_history.jsonl`) with lifetime "space freed" statistics.
- `reveal_path` API: show a file in Explorer or Finder from any list.
- 12 new tests covering the scanner, duplicate finder, detectors, cleaner and trash layer.

### Changed
- Detectors size directories on a thread pool and stream each group to the UI as it completes.
- A path claimed by one detector is skipped by the others, so nothing is counted twice.
- Front-end helpers are shared through a small `window.DC` namespace between `app.js` and `space.js`.
- `--json` output and `--quiet` no longer leave stray progress characters on the console.

### Fixed
- The GUI window could show "file not found" because a query string was appended to the `file://` URL on Windows.
- The pywebview bridge recursed into the window object and crashed with "maximum recursion depth exceeded"; internal state is now kept on private attributes.
- The console no longer crashes on code pages that cannot display the progress bar characters.

## [2.0.0] - 2026-11-08

Complete rewrite of the original 46-line script.

### Added
- Rule engine: extensions, name patterns, regular expressions, minimum age, size ranges, date sub-folders, first match wins, 14 default categories.
- Preview before anything moves, per-file selection.
- Append-only journal and one-click undo of any run.
- Watch mode, conflict strategies (rename, skip, overwrite), ignore patterns, protection for files still being written.
- Full command line (`preview`, `run`, `undo`, `history`, `stats`, `watch`, `config`) with `--json`.
- pywebview desktop app with dashboard, charts, dark and light themes.
- Per-user configuration with automatic migration of the v1 `config.json`.
- Test suite, MIT licence, packaging (`pyproject.toml`).

## [1.0.0]

- Original script: move files on the Desktop into folders by extension.

[2.1.0]: https://github.com/Joeltabe/Automatic-Desktop-Cleaner/releases/tag/v2.1.0
[2.0.0]: https://github.com/Joeltabe/Automatic-Desktop-Cleaner/compare/d0c88b0...v2.0.0
