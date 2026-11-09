"""Execute a clean-up selection and keep a log of every clean for statistics."""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..config import config_dir
from .common import Cancel, ProgressFn, Throttle
from .junk import Analysis, JunkGroup, JunkItem
from .trash import RemoveResult, clear_directory_contents, delete_permanently, empty_recycle_bin, recycle_bin_info, send_to_trash


def space_log_path() -> Path:
    return config_dir() / "space_history.jsonl"


@dataclass
class CleanResult:
    clean_id: str
    freed: int = 0
    removed: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    by_group: dict[str, int] = field(default_factory=dict)
    modes: Counter = field(default_factory=Counter)
    duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"clean_id": self.clean_id, "freed": self.freed, "removed": self.removed, "failed": self.failed,
                "by_group": self.by_group, "modes": dict(self.modes), "duration": round(self.duration, 2)}


def mode_for(group: JunkGroup, item: JunkItem, permanent_review: bool) -> str:
    if item.kind == "recycle_bin":
        return "empty_bin"
    if group.confidence == "safe":
        return "contents" if item.kind == "contents" else "delete"
    if group.confidence == "review":
        return "delete" if permanent_review else "trash"
    return "skip"


def execute(analysis: Analysis, groups_all: list[str], paths: list[str], permanent_review: bool = False,
            progress: ProgressFn | None = None, cancel: Cancel | None = None, log: "SpaceLog | None" = None,
            trigger: str = "gui") -> CleanResult:
    """Remove the selection. ``groups_all`` are whole groups, ``paths`` are individual items from any group."""
    t0 = time.monotonic()
    emit = Throttle(progress, 0.05)
    wanted_paths = {os.path.normcase(p) for p in paths}
    todo: list[tuple[JunkGroup, JunkItem]] = []
    for g in analysis.groups:
        if g.confidence == "manual":
            continue
        for item in g.items:
            if g.id in groups_all or os.path.normcase(item.path) in wanted_paths:
                todo.append((g, item))
    result = CleanResult(clean_id=uuid.uuid4().hex[:10])
    total = len(todo)
    trash_batch: list[tuple[JunkGroup, JunkItem]] = []

    def apply(res: RemoveResult, group: JunkGroup, mode: str) -> None:
        result.freed += res.freed
        result.removed += len(res.removed)
        result.by_group[group.id] = result.by_group.get(group.id, 0) + res.freed
        result.modes[mode] += 1
        for p, err in res.failed:
            result.failed.append({"path": p, "error": err, "group": group.id})

    for i, (g, item) in enumerate(todo):
        if cancel is not None:
            cancel.check()
        mode = mode_for(g, item, permanent_review)
        emit({"phase": "clean", "index": i + 1, "total": total, "name": item.name or os.path.basename(item.path), "freed": result.freed}, force=i == 0)
        if mode == "skip":
            continue
        if mode == "empty_bin":
            before, _ = recycle_bin_info()
            ok = empty_recycle_bin()
            after, _ = recycle_bin_info()
            res = RemoveResult(removed=[item.path] if ok else [], failed=[] if ok else [(item.path, "could not empty")], freed=max(0, before - after))
            apply(res, g, mode)
        elif mode == "contents":
            apply(clear_directory_contents(item.path), g, mode)
        elif mode == "delete":
            apply(delete_permanently(item.path), g, mode)
        elif mode == "trash":
            trash_batch.append((g, item))
    if trash_batch:
        emit({"phase": "clean", "index": total, "total": total, "name": "moving to recycle bin", "freed": result.freed}, force=True)
        # Trash per group so freed bytes are attributed correctly.
        per_group: dict[str, list[JunkItem]] = defaultdict(list)
        gmap = {g.id: g for g, _ in trash_batch}
        for g, item in trash_batch:
            per_group[g.id].append(item)
        for gid, items in per_group.items():
            apply(send_to_trash([i.path for i in items]), gmap[gid], "trash")
    result.duration = time.monotonic() - t0
    emit({"phase": "done", "freed": result.freed, "removed": result.removed, "failed": len(result.failed)}, force=True)
    if log is not None:
        log.append(result, trigger=trigger, titles={g.id: g.title for g in analysis.groups})
    return result


def remove_paths(paths: list[str], permanent: bool = False, log: "SpaceLog | None" = None, label: str = "files",
                 trigger: str = "gui") -> CleanResult:
    """Used by the storage map and duplicates screens: remove arbitrary paths, recycle bin by default."""
    t0 = time.monotonic()
    res = delete_permanently(paths) if permanent else send_to_trash(paths)
    result = CleanResult(clean_id=uuid.uuid4().hex[:10], freed=res.freed, removed=len(res.removed),
                         failed=[{"path": p, "error": e, "group": label} for p, e in res.failed],
                         by_group={label: res.freed}, modes=Counter({"delete" if permanent else "trash": len(res.removed)}),
                         duration=time.monotonic() - t0)
    if log is not None:
        log.append(result, trigger=trigger, titles={label: label.replace("_", " ").title()})
    return result


# --------------------------------------------------------------------------- history / stats

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class SpaceLog:
    """Append-only JSONL of clean-ups (what was freed, when, from which groups)."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else space_log_path()

    def append(self, result: CleanResult, trigger: str, titles: dict[str, str]) -> None:
        if result.freed <= 0 and result.removed <= 0:
            return
        rec = {"type": "clean", "clean_id": result.clean_id, "timestamp": _now_iso(), "trigger": trigger,
               "freed": result.freed, "removed": result.removed, "failed": len(result.failed),
               "groups": [{"id": gid, "title": titles.get(gid, gid), "freed": b} for gid, b in result.by_group.items()]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def records(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def clear(self) -> None:
        if self.path.exists():
            os.remove(self.path)

    def summary(self, days: int = 30) -> dict[str, Any]:
        recs = list(self.records())
        total = sum(r.get("freed", 0) for r in recs)
        by_group: dict[str, dict[str, Any]] = {}
        for r in recs:
            for g in r.get("groups", []):
                row = by_group.setdefault(g["id"], {"id": g["id"], "title": g.get("title", g["id"]), "freed": 0})
                row["freed"] += g.get("freed", 0)
        today = date.today()
        window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
        per_day: dict[date, int] = defaultdict(int)
        for r in recs:
            try:
                d = datetime.fromisoformat(r["timestamp"]).date()
            except (KeyError, ValueError):
                continue
            per_day[d] += r.get("freed", 0)
        last = recs[-1] if recs else None
        return {
            "freed_total": total, "cleans": len(recs), "removed_total": sum(r.get("removed", 0) for r in recs),
            "by_group": sorted(by_group.values(), key=lambda x: -x["freed"]),
            "timeline": [{"date": d.isoformat(), "freed": per_day.get(d, 0)} for d in window],
            "last_clean": None if last is None else {"timestamp": last.get("timestamp"), "freed": last.get("freed", 0),
                                                     "removed": last.get("removed", 0), "trigger": last.get("trigger")},
            "recent": [{"timestamp": r.get("timestamp"), "freed": r.get("freed", 0), "removed": r.get("removed", 0),
                        "failed": r.get("failed", 0), "trigger": r.get("trigger"), "groups": r.get("groups", [])}
                       for r in reversed(recs[-25:])],
        }
