"""Folder scanner for the storage map: a size tree, the largest files and a breakdown by type."""

from __future__ import annotations

import heapq
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import Cancel, ProgressFn, Throttle, category_of, is_reparse_or_link, iter_entries


@dataclass
class DirNode:
    name: str
    path: str
    size: int = 0
    files: int = 0
    dirs: int = 0
    file_bytes: int = 0  # bytes of files directly inside (not in sub-folders)
    children: list[DirNode] = field(default_factory=list)
    denied: bool = False
    newest_mtime: float = 0.0

    def to_dict(self, depth: int = 2, top: int = 40, min_fraction: float = 0.002, root_size: int | None = None) -> dict[str, Any]:
        root_size = root_size or max(1, self.size)
        d: dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "files": self.files,
            "dirs": self.dirs,
            "file_bytes": self.file_bytes,
            "denied": self.denied,
            "newest_mtime": self.newest_mtime,
        }
        if depth > 0:
            kids = sorted(self.children, key=lambda c: -c.size)
            shown = [c for c in kids[:top] if c.size >= root_size * min_fraction or c is kids[0]]
            rest = kids[len(shown) :]
            d["children"] = [c.to_dict(depth - 1, top, min_fraction, root_size) for c in shown]
            if rest:
                d["other"] = {"count": len(rest), "size": sum(c.size for c in rest)}
        return d

    def find(self, path: str) -> DirNode | None:
        norm = os.path.normcase(os.path.normpath(path))
        if os.path.normcase(os.path.normpath(self.path)) == norm:
            return self
        for c in self.children:
            cp = os.path.normcase(os.path.normpath(c.path))
            if norm == cp or norm.startswith(cp + os.sep):
                return c.find(path)
        return None


@dataclass
class ScanResult:
    root: DirNode
    largest: list[dict[str, Any]]
    by_type: list[dict[str, Any]]
    by_age: list[dict[str, Any]]
    files: int
    dirs: int
    bytes: int
    duration: float
    denied: int
    truncated: bool = False

    def to_dict(self, depth: int = 2) -> dict[str, Any]:
        return {
            "root": self.root.to_dict(depth=depth),
            "largest": self.largest,
            "by_type": self.by_type,
            "by_age": self.by_age,
            "files": self.files,
            "dirs": self.dirs,
            "bytes": self.bytes,
            "duration": round(self.duration, 2),
            "denied": self.denied,
            "truncated": self.truncated,
        }


_AGE_BUCKETS = (("This week", 7), ("This month", 30), ("Last 6 months", 182), ("Last year", 365), ("Older", 10**6))


def scan_tree(
    root: Path | str,
    cancel: Cancel | None = None,
    progress: ProgressFn | None = None,
    top_files: int = 150,
    exclude_names: set[str] | None = None,
    max_entries: int = 6_000_000,
) -> ScanResult:
    """Walk ``root`` once and build everything the storage map needs.

    Symlinks and junctions are never followed. Permission errors mark the node ``denied`` and move on.
    """
    root_path = str(Path(os.path.expandvars(str(root))).expanduser())
    root_node = DirNode(name=os.path.basename(root_path.rstrip("\\/")) or root_path, path=root_path)
    emit = Throttle(progress, 0.12)
    heap: list[tuple[int, str, float]] = []
    by_type: dict[str, list[int]] = {}
    by_age: dict[str, list[int]] = {label: [0, 0] for label, _ in _AGE_BUCKETS}
    started = time.monotonic()
    now = time.time()
    total_files = 0
    total_dirs = 0
    denied = 0
    seen = 0
    truncated = False
    excl = {n.lower() for n in (exclude_names or set())}

    # Iterative post-order: push (node, iterator) pairs so sizes bubble up when a dir is exhausted.
    stack: list[tuple[DirNode, Any]] = [(root_node, iter_entries(root_path))]
    if not os.path.isdir(root_path):
        raise FileNotFoundError(root_path)
    while stack:
        node, it = stack[-1]
        entry = next(it, None)
        if entry is None:
            stack.pop()
            if stack:
                parent = stack[-1][0]
                parent.size += node.size
                parent.files += node.files
                parent.dirs += node.dirs + 1
                parent.newest_mtime = max(parent.newest_mtime, node.newest_mtime)
            continue
        seen += 1
        if cancel is not None and seen % 256 == 0:
            cancel.check()
        if seen > max_entries:
            truncated = True
            break
        try:
            if entry.is_dir(follow_symlinks=False):
                if is_reparse_or_link(entry) or entry.name.lower() in excl:
                    continue
                child = DirNode(name=entry.name, path=entry.path)
                node.children.append(child)
                total_dirs += 1
                # Detect access problems eagerly so the node can be flagged.
                try:
                    child_it = os.scandir(entry.path)
                except PermissionError:
                    child.denied = True
                    denied += 1
                    continue
                except OSError:
                    continue
                stack.append((child, _closing_iter(child_it)))
                if seen % 64 == 0:
                    emit(
                        {
                            "phase": "scan",
                            "files": total_files,
                            "dirs": total_dirs,
                            "bytes": root_node.size + _partial(stack),
                            "current": entry.path,
                        }
                    )
            elif entry.is_file(follow_symlinks=False):
                st = entry.stat(follow_symlinks=False)
                size = st.st_size
                node.size += size
                node.file_bytes += size
                node.files += 1
                node.newest_mtime = max(node.newest_mtime, st.st_mtime)
                total_files += 1
                cat = category_of(entry.name)
                b = by_type.setdefault(cat, [0, 0])
                b[0] += size
                b[1] += 1
                age = (now - st.st_mtime) / 86400.0
                for label, days in _AGE_BUCKETS:
                    if age <= days:
                        by_age[label][0] += size
                        by_age[label][1] += 1
                        break
                if len(heap) < top_files:
                    heapq.heappush(heap, (size, entry.path, st.st_mtime))
                elif size > heap[0][0]:
                    heapq.heapreplace(heap, (size, entry.path, st.st_mtime))
        except OSError:
            continue

    while stack:  # cancelled or truncated mid-way: still roll sizes up so partial data is usable
        node, _ = stack.pop()
        if stack:
            parent = stack[-1][0]
            parent.size += node.size
            parent.files += node.files
            parent.dirs += node.dirs + 1

    largest = [
        {"path": p, "name": os.path.basename(p), "size": s, "mtime": m, "category": category_of(p)}
        for s, p, m in sorted(heap, reverse=True)
    ]
    types = sorted(({"category": k, "bytes": v[0], "files": v[1]} for k, v in by_type.items()), key=lambda d: -d["bytes"])
    ages = [{"label": k, "bytes": v[0], "files": v[1]} for k, v in by_age.items()]
    emit({"phase": "done", "files": total_files, "dirs": total_dirs, "bytes": root_node.size}, force=True)
    return ScanResult(
        root=root_node,
        largest=largest,
        by_type=types,
        by_age=ages,
        files=total_files,
        dirs=total_dirs,
        bytes=root_node.size,
        duration=time.monotonic() - started,
        denied=denied,
        truncated=truncated,
    )


def _closing_iter(scandir_it: Any) -> Any:
    try:
        with scandir_it as it:
            for entry in it:
                yield entry
    except (PermissionError, FileNotFoundError, OSError):
        return


def _partial(stack: list[tuple[DirNode, Any]]) -> int:
    return sum(n.size for n, _ in stack[1:])
