"""Duplicate file finder: size buckets, then a quick partial hash, then a full hash. Only true byte-for-byte copies."""

from __future__ import annotations

import hashlib
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import Cancel, ProgressFn, Throttle, category_of, is_reparse_or_link, iter_entries

_PARTIAL = 64 * 1024
_CHUNK = 1024 * 1024


@dataclass
class DupFile:
    path: str
    size: int
    mtime: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": os.path.basename(self.path),
            "size": self.size,
            "mtime": self.mtime,
            "folder": os.path.dirname(self.path),
        }


@dataclass
class DupGroup:
    digest: str
    size: int
    files: list[DupFile] = field(default_factory=list)

    @property
    def reclaimable(self) -> int:
        return self.size * max(0, len(self.files) - 1)

    def suggested_keep(self) -> str:
        """Keep the oldest copy (most likely the original); ties broken by the shortest path."""
        return min(self.files, key=lambda f: (f.mtime, len(f.path))).path

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "size": self.size,
            "copies": len(self.files),
            "reclaimable": self.reclaimable,
            "keep": self.suggested_keep(),
            "category": category_of(self.files[0].path),
            "files": [f.to_dict() for f in sorted(self.files, key=lambda f: (f.mtime, f.path))],
        }


@dataclass
class DupResult:
    groups: list[DupGroup]
    scanned_files: int
    scanned_bytes: int
    hashed_bytes: int
    duration: float
    roots: list[str]

    @property
    def reclaimable(self) -> int:
        return sum(g.reclaimable for g in self.groups)

    def to_dict(self, max_groups: int = 500) -> dict[str, Any]:
        groups = sorted(self.groups, key=lambda g: -g.reclaimable)
        return {
            "groups": [g.to_dict() for g in groups[:max_groups]],
            "total_groups": len(groups),
            "reclaimable": self.reclaimable,
            "duplicates": sum(len(g.files) - 1 for g in groups),
            "scanned_files": self.scanned_files,
            "scanned_bytes": self.scanned_bytes,
            "hashed_bytes": self.hashed_bytes,
            "duration": round(self.duration, 2),
            "roots": self.roots,
        }


def _hash(path: str, partial: bool, cancel: Cancel | None, counter: list[int]) -> str | None:
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as fh:
            if partial:
                h.update(fh.read(_PARTIAL))
                counter[0] += _PARTIAL
            else:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
                    counter[0] += len(chunk)
                    if cancel is not None:
                        cancel.check()
    except OSError:
        return None
    return h.hexdigest()


def find_duplicates(
    roots: list[Path | str],
    min_bytes: int = 1024 * 1024,
    exclude_names: set[str] | None = None,
    cancel: Cancel | None = None,
    progress: ProgressFn | None = None,
) -> DupResult:
    t0 = time.monotonic()
    emit = Throttle(progress, 0.12)
    excl = {n.lower() for n in (exclude_names or set())}
    by_size: dict[int, list[DupFile]] = defaultdict(list)
    seen_paths: set[str] = set()
    scanned = 0
    scanned_bytes = 0
    root_paths = [str(Path(os.path.expandvars(str(r))).expanduser()) for r in roots]

    for root in root_paths:
        stack = [root]
        while stack:
            cur = stack.pop()
            for entry in iter_entries(cur):
                scanned += 1
                if cancel is not None and scanned % 256 == 0:
                    cancel.check()
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not is_reparse_or_link(entry) and entry.name.lower() not in excl:
                            stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        st = entry.stat(follow_symlinks=False)
                        if st.st_size < min_bytes:
                            continue
                        key = os.path.normcase(os.path.realpath(entry.path))
                        if key in seen_paths:
                            continue
                        seen_paths.add(key)
                        scanned_bytes += st.st_size
                        by_size[st.st_size].append(DupFile(entry.path, st.st_size, st.st_mtime))
                except OSError:
                    continue
                if scanned % 128 == 0:
                    emit({"phase": "collect", "files": scanned, "candidates": sum(len(v) for v in by_size.values() if len(v) > 1)})

    candidates = [files for files in by_size.values() if len(files) > 1]
    to_hash_bytes = sum(f.size for files in candidates for f in files)
    counter = [0]
    groups: list[DupGroup] = []
    done_files = 0
    total_files = sum(len(files) for files in candidates)
    for files in candidates:
        if cancel is not None:
            cancel.check()
        partial: dict[str, list[DupFile]] = defaultdict(list)
        for f in files:
            d = _hash(f.path, True, cancel, counter)
            if d:
                partial[d].append(f)
            done_files += 1
            emit(
                {
                    "phase": "hash",
                    "done": done_files,
                    "total": total_files,
                    "bytes": counter[0],
                    "total_bytes": to_hash_bytes,
                    "current": f.path,
                }
            )
        for sub in partial.values():
            if len(sub) < 2:
                continue
            if sub[0].size <= _PARTIAL:  # partial hash covered the whole file
                groups.append(DupGroup(digest=_hash(sub[0].path, True, cancel, [0]) or "", size=sub[0].size, files=sub))
                continue
            full: dict[str, list[DupFile]] = defaultdict(list)
            for f in sub:
                d = _hash(f.path, False, cancel, counter)
                if d:
                    full[d].append(f)
                emit(
                    {
                        "phase": "hash",
                        "done": done_files,
                        "total": total_files,
                        "bytes": counter[0],
                        "total_bytes": to_hash_bytes,
                        "current": f.path,
                    }
                )
            for digest, same in full.items():
                if len(same) > 1:
                    groups.append(DupGroup(digest=digest, size=same[0].size, files=same))
    emit({"phase": "done", "groups": len(groups)}, force=True)
    return DupResult(
        groups=groups,
        scanned_files=scanned,
        scanned_bytes=scanned_bytes,
        hashed_bytes=counter[0],
        duration=time.monotonic() - t0,
        roots=root_paths,
    )
