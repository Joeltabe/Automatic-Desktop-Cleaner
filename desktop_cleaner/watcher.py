"""Polling watch mode: organise new files as they settle, using only the standard library."""
from __future__ import annotations

import threading
import time
from typing import Callable

from .config import Config
from .engine import ApplyResult, Plan, apply_plan, build_plan
from .journal import Journal

OnBatch = Callable[[Plan, ApplyResult], None]
OnTick = Callable[[Plan], None]


class Watcher:
    """Re-plans every ``interval`` seconds and applies whatever is ready.

    Files are considered "ready" by the engine's own ``skip_recent_seconds``
    rule, so a download that is still being written is left alone until it
    has been quiet long enough.
    """

    def __init__(self, cfg: Config, journal: Journal | None = None, interval: float | None = None,
                 on_batch: OnBatch | None = None, on_tick: OnTick | None = None):
        self.cfg = cfg
        self.journal = journal
        self.interval = float(interval or cfg.options.watch_interval_seconds)
        self.on_batch = on_batch
        self.on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_plan: Plan | None = None
        self.batches_applied = 0
        self.files_moved = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def tick(self) -> ApplyResult | None:
        plan = build_plan(self.cfg)
        self.last_plan = plan
        if self.on_tick:
            self.on_tick(plan)
        if not plan.actions:
            return None
        result = apply_plan(plan, self.cfg, journal=self.journal, trigger="watch")
        if result.moved:
            self.batches_applied += 1
            self.files_moved += len(result.moved)
            if self.on_batch:
                self.on_batch(plan, result)
        return result

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # keep watching even if one pass blows up
                pass
            self._stop.wait(self.interval)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run_forever, name="desktop-cleaner-watch", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            self._thread = None

    def update_config(self, cfg: Config) -> None:
        self.cfg = cfg
        self.interval = float(cfg.options.watch_interval_seconds)
