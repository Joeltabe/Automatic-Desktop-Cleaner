#!/usr/bin/env python3
"""Backward-compatible launcher.

Running ``python desktop-cleaner.py`` used to organise ~/Desktop using the
``config.json`` next to this file. It now hands off to the full CLI; a legacy
config.json in this folder is imported automatically on first run.

    python desktop-cleaner.py            # opens the GUI (or previews if pywebview is missing)
    python desktop-cleaner.py run        # organise now
    python desktop-cleaner.py --help     # everything else
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from desktop_cleaner.cli import main  # noqa: E402
from desktop_cleaner.config import default_config_path, load_config, save_config  # noqa: E402


def _import_legacy_config() -> None:
    legacy = HERE / "config.json"
    target = default_config_path()
    if legacy.exists() and not target.exists():
        try:
            save_config(load_config(legacy), target)
            print(f"Imported legacy config.json into {target}")
        except Exception as exc:  # never block the user on a bad old file
            print(f"Could not import legacy config.json: {exc}")


if __name__ == "__main__":
    _import_legacy_config()
    raise SystemExit(main())
