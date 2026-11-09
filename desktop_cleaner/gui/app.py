"""pywebview application: exposes the engine to the HTML front end through a JS API.

Note on attribute names: pywebview recursively exposes every *public* attribute of
the ``js_api`` object to JavaScript. Anything that is not meant to be called from
the page (the window handle, the config, the journal) therefore lives on
underscore-prefixed attributes, otherwise pywebview walks into them.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Any, Callable

from .. import __version__
from ..config import (CONFLICT_STRATEGIES, DATE_SUBFOLDER_MODES, config_dir, config_from_dict, config_to_dict,
                      default_config, default_config_path, ensure_config, save_config)
from ..engine import Plan, apply_plan, build_plan
from ..journal import Journal
from ..rules import human_size
from ..space.cleaner import SpaceLog, execute as execute_clean, remove_paths
from ..space.common import Cancel, Cancelled
from ..space.disks import list_volumes
from ..space.duplicates import find_duplicates
from ..space.junk import Analysis, analyze
from ..space.scanner import ScanResult, scan_tree
from ..stats import summarize
from ..watcher import Watcher

WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger("desktop_cleaner.gui")


def _setup_logging() -> Path:
    log_path = config_dir() / "app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(log_path) for h in root.handlers):
        root.addHandler(handler)
    return log_path


def _guard(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Never let an exception escape into the JS bridge; log it and return an error payload."""

    @functools.wraps(fn)
    def wrapper(self: "Api", *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - we want everything
            log.error("api.%s failed: %s\n%s", fn.__name__, exc, traceback.format_exc())
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return wrapper


class Api:
    """Every public method here is callable from JavaScript as ``window.pywebview.api.<name>``."""

    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path or default_config_path()
        self._cfg, _, _ = ensure_config(self._config_path)
        self._journal = Journal()
        self._window: Any = None
        self._watcher: Watcher | None = None
        self._lock = threading.Lock()
        self._log_path = _setup_logging()
        # free-up-space state
        self._space_log = SpaceLog()
        self._cancel = Cancel()
        self._busy: str | None = None
        self._analysis: Analysis | None = None
        self._scan: ScanResult | None = None
        self._dupes: Any = None

    # ------------------------------------------------------------------ helpers
    def _emit(self, event: str, payload: Any = None) -> None:
        if self._window is None:
            return
        js = f"window.__dc_event && window.__dc_event({json.dumps(event)}, {json.dumps(payload)})"
        try:
            self._window.evaluate_js(js)
        except Exception as exc:  # noqa: BLE001
            log.warning("evaluate_js failed for %s: %s", event, exc)

    @staticmethod
    def _ok(**kw: Any) -> dict[str, Any]:
        return {"ok": True, **kw}

    @staticmethod
    def _err(message: str) -> dict[str, Any]:
        return {"ok": False, "error": message}

    def _dialog(self, kind: str, **kw: Any) -> Any:
        import webview
        fd = getattr(webview, "FileDialog", None)  # pywebview >= 5.4 enum; the module constants are deprecated
        if fd is not None:
            dialog_type = {"open": fd.OPEN, "folder": fd.FOLDER, "save": fd.SAVE}[kind]
        else:  # pragma: no cover - older pywebview
            dialog_type = {"open": webview.OPEN_DIALOG, "folder": webview.FOLDER_DIALOG, "save": webview.SAVE_DIALOG}[kind]
        return self._window.create_file_dialog(dialog_type, **kw)

    # ------------------------------------------------------------------ app info
    @_guard
    def bootstrap(self) -> dict[str, Any]:
        return self._ok(
            version=__version__,
            config_path=str(self._config_path),
            journal_path=str(self._journal.path),
            log_path=str(self._log_path),
            platform=sys.platform,
            home=str(Path.home()),
            conflict_strategies=list(CONFLICT_STRATEGIES),
            date_modes=list(DATE_SUBFOLDER_MODES),
            config=config_to_dict(self._cfg),
            watching=self._watcher.running if self._watcher else False,
        )

    @_guard
    def log_client_error(self, message: str, detail: str = "") -> dict[str, Any]:
        log.error("client: %s %s", message, detail)
        return self._ok()

    # ------------------------------------------------------------------ config
    @_guard
    def get_config(self) -> dict[str, Any]:
        return self._ok(config=config_to_dict(self._cfg))

    @_guard
    def save_config(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            cfg = config_from_dict(data)
        except Exception as exc:  # noqa: BLE001 - validation problems surface to the UI
            return self._err(f"Invalid configuration: {exc}")
        with self._lock:
            self._cfg = cfg
            save_config(cfg, self._config_path)
            if self._watcher:
                self._watcher.update_config(cfg)
        return self._ok(config=config_to_dict(cfg))

    @_guard
    def reset_config(self) -> dict[str, Any]:
        with self._lock:
            self._cfg = default_config()
            save_config(self._cfg, self._config_path)
        return self._ok(config=config_to_dict(self._cfg))

    @_guard
    def import_config(self) -> dict[str, Any]:
        paths = self._dialog("open", allow_multiple=False, file_types=("JSON files (*.json)", "All files (*.*)"))
        if not paths:
            return self._err("cancelled")
        path = paths[0] if isinstance(paths, (list, tuple)) else paths
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg = config_from_dict(json.load(fh))
        except Exception as exc:  # noqa: BLE001
            return self._err(f"Could not import: {exc}")
        with self._lock:
            self._cfg = cfg
            save_config(cfg, self._config_path)
        return self._ok(config=config_to_dict(cfg))

    @_guard
    def export_config(self) -> dict[str, Any]:
        target = self._dialog("save", save_filename="desktop-cleaner-config.json")
        if not target:
            return self._err("cancelled")
        path = Path(target[0] if isinstance(target, (list, tuple)) else target)
        save_config(self._cfg, path)
        return self._ok(path=str(path))

    # ------------------------------------------------------------------ folders
    @_guard
    def pick_folder(self) -> dict[str, Any]:
        result = self._dialog("folder")
        if not result:
            return self._err("cancelled")
        return self._ok(path=result[0] if isinstance(result, (list, tuple)) else result)

    @_guard
    def open_path(self, path: str) -> dict[str, Any]:
        p = Path(os.path.expandvars(path)).expanduser()
        if not p.exists():
            return self._err("That folder does not exist yet")
        if sys.platform == "win32":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return self._ok()

    @_guard
    def open_url(self, url: str) -> dict[str, Any]:
        if sys.platform == "win32" and url.startswith("ms-settings:"):
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            webbrowser.open(url)
        return self._ok()

    @_guard
    def reveal_path(self, path: str) -> dict[str, Any]:
        """Open the containing folder with the item selected (falls back to opening the folder)."""
        p = Path(path)
        if not p.exists():
            return self._err("That file no longer exists")
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
        return self._ok()

    @_guard
    def source_summary(self) -> dict[str, Any]:
        """Quick read-only look at each source: counts by extension for the dashboard."""
        out = []
        for src in self._cfg.source_paths():
            entry: dict[str, Any] = {"path": str(src), "exists": src.is_dir(), "files": 0, "folders": 0, "bytes": 0, "top_ext": []}
            if src.is_dir():
                counts: dict[str, int] = {}
                try:
                    for p in src.iterdir():
                        if p.is_dir():
                            entry["folders"] += 1
                        else:
                            entry["files"] += 1
                            try:
                                entry["bytes"] += p.stat().st_size
                            except OSError:
                                pass
                            ext = p.suffix.lower().lstrip(".") or "(none)"
                            counts[ext] = counts.get(ext, 0) + 1
                except OSError:
                    pass
                entry["top_ext"] = sorted(({"ext": k, "count": v} for k, v in counts.items()),
                                          key=lambda d: -d["count"])[:8]
            out.append(entry)
        return self._ok(sources=out)

    # ------------------------------------------------------------------ engine
    @_guard
    def preview(self) -> dict[str, Any]:
        plan = build_plan(self._cfg)
        return self._ok(plan=plan.to_dict(), human_total=human_size(plan.bytes_total))

    @_guard
    def run(self, only: list[str] | None = None) -> dict[str, Any]:
        plan = build_plan(self._cfg)
        if not plan.actions:
            return self._ok(result=None, plan=plan.to_dict())
        selected = set(only) if only else None

        def progress(i: int, n: int, action: Any) -> None:
            self._emit("progress", {"index": i, "total": n, "name": Path(action.source).name})

        result = apply_plan(plan, self._cfg, journal=self._journal, trigger="gui", progress=progress, only=selected)
        log.info("run: moved %d, failed %d, skipped %d", len(result.moved), len(result.failed), len(result.skipped_conflicts))
        return self._ok(result=result.to_dict(), plan=plan.to_dict(), human_total=human_size(result.bytes_moved))

    @_guard
    def undo(self, batch_id: str | None = None) -> dict[str, Any]:
        try:
            res = self._journal.undo(batch_id)
        except (LookupError, ValueError) as exc:
            return self._err(str(exc))
        log.info("undo %s: restored %d, failed %d", res.batch_id, len(res.restored), len(res.failed))
        return self._ok(batch_id=res.batch_id, restored=len(res.restored),
                        failed=[{"file": m.dest, "error": e} for m, e in res.failed])

    @_guard
    def history(self, limit: int = 50) -> dict[str, Any]:
        batches = self._journal.batches()[-int(limit):]
        return self._ok(batches=[b.to_dict() for b in reversed(batches)])

    @_guard
    def stats(self, days: int = 30) -> dict[str, Any]:
        return self._ok(stats=summarize(self._journal, days=int(days)))

    @_guard
    def clear_history(self) -> dict[str, Any]:
        self._journal.clear()
        return self._ok()

    # ------------------------------------------------------------------ watch mode
    @_guard
    def watch_start(self) -> dict[str, Any]:
        if self._watcher and self._watcher.running:
            return self._ok(watching=True)

        def on_batch(plan: Plan, result: Any) -> None:
            self._emit("watch_batch", {"moved": len(result.moved), "bytes": result.bytes_moved,
                                       "names": [Path(m.src).name for m in result.moved][:5]})

        def on_tick(plan: Plan) -> None:
            self._emit("watch_tick", {"pending": len(plan.actions)})

        self._watcher = Watcher(self._cfg, journal=self._journal, on_batch=on_batch, on_tick=on_tick)
        self._watcher.start()
        log.info("watch started")
        return self._ok(watching=True)

    @_guard
    def watch_stop(self) -> dict[str, Any]:
        if self._watcher:
            self._watcher.stop()
        log.info("watch stopped")
        return self._ok(watching=False)

    @_guard
    def watch_status(self) -> dict[str, Any]:
        w = self._watcher
        return self._ok(watching=bool(w and w.running), batches=w.batches_applied if w else 0,
                        files=w.files_moved if w else 0)

    # ------------------------------------------------------------------ free up space
    def _begin(self, what: str) -> str | None:
        with self._lock:
            if self._busy:
                return f"{self._busy} is still running. Cancel it first."
            self._busy = what
            self._cancel.reset()
        return None

    def _end(self) -> None:
        with self._lock:
            self._busy = None

    def _default_dupe_roots(self) -> list[str]:
        if self._cfg.space.duplicate_roots:
            return [str(Path(os.path.expandvars(r)).expanduser()) for r in self._cfg.space.duplicate_roots]
        roots = [str(p) for p in self._cfg.source_paths()]
        for extra in ("Downloads", "Documents", "Pictures", "Videos", "Music"):
            p = Path.home() / extra
            if p.is_dir() and str(p) not in roots:
                roots.append(str(p))
        return roots

    @_guard
    def space_overview(self) -> dict[str, Any]:
        return self._ok(volumes=list_volumes(), history=self._space_log.summary(30),
                        analysis=self._analysis.to_dict() if self._analysis else None, busy=self._busy,
                        duplicate_roots=self._default_dupe_roots())

    @_guard
    def analyze(self) -> dict[str, Any]:
        err = self._begin("Analysis")
        if err:
            return self._err(err)
        try:
            analysis = analyze(self._cfg, cancel=self._cancel, progress=lambda p: self._emit("analyze_progress", p))
        except Cancelled:
            return self._err("cancelled")
        finally:
            self._end()
        self._analysis = analysis
        log.info("analysis: %d groups, safe %s, review %s in %.1fs", len(analysis.groups), human_size(analysis.total("safe")),
                 human_size(analysis.total("review")), analysis.duration)
        return self._ok(analysis=analysis.to_dict(), volumes=list_volumes())

    @_guard
    def cancel_scan(self) -> dict[str, Any]:
        self._cancel.set()
        return self._ok()

    @_guard
    def clean(self, groups_all: list[str] | None = None, paths: list[str] | None = None, permanent: bool | None = None) -> dict[str, Any]:
        if self._analysis is None:
            return self._err("Run an analysis first")
        err = self._begin("Clean-up")
        if err:
            return self._err(err)
        perm = self._cfg.space.permanent_delete if permanent is None else bool(permanent)
        groups_all = list(groups_all or [])
        paths = list(paths or [])
        try:
            result = execute_clean(self._analysis, groups_all, paths, permanent_review=perm, log=self._space_log,
                                   progress=lambda p: self._emit("clean_progress", p), cancel=self._cancel, trigger="gui")
        except Cancelled:
            return self._err("cancelled")
        finally:
            self._end()
        # Drop what is gone from the cached analysis so the screen updates without a rescan.
        failed = {os.path.normcase(f["path"]) for f in result.failed}
        wanted = {os.path.normcase(p) for p in paths}
        for g in self._analysis.groups:
            if g.confidence == "manual":
                continue
            keep = []
            for item in g.items:
                selected = g.id in groups_all or os.path.normcase(item.path) in wanted
                if selected and os.path.normcase(item.path) not in failed:
                    continue
                if selected and item.kind in ("contents", "recycle_bin"):
                    # partially emptied: re-measure what is left
                    from ..space.common import path_size
                    size, count = path_size(item.path) if item.kind == "contents" else (0, 0)
                    if size <= 0:
                        continue
                    item.size, item.count = size, count
                keep.append(item)
            g.items = keep
        self._analysis.groups = [g for g in self._analysis.groups if g.items]
        log.info("clean %s: freed %s, removed %d, failed %d", result.clean_id, human_size(result.freed), result.removed, len(result.failed))
        return self._ok(result=result.to_dict(), analysis=self._analysis.to_dict(), volumes=list_volumes(), human_freed=human_size(result.freed))

    @_guard
    def scan_folder(self, path: str | None = None, depth: int = 2) -> dict[str, Any]:
        target = Path(os.path.expandvars(path or str(Path.home()))).expanduser()
        if not target.is_dir():
            return self._err("That folder does not exist")
        err = self._begin("Folder scan")
        if err:
            return self._err(err)
        try:
            self._scan = scan_tree(target, cancel=self._cancel, progress=lambda p: self._emit("scan_progress", p),
                                   exclude_names={"$RECYCLE.BIN", "System Volume Information"})
        except Cancelled:
            return self._err("cancelled")
        finally:
            self._end()
        log.info("scan %s: %d files, %s in %.1fs", target, self._scan.files, human_size(self._scan.bytes), self._scan.duration)
        return self._ok(scan=self._scan.to_dict(depth=int(depth)))

    @_guard
    def scan_node(self, path: str, depth: int = 2) -> dict[str, Any]:
        if self._scan is None:
            return self._err("Scan a folder first")
        node = self._scan.root.find(path)
        if node is None:
            return self._err("That folder is not part of the last scan")
        return self._ok(node=node.to_dict(depth=int(depth), root_size=max(1, node.size)))

    @_guard
    def list_files(self, path: str, limit: int = 200) -> dict[str, Any]:
        """Files directly inside a folder, largest first (for the storage map detail pane)."""
        p = Path(path)
        if not p.is_dir():
            return self._err("Folder not found")
        rows = []
        try:
            for entry in os.scandir(p):
                try:
                    if entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        rows.append({"path": entry.path, "name": entry.name, "size": st.st_size, "mtime": st.st_mtime})
                except OSError:
                    continue
        except OSError as exc:
            return self._err(str(exc))
        rows.sort(key=lambda r: -r["size"])
        return self._ok(files=rows[:int(limit)], total=len(rows))

    @_guard
    def find_duplicates(self, roots: list[str] | None = None, min_mb: float | None = None) -> dict[str, Any]:
        roots = [r for r in (roots or self._default_dupe_roots()) if r]
        if not roots:
            return self._err("Pick at least one folder")
        mb = self._cfg.space.duplicate_min_mb if min_mb is None else float(min_mb)
        err = self._begin("Duplicate scan")
        if err:
            return self._err(err)
        try:
            self._dupes = find_duplicates(roots, min_bytes=int(mb * 1024 * 1024), exclude_names=set(self._cfg.space.exclude),
                                          cancel=self._cancel, progress=lambda p: self._emit("dupes_progress", p))
        except Cancelled:
            return self._err("cancelled")
        finally:
            self._end()
        log.info("duplicates: %d groups, %s reclaimable in %.1fs", len(self._dupes.groups), human_size(self._dupes.reclaimable), self._dupes.duration)
        return self._ok(dupes=self._dupes.to_dict())

    @_guard
    def remove_paths(self, paths: list[str], permanent: bool = False, label: str = "files") -> dict[str, Any]:
        if not paths:
            return self._err("Nothing selected")
        result = remove_paths(list(paths), permanent=bool(permanent), log=self._space_log, label=label, trigger="gui")
        gone = {os.path.normcase(p) for p in paths} - {os.path.normcase(f["path"]) for f in result.failed}
        if self._dupes is not None:
            for g in self._dupes.groups:
                g.files = [f for f in g.files if os.path.normcase(f.path) not in gone]
            self._dupes.groups = [g for g in self._dupes.groups if len(g.files) > 1]
        log.info("remove_paths(%s): freed %s, removed %d, failed %d", label, human_size(result.freed), result.removed, len(result.failed))
        return self._ok(result=result.to_dict(), human_freed=human_size(result.freed),
                        dupes=self._dupes.to_dict() if self._dupes is not None else None)

    @_guard
    def space_history(self, days: int = 30) -> dict[str, Any]:
        return self._ok(history=self._space_log.summary(int(days)))

    @_guard
    def clear_space_history(self) -> dict[str, Any]:
        self._space_log.clear()
        return self._ok()

    # ------------------------------------------------------------------ window
    @_guard
    def minimize(self) -> dict[str, Any]:
        if self._window:
            self._window.minimize()
        return self._ok()

    @_guard
    def toggle_fullscreen(self) -> dict[str, Any]:
        if self._window:
            self._window.toggle_fullscreen()
        return self._ok()

    @_guard
    def close(self) -> dict[str, Any]:
        if self._watcher:
            self._watcher.stop(timeout=1.0)
        if self._window:
            self._window.destroy()
        return self._ok()


def launch(config_path: Path | None = None, debug: bool = False) -> int:
    import webview

    api = Api(config_path)
    log.info("launching GUI v%s (pywebview %s)", __version__, getattr(webview, "__version__", "?"))
    window = webview.create_window(
        "Desktop Cleaner",
        url=str(WEB_DIR / "index.html"),
        js_api=api,
        width=1180,
        height=760,
        min_size=(900, 600),
        background_color="#0b0f1a",
        text_select=False,
    )
    api._window = window

    def on_closing() -> None:
        if api._watcher:
            api._watcher.stop(timeout=1.0)

    window.events.closing += on_closing
    try:
        webview.start(debug=debug or bool(os.environ.get("DESKTOP_CLEANER_DEBUG")))
    except Exception:  # noqa: BLE001
        log.critical("GUI crashed:\n%s", traceback.format_exc())
        raise
    return 0
