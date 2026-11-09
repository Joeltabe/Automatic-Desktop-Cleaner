"""File inspection and rule matching."""
from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Rule


@dataclass(frozen=True)
class FileInfo:
    path: Path
    name: str
    ext: str          # lower-case, without the dot; "" when none
    size: int
    mtime: float
    is_dir: bool

    @staticmethod
    def from_path(path: Path) -> "FileInfo":
        st = path.stat()
        is_dir = path.is_dir()
        ext = "" if is_dir else path.suffix.lower().lstrip(".")
        return FileInfo(path=path, name=path.name, ext=ext, size=0 if is_dir else st.st_size,
                        mtime=st.st_mtime, is_dir=is_dir)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.mtime)

    @property
    def age_days(self) -> float:
        return self.age_seconds / 86400.0


def is_ignored(name: str, ignore_patterns: list[str]) -> bool:
    lowered = name.lower()
    for pattern in ignore_patterns:
        if fnmatch.fnmatchcase(lowered, pattern.lower()):
            return True
    return False


_REGEX_CACHE: dict[str, re.Pattern[str] | None] = {}


def _compiled(regex: str) -> re.Pattern[str] | None:
    if regex not in _REGEX_CACHE:
        try:
            _REGEX_CACHE[regex] = re.compile(regex, re.IGNORECASE)
        except re.error:
            _REGEX_CACHE[regex] = None
    return _REGEX_CACHE[regex]


def rule_matches(rule: Rule, info: FileInfo) -> bool:
    """A rule matches when every configured criterion is satisfied.

    Extensions and patterns are alternatives *within* their own criterion
    (any extension in the list; any pattern in the list), but criteria are
    combined with AND: a rule with both extensions and patterns only matches
    files that satisfy both.
    """
    m = rule.match
    if not (m.extensions or m.patterns or m.regex):
        return False
    if m.extensions and info.ext not in m.extensions:
        return False
    if m.patterns:
        lowered = info.name.lower()
        if not any(fnmatch.fnmatchcase(lowered, p.lower()) for p in m.patterns):
            return False
    if m.regex:
        compiled = _compiled(m.regex)
        if compiled is None or not compiled.search(info.name):
            return False
    if m.min_age_days and info.age_days < m.min_age_days:
        return False
    size_mb = info.size / (1024 * 1024)
    if m.min_size_mb is not None and size_mb < m.min_size_mb:
        return False
    if m.max_size_mb is not None and size_mb > m.max_size_mb:
        return False
    return True


def first_matching_rule(rules: list[Rule], info: FileInfo) -> Rule | None:
    for rule in rules:
        if rule.enabled and rule_matches(rule, info):
            return rule
    return None


def resolve_destination_dir(rule_destination: str, date_mode: str, source_dir: Path, info: FileInfo | None = None) -> Path:
    """Where a file goes. Relative destinations live inside the source folder."""
    raw = os.path.expandvars(rule_destination)
    dest = Path(raw).expanduser()
    if not dest.is_absolute():
        dest = source_dir / dest
    if info is not None and date_mode != "none":
        stamp = datetime.fromtimestamp(info.mtime)
        if date_mode == "year":
            dest = dest / f"{stamp.year:04d}"
        elif date_mode == "year-month":
            dest = dest / f"{stamp.year:04d}" / f"{stamp.month:02d}"
    return dest


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"
