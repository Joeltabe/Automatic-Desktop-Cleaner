"""Small shared helpers for the space modules: cancellation, sizing, platform paths."""
from __future__ import annotations

import os
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

ProgressFn = Callable[[dict], None]


class Cancelled(Exception):
    """Raised inside a scan when the cancel event is set."""


class Cancel:
    """A cancellation token that scans poll between directory entries."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def set(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


class Throttle:
    """Call ``fn`` at most every ``interval`` seconds (used for progress callbacks)."""

    def __init__(self, fn: ProgressFn | None, interval: float = 0.15):
        self.fn = fn
        self.interval = interval
        self._last = 0.0

    def __call__(self, payload: dict, force: bool = False) -> None:
        if self.fn is None:
            return
        now = time.monotonic()
        if force or now - self._last >= self.interval:
            self._last = now
            try:
                self.fn(payload)
            except Exception:  # noqa: BLE001 - progress must never break a scan
                pass


def home() -> Path:
    return Path.home()


def env_path(var: str, *rest: str) -> Path | None:
    base = os.environ.get(var)
    if not base:
        return None
    return Path(base).joinpath(*rest)


def local_appdata() -> Path | None:
    if IS_WIN:
        return env_path("LOCALAPPDATA") or home() / "AppData" / "Local"
    return None


def roaming_appdata() -> Path | None:
    if IS_WIN:
        return env_path("APPDATA") or home() / "AppData" / "Roaming"
    return None


def xdg_cache() -> Path:
    if IS_MAC:
        return home() / "Library" / "Caches"
    return Path(os.environ.get("XDG_CACHE_HOME") or home() / ".cache")


def is_reparse_or_link(entry: os.DirEntry) -> bool:
    """True for symlinks and Windows junctions/reparse points, which scans never follow."""
    try:
        if entry.is_symlink():
            return True
        if IS_WIN:
            st = entry.stat(follow_symlinks=False)
            return bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True
    return False


def iter_entries(path: Path | str) -> Iterator[os.DirEntry]:
    """``os.scandir`` that swallows permission errors and vanished directories."""
    try:
        with os.scandir(path) as it:
            for entry in it:
                yield entry
    except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
        return


def dir_size(path: Path | str, cancel: Cancel | None = None, limit_entries: int | None = None) -> tuple[int, int]:
    """Total size in bytes and number of files under ``path`` (no link following)."""
    total = 0
    count = 0
    stack = [str(path)]
    seen = 0
    while stack:
        current = stack.pop()
        for entry in iter_entries(current):
            seen += 1
            if cancel is not None and seen % 512 == 0:
                cancel.check()
            if limit_entries is not None and seen > limit_entries:
                return total, count
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not is_reparse_or_link(entry):
                        stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
                    count += 1
            except OSError:
                continue
    return total, count


def path_size(path: Path | str, cancel: Cancel | None = None) -> tuple[int, int]:
    """Size and count for a file *or* a directory."""
    p = Path(path)
    try:
        if p.is_symlink():
            return 0, 0
        if p.is_dir():
            return dir_size(p, cancel)
        if p.is_file():
            return p.stat().st_size, 1
    except OSError:
        pass
    return 0, 0


def age_days(mtime: float) -> float:
    return max(0.0, (time.time() - mtime) / 86400.0)


def mtime_of(path: Path | str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return time.time()


def is_hidden_name(name: str) -> bool:
    return name.startswith(".")


EXT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Images": ("jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "svg", "tiff", "tif", "ico", "raw", "cr2", "nef", "psd", "ai"),
    "Video": ("mp4", "mkv", "mov", "avi", "webm", "wmv", "m4v", "flv", "mpg", "mpeg", "ts", "3gp"),
    "Audio": ("mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "aiff", "opus"),
    "Documents": ("pdf", "doc", "docx", "odt", "rtf", "txt", "md", "pages", "xls", "xlsx", "csv", "ods", "ppt", "pptx", "key", "odp", "epub"),
    "Archives": ("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "tgz", "cab"),
    "Installers": ("exe", "msi", "msix", "appx", "dmg", "pkg", "deb", "rpm", "appimage", "apk"),
    "Disk images": ("iso", "img", "vhd", "vhdx", "vmdk", "qcow2", "ova", "wim", "esd"),
    "Code": ("py", "js", "ts", "tsx", "jsx", "html", "css", "scss", "json", "java", "kt", "c", "h", "cpp", "cs", "go", "rs",
             "sh", "ps1", "sql", "ipynb", "dart", "swift", "rb", "php", "yaml", "yml", "toml", "xml", "lock"),
    "Data & databases": ("db", "sqlite", "sqlite3", "parquet", "arrow", "feather", "h5", "hdf5", "npy", "npz", "pkl", "pickle", "bin", "dat", "log", "bak"),
    "3D & design": ("glb", "gltf", "obj", "fbx", "blend", "stl", "fig", "sketch", "drawio", "3ds", "dae", "usdz"),
    "Models & ML": ("safetensors", "ckpt", "pt", "pth", "onnx", "gguf", "tflite", "pb"),
    "Fonts": ("ttf", "otf", "woff", "woff2"),
}
_EXT_LOOKUP = {ext: cat for cat, exts in EXT_CATEGORIES.items() for ext in exts}


def category_of(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT_LOOKUP.get(ext, "Other")


def human_size(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    v = float(n)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{int(v)} B" if i == 0 else f"{v:.1f} {units[i]}"
