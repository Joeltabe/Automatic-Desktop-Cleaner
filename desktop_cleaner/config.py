"""Configuration model, defaults, persistence and migration from the v1 config.json."""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "DesktopCleaner"
CONFIG_VERSION = 2

CONFLICT_STRATEGIES = ("rename", "skip", "overwrite")
DATE_SUBFOLDER_MODES = ("none", "year", "year-month")


def config_dir() -> Path:
    """Per-user directory that holds config.json and the undo journal."""
    override = os.environ.get("DESKTOP_CLEANER_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "desktop-cleaner"


def default_config_path() -> Path:
    return config_dir() / "config.json"


def journal_path() -> Path:
    return config_dir() / "journal.jsonl"


@dataclass
class Match:
    extensions: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    regex: str | None = None
    min_age_days: int = 0
    min_size_mb: float | None = None
    max_size_mb: float | None = None

    def normalized(self) -> Match:
        self.extensions = sorted({e.lower().lstrip(".").strip() for e in self.extensions if e and e.strip()})
        self.patterns = [p.strip() for p in self.patterns if p and p.strip()]
        self.regex = (self.regex or "").strip() or None
        self.min_age_days = max(0, int(self.min_age_days or 0))
        self.min_size_mb = float(self.min_size_mb) if self.min_size_mb not in (None, "") else None
        self.max_size_mb = float(self.max_size_mb) if self.max_size_mb not in (None, "") else None
        return self


@dataclass
class Rule:
    id: str
    name: str
    destination: str
    match: Match = field(default_factory=Match)
    enabled: bool = True
    date_subfolders: str = "none"
    color: str = "#6c8cff"

    @staticmethod
    def new(
        name: str,
        destination: str,
        extensions: list[str] | None = None,
        color: str = "#6c8cff",
        **match_kwargs: Any,
    ) -> Rule:
        return Rule(
            id=uuid.uuid4().hex[:8],
            name=name,
            destination=destination,
            match=Match(extensions=list(extensions or []), **match_kwargs).normalized(),
            color=color,
        )


@dataclass
class Options:
    conflict: str = "rename"
    ignore: list[str] = field(
        default_factory=lambda: [
            "desktop.ini",
            "Thumbs.db",
            ".DS_Store",
            "*.lnk",
            "*.url",
            ".*",
            "*.tmp",
            "*.crdownload",
            "*.part",
            "*.download",
            "~$*",
        ]
    )
    skip_recent_seconds: int = 30
    move_folders: bool = False
    watch_interval_seconds: int = 5
    unmatched_destination: str | None = None
    notifications: bool = True
    theme: str = "dark"


@dataclass
class SpaceOptions:
    """Settings for the free-up-space features (junk analysis, duplicates, storage map)."""

    project_roots: list[str] = field(default_factory=list)  # empty = auto-detect common code folders
    stale_project_days: int = 60
    installer_age_days: int = 30
    large_file_mb: int = 100
    old_download_days: int = 90
    duplicate_roots: list[str] = field(default_factory=list)  # empty = sources + Documents/Pictures
    duplicate_min_mb: float = 1.0
    exclude: list[str] = field(
        default_factory=lambda: [
            ".git",
            "node_modules",
            ".venv",
            "venv",
            "__pycache__",
            "$RECYCLE.BIN",
            "System Volume Information",
            "Windows",
            "Program Files",
            "Program Files (x86)",
            "ProgramData",
            "AppData",
        ]
    )
    permanent_delete: bool = False  # default: review items go to the recycle bin


@dataclass
class Config:
    version: int = CONFIG_VERSION
    sources: list[str] = field(default_factory=lambda: ["~/Desktop"])
    rules: list[Rule] = field(default_factory=list)
    options: Options = field(default_factory=Options)
    space: SpaceOptions = field(default_factory=SpaceOptions)

    def enabled_rules(self) -> list[Rule]:
        return [r for r in self.rules if r.enabled]

    def source_paths(self) -> list[Path]:
        return [Path(os.path.expandvars(s)).expanduser() for s in self.sources]


# --------------------------------------------------------------------------- defaults


def default_rules() -> list[Rule]:
    return [
        Rule.new(
            "Screenshots",
            "Images/Screenshots",
            ["png", "jpg", "jpeg"],
            "#ff9a7a",
            patterns=["Screenshot*", "Capture*", "Screen Shot*", "Snip*"],
        ),
        Rule.new("Images", "Images", ["jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "svg", "tiff", "ico"], "#ff7a9a"),
        Rule.new("PDFs", "Documents/PDFs", ["pdf"], "#ff6b6b"),
        Rule.new("Documents", "Documents", ["doc", "docx", "odt", "rtf", "txt", "md", "pages"], "#6c8cff"),
        Rule.new("Spreadsheets", "Documents/Spreadsheets", ["xls", "xlsx", "csv", "ods", "numbers"], "#3ddc84"),
        Rule.new("Presentations", "Documents/Presentations", ["ppt", "pptx", "key", "odp"], "#ffb347"),
        Rule.new("Videos", "Videos", ["mp4", "mkv", "mov", "avi", "webm", "wmv", "m4v"], "#c084fc"),
        Rule.new("Music", "Music", ["mp3", "wav", "flac", "aac", "ogg", "m4a"], "#38bdf8"),
        Rule.new("Archives", "Archives", ["zip", "rar", "7z", "tar", "gz", "bz2", "xz"], "#a3a3a3"),
        Rule.new("Installers", "Installers", ["exe", "msi", "dmg", "pkg", "deb", "rpm", "appimage", "apk"], "#f472b6"),
        Rule.new(
            "Code", "Code", ["py", "js", "ts", "html", "css", "json", "java", "c", "cpp", "cs", "go", "rs", "sh", "sql", "ipynb"], "#34d399"
        ),
        Rule.new("3D & Design", "Design", ["glb", "gltf", "obj", "fbx", "blend", "stl", "psd", "ai", "fig", "drawio", "sketch"], "#fbbf24"),
        Rule.new("Fonts", "Fonts", ["ttf", "otf", "woff", "woff2"], "#e879f9"),
        Rule.new("Subtitles & Data", "Documents/Misc", ["srt", "vtt", "xml", "yaml", "yml", "log"], "#94a3b8"),
    ]


def default_config() -> Config:
    return Config(rules=default_rules())


# --------------------------------------------------------------------------- (de)serialisation


def config_to_dict(cfg: Config) -> dict[str, Any]:
    return asdict(cfg)


def _is_legacy(data: dict[str, Any]) -> bool:
    rules = data.get("rules")
    return "version" not in data and isinstance(rules, list) and bool(rules) and "file_type" in rules[0]


def config_from_dict(data: dict[str, Any]) -> Config:
    if _is_legacy(data):
        return migrate_legacy(data)
    rules: list[Rule] = []
    for raw in data.get("rules", []) or []:
        match_raw = dict(raw.get("match") or {})
        known = {k: match_raw[k] for k in Match.__dataclass_fields__ if k in match_raw}
        match = Match(**known).normalized()
        mode = raw.get("date_subfolders")
        rules.append(
            Rule(
                id=str(raw.get("id") or uuid.uuid4().hex[:8]),
                name=str(raw.get("name") or raw.get("destination") or "Rule"),
                destination=str(raw.get("destination") or "Miscellaneous"),
                match=match,
                enabled=bool(raw.get("enabled", True)),
                date_subfolders=mode if mode in DATE_SUBFOLDER_MODES else "none",
                color=str(raw.get("color") or "#6c8cff"),
            )
        )
    opt_raw = dict(data.get("options") or {})
    known_opts = {k: opt_raw[k] for k in Options.__dataclass_fields__ if k in opt_raw}
    options = Options(**known_opts)
    if options.conflict not in CONFLICT_STRATEGIES:
        options.conflict = "rename"
    options.skip_recent_seconds = max(0, int(options.skip_recent_seconds or 0))
    options.watch_interval_seconds = max(1, int(options.watch_interval_seconds or 1))
    options.ignore = [str(p) for p in (options.ignore or [])]
    if options.unmatched_destination is not None and not str(options.unmatched_destination).strip():
        options.unmatched_destination = None
    sources = [str(s) for s in (data.get("sources") or ["~/Desktop"])]
    space_raw = dict(data.get("space") or {})
    known_space = {k: space_raw[k] for k in SpaceOptions.__dataclass_fields__ if k in space_raw}
    space = SpaceOptions(**known_space)
    space.project_roots = [str(p) for p in (space.project_roots or [])]
    space.duplicate_roots = [str(p) for p in (space.duplicate_roots or [])]
    space.exclude = [str(p) for p in (space.exclude or [])]
    space.stale_project_days = max(0, int(space.stale_project_days or 0))
    space.installer_age_days = max(0, int(space.installer_age_days or 0))
    space.old_download_days = max(0, int(space.old_download_days or 0))
    space.large_file_mb = max(1, int(space.large_file_mb or 1))
    space.duplicate_min_mb = max(0.0, float(space.duplicate_min_mb or 0))
    return Config(version=CONFIG_VERSION, sources=sources, rules=rules, options=options, space=space)


def migrate_legacy(data: dict[str, Any]) -> Config:
    """Convert the v1 ``{"rules": [{"file_type", "destination_folder"}]}`` shape."""
    grouped: dict[str, list[str]] = {}
    for raw in data.get("rules", []):
        ext = str(raw.get("file_type", "")).lower().lstrip(".")
        dest = str(raw.get("destination_folder") or "Miscellaneous")
        if ext:
            grouped.setdefault(dest, []).append(ext)
    palette = ["#6c8cff", "#ff7a9a", "#3ddc84", "#ffb347", "#c084fc", "#38bdf8", "#f472b6", "#34d399"]
    rules = [
        Rule.new(dest.replace("/", " / ").title(), dest, exts, palette[i % len(palette)]) for i, (dest, exts) in enumerate(grouped.items())
    ]
    return Config(rules=rules)


def load_config(path: Path | None = None) -> Config:
    """Load the config at ``path`` (or the default location). A missing file yields defaults."""
    p = Path(path) if path else default_config_path()
    if not p.exists():
        return default_config()
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return config_from_dict(data)


def save_config(cfg: Config, path: Path | None = None) -> Path:
    p = Path(path) if path else default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(config_to_dict(cfg), fh, indent=2)
    os.replace(tmp, p)
    return p


def ensure_config(path: Path | None = None) -> tuple[Config, Path, bool]:
    """Load the config, writing defaults if nothing exists yet. Returns (cfg, path, created)."""
    p = Path(path) if path else default_config_path()
    if p.exists():
        return load_config(p), p, False
    cfg = default_config()
    save_config(cfg, p)
    return cfg, p, True
