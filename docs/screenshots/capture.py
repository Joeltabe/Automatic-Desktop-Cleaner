"""Regenerate the README screenshots from the GUI's browser mock mode.

    python -m http.server 8765 --directory desktop_cleaner/gui/web   # in one terminal
    python docs/screenshots/capture.py                               # in another

Needs `pip install playwright` and an installed Edge or Chrome (no browser download).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT = os.environ.get("DC_PREVIEW_PORT", "8765")
BASE = f"http://127.0.0.1:{PORT}/index.html?clean=1"
OUT = Path(__file__).parent
SHOTS = {
    "dashboard": "&watch=1&analysis=1",
    "space": "&analysis=1#space",
    "storage-map": "&scan=1#map",
    "duplicates": "&dupes=1#dupes",
    "preview": "#preview",
    "rules": "#rules",
    "rule-editor": "&modal=rule#rules",
    "history": "#history",
    "settings": "#settings",
    "dashboard-light": "&theme=light&watch=1&analysis=1",
}


def main() -> int:
    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome"):
            try:
                browser = p.chromium.launch(channel=channel, headless=True)
                break
            except Exception:  # try the next channel
                continue
        if browser is None:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        for name, suffix in SHOTS.items():
            page.goto(BASE + suffix)
            page.wait_for_timeout(3200 if ("scan=1" in suffix or "dupes=1" in suffix) else 2200)
            page.screenshot(path=str(OUT / f"{name}.png"))
            print("saved", name)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
