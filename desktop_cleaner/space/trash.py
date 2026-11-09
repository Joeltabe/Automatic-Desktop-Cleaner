"""Cross-platform "move to recycle bin" plus permanent deletion that tolerates locked files.

No third-party dependency: Windows uses the shell API through ctypes, macOS asks Finder,
Linux follows the freedesktop trash specification.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import stat
import subprocess
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from .common import IS_MAC, IS_WIN, home


@dataclass
class RemoveResult:
    removed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    freed: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


# --------------------------------------------------------------------------- recycle bin: Windows

if IS_WIN:
    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    class _SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]

    _FO_DELETE = 3
    _FOF_SILENT = 0x0004
    _FOF_NOCONFIRMATION = 0x0010
    _FOF_ALLOWUNDO = 0x0040
    _FOF_NOERRORUI = 0x0400
    _FOF_NO_CONNECTED_ELEMENTS = 0x2000

    def _win_trash(paths: list[str]) -> int:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        op = _SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = _FO_DELETE
        op.pFrom = "\0".join(os.path.abspath(p) for p in paths) + "\0\0"
        op.pTo = None
        op.fFlags = _FOF_SILENT | _FOF_NOCONFIRMATION | _FOF_ALLOWUNDO | _FOF_NOERRORUI | _FOF_NO_CONNECTED_ELEMENTS
        rc = shell32.SHFileOperationW(ctypes.byref(op))
        if rc != 0 or op.fAnyOperationsAborted:
            raise OSError(rc, f"SHFileOperation failed with code {rc}")
        return rc

    def recycle_bin_info() -> tuple[int, int]:
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(_SHQUERYRBINFO)
        rc = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))  # type: ignore[attr-defined]
        if rc != 0:
            return 0, 0
        return int(info.i64Size), int(info.i64NumItems)

    def empty_recycle_bin() -> bool:
        flags = 0x1 | 0x2 | 0x4  # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
        rc = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)  # type: ignore[attr-defined]
        return rc in (0, -2147418113)  # S_OK, or E_UNEXPECTED when it was already empty

else:
    def _trash_dirs() -> tuple[Path, Path]:
        if IS_MAC:
            t = home() / ".Trash"
            return t, t
        base = Path(os.environ.get("XDG_DATA_HOME") or home() / ".local" / "share") / "Trash"
        return base / "files", base / "info"

    def recycle_bin_info() -> tuple[int, int]:
        files_dir, _ = _trash_dirs()
        if not files_dir.is_dir():
            return 0, 0
        from .common import dir_size
        size, _ = dir_size(files_dir)
        try:
            count = sum(1 for _ in files_dir.iterdir())
        except OSError:
            count = 0
        return size, count

    def empty_recycle_bin() -> bool:
        files_dir, info_dir = _trash_dirs()
        ok = True
        for d in {files_dir, info_dir}:
            if not d.is_dir():
                continue
            for child in d.iterdir():
                res = delete_permanently(str(child))
                ok = ok and res.ok
        return ok

    def _mac_trash(paths: list[str]) -> None:
        items = ", ".join(f'POSIX file "{p}"' for p in paths)
        script = f'tell application "Finder" to delete {{{items}}}'
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=120)

    def _linux_trash(path: str) -> None:
        files_dir, info_dir = _trash_dirs()
        files_dir.mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)
        src = Path(path)
        name = src.name
        target = files_dir / name
        n = 1
        while target.exists() or (info_dir / f"{target.name}.trashinfo").exists():
            target = files_dir / f"{src.stem} ({n}){src.suffix}"
            n += 1
        info = (f"[Trash Info]\nPath={urllib.parse.quote(str(src.resolve()))}\n"
                f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        (info_dir / f"{target.name}.trashinfo").write_text(info, encoding="utf-8")
        shutil.move(str(src), str(target))


# --------------------------------------------------------------------------- public API

def send_to_trash(paths: list[str] | str) -> RemoveResult:
    """Move files/folders to the recycle bin. Sizes are measured first so ``freed`` is meaningful."""
    from .common import path_size
    items = [paths] if isinstance(paths, str) else list(paths)
    res = RemoveResult()
    sizes = {p: path_size(p)[0] for p in items}
    existing = [p for p in items if os.path.lexists(p)]
    for p in items:
        if p not in existing:
            res.failed.append((p, "no longer exists"))
    if not existing:
        return res
    if IS_WIN:
        # Batch when possible, fall back to one-by-one so a single locked file does not sink the lot.
        try:
            _win_trash(existing)
            for p in existing:
                res.removed.append(p)
                res.freed += sizes[p]
            return res
        except OSError:
            pass
        for p in existing:
            try:
                _win_trash([p])
                res.removed.append(p)
                res.freed += sizes[p]
            except OSError as exc:
                res.failed.append((p, _describe(exc)))
    elif IS_MAC:
        try:
            _mac_trash(existing)
            for p in existing:
                res.removed.append(p)
                res.freed += sizes[p]
        except (subprocess.SubprocessError, OSError):
            for p in existing:
                try:
                    _mac_trash([p])
                    res.removed.append(p)
                    res.freed += sizes[p]
                except (subprocess.SubprocessError, OSError) as exc:
                    res.failed.append((p, _describe(exc)))
    else:
        for p in existing:
            try:
                _linux_trash(p)
                res.removed.append(p)
                res.freed += sizes[p]
            except OSError as exc:
                res.failed.append((p, _describe(exc)))
    return res


def _on_rm_error(func, path, exc_info) -> None:  # type: ignore[no-untyped-def]
    """Retry read-only files once; anything else is collected by the caller through a re-measure."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def delete_permanently(paths: list[str] | str) -> RemoveResult:
    """Delete for good. Locked or protected files are skipped and reported, the rest still goes.

    ``freed`` is computed as size-before minus size-after so partially removed folders count correctly.
    """
    from .common import path_size
    items = [paths] if isinstance(paths, str) else list(paths)
    res = RemoveResult()
    for p in items:
        if not os.path.lexists(p):
            res.failed.append((p, "no longer exists"))
            continue
        before, _ = path_size(p)
        try:
            if os.path.islink(p) or not os.path.isdir(p):
                try:
                    os.remove(p)
                except PermissionError:
                    os.chmod(p, stat.S_IWRITE)
                    os.remove(p)
            else:
                shutil.rmtree(p, onerror=_on_rm_error)
        except OSError as exc:
            if os.path.lexists(p):
                after, _ = path_size(p)
                res.freed += max(0, before - after)
                res.failed.append((p, _describe(exc)))
                continue
        if os.path.lexists(p):
            after, _ = path_size(p)
            res.freed += max(0, before - after)
            res.failed.append((p, "some files are in use and were left behind"))
        else:
            res.freed += before
            res.removed.append(p)
    return res


def clear_directory_contents(path: str) -> RemoveResult:
    """Permanently delete everything *inside* a folder but keep the folder itself (temp/cache dirs)."""
    res = RemoveResult()
    try:
        children = [os.path.join(path, n) for n in os.listdir(path)]
    except OSError as exc:
        res.failed.append((path, _describe(exc)))
        return res
    sub = delete_permanently(children)
    res.removed.extend(sub.removed)
    res.failed.extend(sub.failed)
    res.freed += sub.freed
    return res


def _describe(exc: BaseException) -> str:
    if isinstance(exc, PermissionError):
        return "in use or access denied"
    msg = str(exc)
    if "code 32" in msg or "being used" in msg:
        return "file is in use"
    return msg[:160]
