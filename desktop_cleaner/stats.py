"""Aggregate statistics derived from the journal."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .journal import Batch, Journal


def _day_of(ts: str) -> date | None:
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return None


def summarize(journal: Journal, days: int = 30) -> dict[str, Any]:
    batches: list[Batch] = [b for b in journal.batches() if not b.undone]
    all_batches = journal.batches()

    files = sum(len(b.moves) for b in batches)
    size = sum(b.bytes_moved for b in batches)
    by_rule: Counter[str] = Counter()
    bytes_by_rule: Counter[str] = Counter()
    for b in batches:
        for m in b.moves:
            by_rule[m.rule_name] += 1
            bytes_by_rule[m.rule_name] += m.size

    today = date.today()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    per_day: dict[date, int] = defaultdict(int)
    for b in batches:
        d = _day_of(b.timestamp)
        if d is not None:
            per_day[d] += len(b.moves)
    timeline = [{"date": d.isoformat(), "files": per_day.get(d, 0)} for d in window]

    streak = 0
    for d in reversed(window):
        if per_day.get(d, 0) > 0:
            streak += 1
        elif d != today:
            break

    last = all_batches[-1] if all_batches else None
    return {
        "files_moved": files,
        "bytes_moved": size,
        "runs": len(batches),
        "undone_runs": sum(1 for b in all_batches if b.undone),
        "by_rule": [
            {"rule": name, "files": count, "bytes": bytes_by_rule[name]}
            for name, count in by_rule.most_common()
        ],
        "timeline": timeline,
        "streak_days": streak,
        "last_run": None if last is None else {
            "batch_id": last.batch_id, "timestamp": last.timestamp, "files": len(last.moves),
            "bytes": last.bytes_moved, "undone": last.undone, "trigger": last.trigger,
        },
    }
