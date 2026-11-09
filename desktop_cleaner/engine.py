"""Planning (dry run) and applying moves, with conflict handling and journaling."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, Rule
from .journal import Batch, Journal, MoveRecord, now_iso
from .rules import FileInfo, first_matching_rule, is_ignored, resolve_destination_dir


@dataclass
class Action:
    source: str
    destination: str  # planned full destination path (before conflict resolution)
    rule_id: str
    rule_name: str
    rule_color: str
    size: int
    mtime: float
    source_dir: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Skip:
    path: str
    reason: str
    is_dir: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    skipped: list[Skip] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    @property
    def bytes_total(self) -> int:
        return sum(a.size for a in self.actions)

    def by_rule(self) -> dict[str, list[Action]]:
        grouped: dict[str, list[Action]] = {}
        for a in self.actions:
            grouped.setdefault(a.rule_name, []).append(a)
        return grouped

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions": [a.to_dict() for a in self.actions],
            "skipped": [s.to_dict() for s in self.skipped],
            "sources": self.sources,
            "missing_sources": self.missing_sources,
            "created_at": self.created_at,
            "bytes_total": self.bytes_total,
        }


@dataclass
class ApplyResult:
    batch: Batch
    failed: list[tuple[Action, str]] = field(default_factory=list)
    skipped_conflicts: list[Action] = field(default_factory=list)

    @property
    def moved(self) -> list[MoveRecord]:
        return self.batch.moves

    @property
    def bytes_moved(self) -> int:
        return self.batch.bytes_moved

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch.batch_id,
            "timestamp": self.batch.timestamp,
            "moved": [asdict(m) for m in self.batch.moves],
            "failed": [{"action": a.to_dict(), "error": err} for a, err in self.failed],
            "skipped_conflicts": [a.to_dict() for a in self.skipped_conflicts],
            "bytes_moved": self.bytes_moved,
        }


# --------------------------------------------------------------------------- planning


def _unmatched_rule(cfg: Config) -> Rule | None:
    dest = cfg.options.unmatched_destination
    if not dest:
        return None
    return Rule(id="__unmatched__", name="Other", destination=dest, color="#9ca3af")


def build_plan(cfg: Config) -> Plan:
    """Compute what *would* move. Never touches the filesystem beyond reading."""
    plan = Plan(sources=[str(p) for p in cfg.source_paths()])
    rules = cfg.enabled_rules()
    catch_all = _unmatched_rule(cfg)
    opts = cfg.options

    for source_dir in cfg.source_paths():
        if not source_dir.is_dir():
            plan.missing_sources.append(str(source_dir))
            continue
        # Destination folders that live inside the source must never be moved themselves.
        protected_dirs = {
            resolve_destination_dir(r.destination, "none", source_dir).resolve() for r in rules + ([catch_all] if catch_all else [])
        }
        try:
            entries = sorted(source_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            plan.missing_sources.append(f"{source_dir} ({exc.strerror or exc})")
            continue

        for entry in entries:
            try:
                info = FileInfo.from_path(entry)
            except OSError:
                plan.skipped.append(Skip(str(entry), "unreadable"))
                continue

            if is_ignored(info.name, opts.ignore):
                plan.skipped.append(Skip(str(entry), "ignored by pattern", info.is_dir))
                continue
            if info.is_dir:
                if not opts.move_folders:
                    plan.skipped.append(Skip(str(entry), "folder (folders are not moved)", True))
                    continue
                if entry.resolve() in protected_dirs:
                    plan.skipped.append(Skip(str(entry), "destination folder", True))
                    continue
            if opts.skip_recent_seconds and info.age_seconds < opts.skip_recent_seconds:
                plan.skipped.append(Skip(str(entry), f"modified in the last {opts.skip_recent_seconds}s", info.is_dir))
                continue

            rule = first_matching_rule(rules, info)
            if rule is None and catch_all is not None and not info.is_dir:
                rule = catch_all
            if rule is None:
                plan.skipped.append(Skip(str(entry), "no matching rule", info.is_dir))
                continue

            dest_dir = resolve_destination_dir(rule.destination, rule.date_subfolders, source_dir, info)
            if dest_dir.resolve() == entry.parent.resolve():
                plan.skipped.append(Skip(str(entry), "already in place", info.is_dir))
                continue
            plan.actions.append(
                Action(
                    source=str(entry),
                    destination=str(dest_dir / info.name),
                    rule_id=rule.id,
                    rule_name=rule.name,
                    rule_color=rule.color,
                    size=info.size,
                    mtime=info.mtime,
                    source_dir=str(source_dir),
                )
            )
    return plan


# --------------------------------------------------------------------------- applying


def resolve_conflict(dest: Path, strategy: str) -> Path | None:
    """Return the path to write to, or None when the move should be skipped."""
    if not dest.exists():
        return dest
    if strategy == "skip":
        return None
    if strategy == "overwrite":
        return dest
    stem, suffix = dest.stem, dest.suffix
    for i in range(1, 10_000):
        candidate = dest.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    return None


ProgressFn = Callable[[int, int, Action], None]


def apply_plan(
    plan: Plan,
    cfg: Config,
    journal: Journal | None = None,
    trigger: str = "manual",
    progress: ProgressFn | None = None,
    only: set[str] | None = None,
) -> ApplyResult:
    """Execute the plan. ``only`` restricts to the given source paths (for partial applies)."""
    batch = Batch.new(plan.sources, trigger=trigger)
    result = ApplyResult(batch=batch)
    actions = [a for a in plan.actions if only is None or a.source in only]
    total = len(actions)

    for index, action in enumerate(actions, start=1):
        src = Path(action.source)
        if not src.exists():
            result.failed.append((action, "file disappeared before it could be moved"))
            continue
        planned = Path(action.destination)
        try:
            planned.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.failed.append((action, f"could not create destination: {exc}"))
            continue
        final = resolve_conflict(planned, cfg.options.conflict)
        if final is None:
            result.skipped_conflicts.append(action)
            continue
        try:
            if cfg.options.conflict == "overwrite" and final.exists():
                if final.is_dir():
                    shutil.rmtree(final)
                else:
                    os.remove(final)
            shutil.move(str(src), str(final))
        except OSError as exc:
            result.failed.append((action, exc.strerror or str(exc)))
            continue
        batch.moves.append(
            MoveRecord(
                src=str(src),
                dest=str(final),
                rule_id=action.rule_id,
                rule_name=action.rule_name,
                size=action.size,
                renamed=final != planned,
            )
        )
        if progress:
            progress(index, total, action)

    if journal is not None and batch.moves:
        journal.append_batch(batch)
    return result


def run(cfg: Config, journal: Journal | None = None, trigger: str = "manual", dry_run: bool = False) -> tuple[Plan, ApplyResult | None]:
    plan = build_plan(cfg)
    if dry_run or not plan.actions:
        return plan, None
    return plan, apply_plan(plan, cfg, journal=journal, trigger=trigger)
