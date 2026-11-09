"""Volumes and their usage."""

from __future__ import annotations

import ctypes
import os
import shutil
from pathlib import Path
from typing import Any

from .common import IS_MAC, IS_WIN, home


def _win_volumes() -> list[dict[str, Any]]:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mask = kernel32.GetLogicalDrives()
    out = []
    system_drive = (os.environ.get("SystemDrive") or "C:").upper()
    home_drive = str(home())[:2].upper()
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = f"{chr(65 + i)}:"
        root = letter + "\\"
        kind = kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))
        # 2 removable, 3 fixed, 4 remote, 5 cdrom, 6 ramdisk
        if kind not in (2, 3, 4):
            continue
        label_buf = ctypes.create_unicode_buffer(261)
        fs_buf = ctypes.create_unicode_buffer(261)
        ok = kernel32.GetVolumeInformationW(ctypes.c_wchar_p(root), label_buf, 261, None, None, None, fs_buf, 261)
        label = label_buf.value if ok else ""
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        out.append(
            {
                "path": root,
                "label": label or ("Windows" if letter.upper() == system_drive else "Local Disk"),
                "kind": {2: "removable", 3: "fixed", 4: "network"}[kind],
                "fs": fs_buf.value if ok else "",
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "is_system": letter.upper() == system_drive,
                "is_home": letter.upper() == home_drive,
            }
        )
    return out


def _posix_volumes() -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(mount: str, label: str, kind: str = "fixed") -> None:
        if mount in seen:
            return
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            return
        if usage.total == 0:
            return
        seen.add(mount)
        out.append(
            {
                "path": mount,
                "label": label,
                "kind": kind,
                "fs": "",
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "is_system": mount == "/",
                "is_home": False,
            }
        )

    add("/", "System")
    if IS_MAC:
        vols = Path("/Volumes")
        if vols.is_dir():
            for v in sorted(vols.iterdir()):
                if v.is_dir() and not v.is_symlink():
                    add(str(v), v.name, "removable")
    else:
        try:
            with open("/proc/mounts", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    mount, fstype = parts[1], parts[2]
                    if fstype in ("ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "ntfs", "ntfs3", "exfat", "vfat", "fuseblk", "zfs"):
                        name = mount.rstrip("/").rsplit("/", 1)[-1] or "root"
                        kind = "removable" if mount.startswith(("/media", "/run/media", "/mnt")) else "fixed"
                        add(mount, name, kind)
        except OSError:
            pass
    # Mark the volume that holds the home directory.
    h = str(home())
    best = max((v for v in out if h.startswith(v["path"].rstrip("/") or "/")), key=lambda v: len(v["path"]), default=None)
    if best:
        best["is_home"] = True
    return out


def list_volumes() -> list[dict[str, Any]]:
    """All mounted volumes with usage, system/home volume first."""
    vols = _win_volumes() if IS_WIN else _posix_volumes()
    for v in vols:
        v["percent"] = round(v["used"] / v["total"] * 100, 1) if v["total"] else 0.0
        v["status"] = "critical" if v["percent"] >= 92 else "warn" if v["percent"] >= 80 else "ok"
    vols.sort(key=lambda v: (not v["is_system"], not v["is_home"], v["path"]))
    return vols
