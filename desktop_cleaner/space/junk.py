"""Junk analysis: find space that can be reclaimed and grade how safe it is to remove.

Three confidence levels drive what the cleaner does with a group:

* ``safe``   - regenerated automatically (temp files, caches, crash dumps). Deleted permanently,
               because moving a cache into the recycle bin frees nothing.
* ``review`` - recreatable with a command or probably unwanted (node_modules of an idle project,
               installers from months ago, the recycle bin). Sent to the recycle bin unless the user
               opts into permanent deletion.
* ``manual`` - reported only, with the tool that handles it (Windows.old, hibernation file ...).
"""
from __future__ import annotations

import glob
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import Config
from .common import (IS_MAC, IS_WIN, Cancel, ProgressFn, Throttle, age_days, dir_size, env_path, home,
                     is_reparse_or_link, iter_entries, local_appdata, mtime_of, roaming_appdata, xdg_cache)
from .trash import recycle_bin_info

CONFIDENCE_ORDER = {"safe": 0, "review": 1, "manual": 2}
MIN_ITEM_BYTES = 128 * 1024


@dataclass
class JunkItem:
    path: str
    size: int
    count: int = 1
    is_dir: bool = False
    detail: str = ""
    mtime: float = 0.0
    kind: str = "path"        # path | contents (empty the folder, keep it) | recycle_bin
    stale: bool = False
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "name": self.name or os.path.basename(self.path.rstrip("\\/")) or self.path,
                "size": self.size, "count": self.count, "is_dir": self.is_dir, "detail": self.detail,
                "mtime": self.mtime, "kind": self.kind, "stale": self.stale}


@dataclass
class JunkGroup:
    id: str
    title: str
    description: str
    confidence: str
    icon: str
    items: list[JunkItem] = field(default_factory=list)
    duration: float = 0.0
    note: str = ""
    hint: str = ""             # manual groups: the command/tool that handles it
    hint_url: str = ""         # manual groups: something the UI can open (ms-settings:..., https://...)

    @property
    def size(self) -> int:
        return sum(i.size for i in self.items)

    @property
    def count(self) -> int:
        return sum(i.count for i in self.items)

    def to_dict(self, max_items: int = 200) -> dict[str, Any]:
        items = sorted(self.items, key=lambda i: (-int(i.stale), -i.size))
        shown = items[:max_items]
        return {
            "id": self.id, "title": self.title, "description": self.description, "confidence": self.confidence,
            "icon": self.icon, "note": self.note, "hint": self.hint, "hint_url": self.hint_url,
            "size": self.size, "count": self.count, "items": [i.to_dict() for i in shown],
            "more": len(items) - len(shown), "more_size": sum(i.size for i in items[max_items:]),
            "removable": self.confidence != "manual", "duration": round(self.duration, 2),
        }


@dataclass
class Analysis:
    groups: list[JunkGroup]
    duration: float
    started_at: float

    def total(self, confidence: str) -> int:
        return sum(g.size for g in self.groups if g.confidence == confidence)

    def group(self, group_id: str) -> JunkGroup | None:
        return next((g for g in self.groups if g.id == group_id), None)

    def to_dict(self) -> dict[str, Any]:
        groups = sorted(self.groups, key=lambda g: (CONFIDENCE_ORDER[g.confidence], -g.size))
        return {
            "groups": [g.to_dict() for g in groups],
            "safe_bytes": self.total("safe"), "review_bytes": self.total("review"), "manual_bytes": self.total("manual"),
            "reclaimable_bytes": self.total("safe") + self.total("review"),
            "duration": round(self.duration, 2), "started_at": self.started_at,
        }


# --------------------------------------------------------------------------- helpers

Candidate = tuple  # (path, detail, kind, min_bytes)


class _Ctx:
    def __init__(self, cfg: Config, cancel: Cancel | None, progress: ProgressFn | None, workers: int = 16):
        self.cfg = cfg
        self.cancel = cancel
        self.emit = Throttle(progress, 0.1)
        self.step = ""
        self.seen: set[str] = set()
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="junk-size")

    def tick(self, current: str = "") -> None:
        if self.cancel:
            self.cancel.check()
        self.emit({"phase": "junk", "step": self.step, "current": current})

    def claim(self, path: Path | str) -> bool:
        """Reserve a path so no two detectors report the same bytes (also rejects children of claimed folders)."""
        try:
            key = os.path.normcase(os.path.realpath(str(path)))
        except OSError:
            key = os.path.normcase(str(path))
        probe = key
        while True:
            if probe in self.seen:
                return False
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        self.seen.add(key)
        return True

    def _measure(self, path: Path | str, detail: str, kind: str, min_bytes: int) -> JunkItem | None:
        p = Path(path)
        try:
            if p.is_symlink() or not p.exists():
                return None
            if p.is_dir():
                size, count = dir_size(p, self.cancel)
                is_dir = True
            else:
                size, count, is_dir = p.stat().st_size, 1, False
        except OSError:
            return None
        if size < min_bytes or count == 0:
            return None
        return JunkItem(path=str(p), size=size, count=count, is_dir=is_dir, detail=detail, mtime=mtime_of(p), kind=kind)

    def item_for(self, path: Path | str, detail: str = "", kind: str = "path", min_bytes: int = MIN_ITEM_BYTES) -> JunkItem | None:
        if not self.claim(path):
            return None
        return self._measure(path, detail, kind, min_bytes)

    def items_for(self, candidates: list[Candidate]) -> list[JunkItem]:
        """Size many candidates concurrently (directory walks are I/O bound). Order is preserved."""
        claimed = [c for c in candidates if self.claim(c[0])]
        if not claimed:
            return []
        futures = [self.pool.submit(self._measure, *c) for c in claimed]
        out: list[JunkItem] = []
        for f in futures:
            item = f.result()
            if item is not None:
                out.append(item)
            self.tick()
        return out

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


def _existing(*candidates: Path | None) -> list[Path]:
    out: list[Path] = []
    for c in candidates:
        if c is not None and c.exists() and c not in out:
            out.append(c)
    return out


def _globdirs(pattern: str) -> list[Path]:
    return [Path(p) for p in glob.glob(pattern) if os.path.isdir(p)]


def _detail_age(path: Path | str) -> str:
    d = age_days(mtime_of(path))
    return "modified today" if d < 1 else f"modified {int(d)} day{'s' if int(d) != 1 else ''} ago"


def _downloads() -> Path:
    return home() / "Downloads"


# --------------------------------------------------------------------------- detectors

def _temp_files(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Temporary files"
    dirs: list[Path] = []
    if IS_WIN:
        dirs += _existing(env_path("TEMP"), env_path("TMP"), local_appdata() and local_appdata() / "Temp")
        win_temp = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"
        if win_temp.is_dir() and os.access(win_temp, os.W_OK):
            dirs.append(win_temp)
    else:
        tmpdir = os.environ.get("TMPDIR")
        dirs += _existing(Path(tmpdir) if tmpdir else None, Path("/tmp"), Path("/var/tmp"))
    group = JunkGroup("temp", "Temporary files", "Scratch files apps forgot to delete. Anything younger than a day is left alone.",
                      "safe", "temp", note="Files still in use are skipped automatically.")
    seen: set[str] = set()
    cands: list[Candidate] = []
    uid = None if IS_WIN else os.getuid()
    for d in dirs:
        rd = os.path.realpath(d)
        if rd in seen:
            continue
        seen.add(rd)
        for entry in iter_entries(d):
            ctx.tick(entry.path)
            try:
                st = entry.stat(follow_symlinks=False)
                if time.time() - st.st_mtime < 86400:
                    continue
                if uid is not None and st.st_uid != uid:
                    continue
                if is_reparse_or_link(entry):
                    continue
            except OSError:
                continue
            cands.append((entry.path, _detail_age(entry.path), "path", 1))
    group.items.extend(ctx.items_for(cands))
    return group if group.items else None


_CHROMIUM_CACHE_SUBDIRS = ("Cache/Cache_Data", "Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache",
                           "DawnWebGPUCache", "GrShaderCache", "ShaderCache", "Media Cache",
                           "Service Worker/CacheStorage", "Service Worker/ScriptCache", "Storage/ext/*/def/Cache")


def _chromium_user_data_dirs() -> list[tuple[str, Path]]:
    la, ra, h = local_appdata(), roaming_appdata(), home()
    out: list[tuple[str, Path | None]] = []
    if IS_WIN and la and ra:
        out += [("Chrome", la / "Google/Chrome/User Data"), ("Edge", la / "Microsoft/Edge/User Data"),
                ("Brave", la / "BraveSoftware/Brave-Browser/User Data"), ("Vivaldi", la / "Vivaldi/User Data"),
                ("Chromium", la / "Chromium/User Data"), ("Opera", ra / "Opera Software/Opera Stable"),
                ("Opera GX", ra / "Opera Software/Opera GX Stable"), ("Arc", la / "Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4/LocalCache/Local/Arc/User Data")]
    elif IS_MAC:
        c = h / "Library/Caches"
        s = h / "Library/Application Support"
        out += [("Chrome", c / "Google/Chrome"), ("Chrome", s / "Google/Chrome"), ("Edge", c / "Microsoft Edge"),
                ("Edge", s / "Microsoft Edge"), ("Brave", c / "BraveSoftware/Brave-Browser"), ("Brave", s / "BraveSoftware/Brave-Browser"),
                ("Arc", c / "Arc"), ("Arc", s / "Arc/User Data"), ("Vivaldi", c / "Vivaldi"), ("Chromium", c / "Chromium")]
    else:
        c, s = xdg_cache(), Path(os.environ.get("XDG_CONFIG_HOME") or h / ".config")
        out += [("Chrome", c / "google-chrome"), ("Chrome", s / "google-chrome"), ("Edge", c / "microsoft-edge"),
                ("Edge", s / "microsoft-edge"), ("Brave", c / "BraveSoftware/Brave-Browser"), ("Brave", s / "BraveSoftware/Brave-Browser"),
                ("Chromium", c / "chromium"), ("Chromium", s / "chromium"), ("Vivaldi", c / "vivaldi"), ("Opera", c / "opera")]
    return [(n, p) for n, p in out if p is not None and p.is_dir()]


def _browser_caches(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Browser caches"
    group = JunkGroup("browser", "Browser caches", "Pages, scripts and images your browsers keep for faster reloads. They rebuild themselves.",
                      "safe", "browser", note="Close your browsers first for the full amount; files in use are skipped.")
    cands: list[Candidate] = []
    for name, base in _chromium_user_data_dirs():
        profiles = [base] if base.name.startswith("Opera") else [p for p in base.iterdir() if p.is_dir() and (p.name == "Default" or p.name.startswith(("Profile", "Guest", "System")))]
        if not profiles and (base / "Cache").exists():
            profiles = [base]
        for prof in profiles:
            ctx.tick(str(prof))
            for sub in _CHROMIUM_CACHE_SUBDIRS:
                for cache_dir in (_globdirs(str(prof / sub)) if "*" in sub else _existing(prof / sub)):
                    if sub == "Cache" and (cache_dir / "Cache_Data").is_dir():
                        continue  # the Cache_Data child is listed instead
                    cands.append((cache_dir, f"{name} · {prof.name}", "contents", MIN_ITEM_BYTES))
    firefox = []
    if IS_WIN and local_appdata():
        firefox = _globdirs(str(local_appdata() / "Mozilla/Firefox/Profiles/*"))
    elif IS_MAC:
        firefox = _globdirs(str(home() / "Library/Caches/Firefox/Profiles/*"))
    else:
        firefox = _globdirs(str(xdg_cache() / "mozilla/firefox/*"))
    for prof in firefox:
        for sub in ("cache2", "startupCache", "thumbnails", "shader-cache"):
            cands.append((prof / sub, f"Firefox · {prof.name.split('.')[-1]}", "contents", MIN_ITEM_BYTES))
    group.items.extend(ctx.items_for(cands))
    return group if group.items else None


_ELECTRON_CACHE_DIRS = ("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
                        "Service Worker/CacheStorage", "Service Worker/ScriptCache", "CachedData", "CachedExtensionVSIXs",
                        "CachedProfilesData", "logs", "blob_storage")
_BROWSER_DIR_NAMES = {"google", "microsoft", "bravesoftware", "mozilla", "vivaldi", "chromium", "opera software"}


def _app_caches(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Application caches"
    group = JunkGroup("apps", "Application caches", "Electron apps (VS Code, Discord, Slack, Teams, Notion, Figma, Postman ...), media and design tools.",
                      "safe", "apps", note="Apps rebuild these on next launch. Close the app first for the full amount.")
    roots: list[Path] = []
    if IS_WIN:
        roots += _existing(roaming_appdata(), local_appdata(), local_appdata() and local_appdata() / "Programs")
    elif IS_MAC:
        roots += _existing(home() / "Library/Application Support")
    else:
        roots += _existing(Path(os.environ.get("XDG_CONFIG_HOME") or home() / ".config"))
    cands: list[Candidate] = []
    for root in roots:
        for app in iter_entries(root):
            ctx.tick(app.path)
            try:
                if not app.is_dir(follow_symlinks=False) or is_reparse_or_link(app) or app.name.lower() in _BROWSER_DIR_NAMES:
                    continue
            except OSError:
                continue
            app_path = Path(app.path)
            # A Chromium/Electron profile has these marker files next to its caches. A plain folder named "Cache" is not enough.
            looks_electron = ((app_path / "Code Cache").is_dir() or (app_path / "GPUCache").is_dir()
                              or any((app_path / m).exists() for m in ("Local State", "Preferences", "Network Persistent State", "Local Storage")))
            if not looks_electron:
                continue
            for sub in _ELECTRON_CACHE_DIRS:
                d = app_path / sub
                if d.is_dir():
                    cands.append((d, app.name, "contents", MIN_ITEM_BYTES))
    extra: list[tuple[Path | None, str]] = []
    if IS_WIN and local_appdata() and roaming_appdata():
        la, ra = local_appdata(), roaming_appdata()
        extra += [(ra / "Adobe/Common/Media Cache Files", "Adobe media cache"), (ra / "Adobe/Common/Peak Files", "Adobe peak files"),
                  (la / "Spotify/Storage", "Spotify offline cache"), (la / "Steam/htmlcache", "Steam web cache"),
                  (la / "Microsoft/Windows/INetCache", "Internet cache"), (la / "Microsoft/Windows/WebCache", "Web cache database"),
                  (la / "D3DSCache", "DirectX shader cache"), (la / "NVIDIA/DXCache", "NVIDIA shader cache"),
                  (la / "NVIDIA/GLCache", "NVIDIA shader cache"), (la / "AMD/DxCache", "AMD shader cache"),
                  (la / "Microsoft/Teams/Cache", "Teams (classic)"), (la / "Packages/MSTeams_8wekyb3d8bbwe/LocalCache", "Teams"),
                  (la / "Microsoft/Office/16.0/OfficeFileCache", "Office file cache"), (la / "pip/http", "pip http cache")]
    elif IS_MAC:
        extra += [(home() / "Library/Caches/com.spotify.client", "Spotify"), (home() / "Library/Caches/com.apple.dt.Xcode", "Xcode"),
                  (home() / "Library/Application Support/Adobe/Common/Media Cache Files", "Adobe media cache")]
    for p, label in extra:
        if p is not None:
            cands.append((p, label, "contents", MIN_ITEM_BYTES))
    group.items.extend(ctx.items_for(cands))
    return group if group.items else None


def _thumbnails(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Thumbnail caches"
    group = JunkGroup("thumbs", "Thumbnail & icon caches", "Explorer and desktop thumbnails. They are rebuilt as you browse folders.",
                      "safe", "thumbs", note="Files Explorer currently holds open are skipped.")
    if IS_WIN and local_appdata():
        for f in glob.glob(str(local_appdata() / "Microsoft/Windows/Explorer/thumbcache_*.db")) + \
                 glob.glob(str(local_appdata() / "Microsoft/Windows/Explorer/iconcache_*.db")):
            item = ctx.item_for(f, "Explorer", min_bytes=1)
            if item:
                group.items.append(item)
    elif not IS_MAC:
        item = ctx.item_for(xdg_cache() / "thumbnails", "freedesktop thumbnails", kind="contents")
        if item:
            group.items.append(item)
    return group if group.items else None


def _crash_reports(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Crash reports & logs"
    group = JunkGroup("crash", "Crash dumps & error reports", "Memory dumps and diagnostic reports written when something crashed. Useful once, then dead weight.",
                      "safe", "crash")
    paths: list[tuple[Path | None, str]] = []
    if IS_WIN:
        la = local_appdata()
        sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        pd = env_path("ProgramData")
        if la:
            paths += [(la / "CrashDumps", "Windows crash dumps"), (la / "Microsoft/Windows/WER/ReportArchive", "Error reports"),
                      (la / "Microsoft/Windows/WER/ReportQueue", "Error reports"), (la / "Microsoft/Windows/WER/Temp", "Error reports")]
        if pd:
            paths += [(pd / "Microsoft/Windows/WER/ReportArchive", "Error reports (all users)"), (pd / "Microsoft/Windows/WER/ReportQueue", "Error reports (all users)")]
        paths += [(sysroot / "Minidump", "Kernel minidumps")]
        if la and (la / "Temp").is_dir():
            dumps: list[Candidate] = []
            for entry in _walk_shallow(la / "Temp", 2):
                if entry.name.lower().endswith((".dmp", ".hdmp", ".mdmp")):
                    dumps.append((entry.path, "dump file", "path", 1))
            group.items.extend(ctx.items_for(dumps))
    elif IS_MAC:
        paths += [(home() / "Library/Logs/DiagnosticReports", "Diagnostic reports"), (home() / "Library/Logs/CrashReporter", "Crash reporter")]
    else:
        paths += [(xdg_cache() / "abrt", "abrt"), (home() / ".local/share/apport", "apport")]
    for p, label in paths:
        if p is None or not p.is_dir() or not os.access(p, os.W_OK):
            continue
        item = ctx.item_for(p, label, kind="contents", min_bytes=1)
        if item:
            group.items.append(item)
    return group if group.items else None


def _recycle_bin(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Recycle bin"
    size, count = recycle_bin_info()
    if size <= 0:
        return None
    label = "Recycle Bin" if IS_WIN else "Trash"
    group = JunkGroup("recycle", label, f"Things you already deleted. Emptying it is what finally frees the space.",
                      "review", "recycle", note="Once emptied, these cannot be restored.")
    group.items.append(JunkItem(path=label, name=label, size=size, count=int(count), is_dir=True, kind="recycle_bin",
                                detail=f"{int(count)} item{'s' if count != 1 else ''}"))
    return group


def _package_caches(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Package manager caches"
    h, la, c = home(), local_appdata(), xdg_cache()
    cand: list[tuple[Path | None, str]] = [
        (h / ".npm/_cacache", "npm"), (h / ".yarn/cache", "yarn"), (c / "yarn", "yarn"), (c / "pip", "pip"),
        (c / "uv", "uv"), (c / "pypoetry", "poetry"), (c / "pypoetry/cache", "poetry"), (c / "go-build", "go build cache"),
        (h / ".cargo/registry/cache", "cargo"), (h / ".cargo/registry/src", "cargo (extracted sources)"), (h / ".cargo/git/checkouts", "cargo git checkouts"),
        (h / ".gradle/caches", "gradle"), (h / ".gradle/daemon", "gradle daemon logs"), (h / ".composer/cache", "composer"), (c / "composer", "composer"),
        (h / ".local/share/NuGet/v3-cache", "NuGet http cache"), (h / ".nuget/packages/.tools", "dotnet tools"), (c / "Homebrew", "Homebrew"),
        (h / "Library/Caches/Homebrew", "Homebrew"), (h / "Library/Caches/CocoaPods", "CocoaPods"), (h / "Library/Developer/Xcode/DerivedData", "Xcode DerivedData"),
        (h / "Library/Developer/Xcode/iOS DeviceSupport", "Xcode device support"), (h / ".android/cache", "Android"), (h / ".android/build-cache", "Android build cache"),
        (c / "bazel", "bazel"), (c / "ms-playwright", "Playwright browsers"), (c / "pre-commit", "pre-commit"), (c / "electron", "electron downloads"),
        (c / "node-gyp", "node-gyp"), (c / "Cypress", "Cypress"), (c / "puppeteer", "puppeteer"), (h / ".bun/install/cache", "bun"), (h / ".deno", "deno cache"),
    ]
    if IS_WIN and la:
        ra = roaming_appdata()
        cand += [(la / "npm-cache", "npm"), (la / "Yarn/Cache", "yarn"), (la / "pip/cache", "pip"), (la / "uv/cache", "uv"), (la / "pypoetry/Cache", "poetry"),
                 (la / "go-build", "go build cache"), (la / "NuGet/v3-cache", "NuGet http cache"), (la / "NuGet/plugins-cache", "NuGet plugins cache"),
                 (la / "Composer", "composer"), (la / "ms-playwright", "Playwright browsers"), (la / "Cypress/Cache", "Cypress"), (la / "node-gyp/Cache", "node-gyp"),
                 (la / "electron/Cache", "electron downloads"), (la / "pre-commit", "pre-commit"), (la / "Temp/chocolatey", "chocolatey"),
                 (la / "Microsoft/WinGet/Cache", "winget"), (la / "Android/Sdk/.temp", "Android SDK temp"), (la / "Android/Sdk/.downloadIntermediates", "Android SDK downloads")]
        if ra:
            cand += [(ra / "npm-cache", "npm"), (ra / "Composer/cache", "composer")]
        for ide in _globdirs(str(la / "JetBrains/*")):
            cand += [(ide / "caches", f"JetBrains {ide.name}"), (ide / "log", f"JetBrains {ide.name} logs"), (ide / "tmp", f"JetBrains {ide.name} temp")]
        for ide in _globdirs(str(la / "Google/AndroidStudio*")):
            cand += [(ide / "caches", "Android Studio"), (ide / "log", "Android Studio logs")]
    elif IS_MAC:
        for ide in _globdirs(str(h / "Library/Caches/JetBrains/*")):
            cand += [(ide, f"JetBrains {ide.name}")]
    else:
        for ide in _globdirs(str(c / "JetBrains/*")):
            cand += [(ide, f"JetBrains {ide.name}")]
    group = JunkGroup("pkgcache", "Package manager & build caches", "pip, npm, yarn, gradle, cargo, NuGet, Playwright, JetBrains ... Downloads again on demand.",
                      "safe", "package", note="The next install or build simply re-downloads what it needs.")
    group.items.extend(ctx.items_for([(p, label, "contents", MIN_ITEM_BYTES) for p, label in cand if p is not None]))
    return group if group.items else None


def _dependency_stores(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Dependency stores"
    h, la, c = home(), local_appdata(), xdg_cache()
    cand: list[tuple[Path | None, str, str]] = [
        (h / ".nuget/packages", "NuGet packages", "dotnet restore"), (h / ".m2/repository", "Maven repository", "mvn install"),
        (h / ".pub-cache", "Dart/Flutter pub cache", "flutter pub get"), (h / "go/pkg/mod", "Go modules", "go mod download"),
        (h / ".local/share/pnpm/store", "pnpm store", "pnpm install"), (h / ".gradle/wrapper/dists", "Gradle distributions", "gradle wrapper"),
        (c / "huggingface/hub", "Hugging Face models", "re-downloaded by transformers/diffusers"), (c / "torch", "PyTorch hub", "re-downloaded on use"),
        (h / "anaconda3/pkgs", "conda packages", "conda clean --all"), (h / "miniconda3/pkgs", "conda packages", "conda clean --all"),
        (h / ".conda/pkgs", "conda packages", "conda clean --all"), (h / ".cargo/registry", "Cargo registry", "cargo build"),
        (h / ".rustup/toolchains", "Rust toolchains", "rustup toolchain install"), (h / ".nvm/versions", "Node versions (nvm)", "nvm install"),
        (h / ".pyenv/versions", "Python versions (pyenv)", "pyenv install"), (h / ".sdkman/candidates", "SDKMAN candidates", "sdk install"),
        (h / ".ollama/models", "Ollama models", "ollama pull"), (h / "Library/Developer/CoreSimulator/Devices", "iOS simulators", "xcrun simctl delete unavailable"),
        (h / "Library/Developer/CoreSimulator/Caches", "Simulator caches", "safe to clear"),
    ]
    if IS_WIN and la:
        cand += [(la / "Pub/Cache", "Dart/Flutter pub cache", "flutter pub get"), (la / "pnpm/store", "pnpm store", "pnpm install"),
                 (la / "pnpm-cache", "pnpm cache", "pnpm install"), (la / "nvm", "Node versions (nvm)", "nvm install"),
                 (la / "Microsoft/dotnet/sdk-advertising", "dotnet", "dotnet"), (la / "Android/Sdk/system-images", "Android system images", "SDK Manager"),
                 (la / "Android/Sdk/emulator", "Android emulator", "SDK Manager"), (la / "Docker/wsl", "Docker Desktop data (WSL disk)", "docker system prune; shrink in Docker Desktop")]
    group = JunkGroup("deps", "Dependency stores & SDKs", "Package stores shared by all your projects. Removing one means the next build re-downloads it. Only worth it when you need the space.",
                      "review", "deps", note="Pick the ones for languages you no longer use.")
    group.items.extend(ctx.items_for([(p, f"{label} · restore: {how}", "path", 10 * 1024 * 1024) for p, label, how in cand if p is not None]))
    return group if group.items else None


# Artifact folder name -> (markers that must exist beside it, how to restore). "*" marker = always.
_ARTIFACTS: dict[str, tuple[tuple[str, ...], str]] = {
    "node_modules": (("package.json",), "npm install"),
    ".venv": (("*",), "python -m venv .venv && pip install -r requirements.txt"),
    "venv": (("*",), "python -m venv venv"),
    "env": (("pyvenv.cfg@",), "python -m venv env"),
    ".tox": (("tox.ini", "pyproject.toml"), "tox"),
    ".nox": (("noxfile.py",), "nox"),
    "build": (("pubspec.yaml", "build.gradle", "build.gradle.kts", "CMakeLists.txt", "setup.py", "pyproject.toml", "package.json", "meson.build"), "rebuild the project"),
    "dist": (("package.json", "pyproject.toml", "setup.py"), "npm run build"),
    "out": (("package.json", "tsconfig.json"), "npm run build"),
    ".output": (("package.json",), "npm run build"),
    "target": (("Cargo.toml", "pom.xml", "build.sbt"), "cargo build / mvn package"),
    ".dart_tool": (("pubspec.yaml",), "flutter pub get"),
    ".gradle": (("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"), "gradle build"),
    ".cxx": (("build.gradle", "build.gradle.kts", "CMakeLists.txt"), "gradle build"),
    ".next": (("package.json",), "next build"),
    ".nuxt": (("package.json",), "nuxt build"),
    ".svelte-kit": (("package.json",), "vite build"),
    ".angular": (("angular.json",), "ng build"),
    ".turbo": (("package.json", "turbo.json"), "turbo run build"),
    ".parcel-cache": (("package.json",), "parcel build"),
    ".expo": (("package.json", "app.json"), "expo start"),
    ".cache": (("package.json", "gatsby-config.js"), "rebuild"),
    ".pytest_cache": (("*",), "pytest"),
    ".mypy_cache": (("*",), "mypy"),
    ".ruff_cache": (("*",), "ruff"),
    "__pycache__": (("*",), "python"),
    ".ipynb_checkpoints": (("*",), "jupyter"),
    "htmlcov": (("*",), "coverage html"),
    "coverage": (("package.json", "pyproject.toml"), "run tests with coverage"),
    ".nyc_output": (("package.json",), "nyc"),
    "bin": (("*.csproj", "*.fsproj", "*.vbproj", "*.sln"), "dotnet build"),
    "obj": (("*.csproj", "*.fsproj", "*.vbproj", "*.sln"), "dotnet build"),
    "Pods": (("Podfile",), "pod install"),
    "DerivedData": (("*",), "Xcode rebuilds it"),
    ".terraform": (("*.tf",), "terraform init"),
    ".serverless": (("serverless.yml", "serverless.yaml"), "serverless deploy"),
    ".aws-sam": (("template.yaml", "template.yml"), "sam build"),
    "cdk.out": (("cdk.json",), "cdk synth"),
    ".build": (("Package.swift",), "swift build"),
    "zig-cache": (("build.zig",), "zig build"),
    "_build": (("mix.exs",), "mix compile"),
    "deps": (("mix.exs",), "mix deps.get"),
    ".stack-work": (("stack.yaml",), "stack build"),
    "dist-newstyle": (("*.cabal",), "cabal build"),
    ".dub": (("dub.json", "dub.sdl"), "dub build"),
    "vendor": (("composer.json", "go.mod"), "composer install / go mod vendor"),
    ".bundle": (("Gemfile",), "bundle install"),
}
_NEVER_DESCEND = {".git", ".hg", ".svn", "AppData", "Library", "$RECYCLE.BIN", "System Volume Information", "Windows",
                  "Program Files", "Program Files (x86)", "ProgramData", "OneDrive - Temp", ".Trash", "snap", "Applications"}
_DEFAULT_PROJECT_DIR_NAMES = ("Documents", "Desktop", "Projects", "projects", "project", "code", "Code", "dev", "Dev", "src", "source",
                              "Source", "repos", "Repos", "repo", "git", "GitHub", "github", "gitlab", "workspace", "Workspace",
                              "workspaces", "work", "Work", "Developer", "development", "Development", "Sites", "www", "go/src", "StudioProjects",
                              "AndroidStudioProjects", "IdeaProjects", "PycharmProjects", "WebstormProjects", "eclipse-workspace", "Downloads")


def _marker_present(project: Path, markers: tuple[str, ...], artifact: Path) -> bool:
    for m in markers:
        if m == "*":
            return True
        if m.endswith("@"):
            if (artifact / m[:-1]).exists():
                return True
            continue
        if "*" in m:
            if glob.glob(str(project / m)):
                return True
        elif (project / m).exists():
            return True
    return False


def _project_activity(project: Path, skip: set[str], cache: dict[str, float]) -> float:
    key = os.path.normcase(str(project))
    if key in cache:
        return cache[key]
    newest = 0.0
    git = project / ".git"
    for f in ("FETCH_HEAD", "ORIG_HEAD", "HEAD", "index", "logs/HEAD", "COMMIT_EDITMSG"):
        p = git / f
        if p.exists():
            newest = max(newest, mtime_of(p))
    for entry in iter_entries(project):
        if entry.name in skip or entry.name in _ARTIFACTS or entry.name == ".git":
            continue
        try:
            st = entry.stat(follow_symlinks=False)
            newest = max(newest, st.st_mtime)
            if entry.is_dir(follow_symlinks=False) and not is_reparse_or_link(entry):
                for sub in iter_entries(entry.path):
                    if sub.name in _ARTIFACTS:
                        continue
                    try:
                        newest = max(newest, sub.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        pass
        except OSError:
            continue
    cache[key] = newest or mtime_of(project)
    return cache[key]


def project_roots(cfg: Config) -> list[Path]:
    if cfg.space.project_roots:
        roots = [Path(os.path.expandvars(r)).expanduser() for r in cfg.space.project_roots]
    else:
        h = home()
        roots = [h / n for n in _DEFAULT_PROJECT_DIR_NAMES]
        roots += [h / "OneDrive" / n for n in ("Documents", "Desktop")]
        # Any top-level home folder that is itself a project or holds .git repos one level down.
        for entry in iter_entries(h):
            try:
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith(".") and entry.name not in _NEVER_DESCEND \
                        and not is_reparse_or_link(entry) and Path(entry.path) not in roots:
                    if any(m in os.listdir(entry.path) for m in ("package.json", ".git", "pyproject.toml", "pubspec.yaml", "Cargo.toml", "go.mod")) or \
                            any((Path(entry.path) / sub.name / ".git").exists() for sub in list(iter_entries(entry.path))[:40]):
                        roots.append(Path(entry.path))
            except OSError:
                continue
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        if r.is_dir():
            key = os.path.normcase(os.path.realpath(r))
            if key not in seen and not any(key.startswith(s + os.sep) for s in seen):
                seen.add(key)
                out.append(r)
    return out


def _project_artifacts(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Project build folders"
    cfg = ctx.cfg
    stale_days = cfg.space.stale_project_days
    excl = set(cfg.space.exclude) | _NEVER_DESCEND
    group = JunkGroup("projects", "Project build folders & dependencies",
                      "node_modules, virtual environments and build outputs. One command recreates them. Projects you have not touched in a while come first.",
                      "review", "projects", note="Nothing in your source code is touched, only generated folders.")
    activity_cache: dict[str, float] = {}
    found: list[tuple[str, Path, str]] = []
    max_depth = 7
    for root in project_roots(cfg):
        stack: list[tuple[str, int]] = [(str(root), 0)]
        while stack:
            current, depth = stack.pop()
            ctx.tick(current)
            for entry in iter_entries(current):
                try:
                    if not entry.is_dir(follow_symlinks=False) or is_reparse_or_link(entry):
                        continue
                except OSError:
                    continue
                name = entry.name
                spec = _ARTIFACTS.get(name)
                if spec is not None:
                    markers, how = spec
                    project = Path(current)
                    if _marker_present(project, markers, Path(entry.path)):
                        found.append((entry.path, project, how))
                        continue  # never descend into an artifact folder
                if name in excl or name in _NEVER_DESCEND or (name.startswith(".") and name not in _ARTIFACTS):
                    continue
                if depth + 1 <= max_depth:
                    stack.append((entry.path, depth + 1))
    items = ctx.items_for([(path, "", "path", 256 * 1024) for path, _, _ in found])
    meta = {os.path.normcase(path): (project, how) for path, project, how in found}
    for item in items:
        project, how = meta[os.path.normcase(item.path)]
        idle = int(age_days(_project_activity(project, excl, activity_cache)))
        item.stale = idle >= stale_days
        item.detail = f"{project.name} · idle {idle} day{'s' if idle != 1 else ''} · restore: {how}"
        item.name = f"{project.name}/{os.path.basename(item.path)}"
        group.items.append(item)
    return group if group.items else None


_INSTALLER_EXT = {"exe", "msi", "msix", "msixbundle", "appx", "appxbundle", "dmg", "pkg", "deb", "rpm", "appimage", "apk", "iso"}


def _download_dirs(cfg: Config) -> list[Path]:
    dirs = [_downloads()] + cfg.source_paths()
    out: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        if d.is_dir():
            k = os.path.normcase(os.path.realpath(d))
            if k not in seen:
                seen.add(k)
                out.append(d)
    return out


def _walk_shallow(root: Path, depth: int) -> Iterable[os.DirEntry]:
    stack = [(str(root), 0)]
    while stack:
        cur, d = stack.pop()
        for entry in iter_entries(cur):
            try:
                if entry.is_dir(follow_symlinks=False):
                    if d + 1 <= depth and not is_reparse_or_link(entry) and not entry.name.startswith("."):
                        stack.append((entry.path, d + 1))
                elif entry.is_file(follow_symlinks=False):
                    yield entry
            except OSError:
                continue


def _old_installers(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Old installers"
    days = ctx.cfg.space.installer_age_days
    group = JunkGroup("installers", "Old installers & disk images", f"Setup files and ISOs downloaded more than {days} days ago. The program is installed; the installer is just taking up room.",
                      "review", "installers")
    for d in _download_dirs(ctx.cfg):
        for entry in _walk_shallow(d, 2):
            ctx.tick(entry.path)
            ext = entry.name.rsplit(".", 1)[-1].lower() if "." in entry.name else ""
            if ext not in _INSTALLER_EXT:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if age_days(st.st_mtime) >= days and st.st_size > 0:
                group.items.append(JunkItem(path=entry.path, size=st.st_size, is_dir=False, mtime=st.st_mtime,
                                            detail=f"{int(age_days(st.st_mtime))} days old · {Path(d).name}"))
    return group if group.items else None


def _old_downloads(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Large forgotten downloads"
    days = ctx.cfg.space.old_download_days
    min_bytes = ctx.cfg.space.large_file_mb * 1024 * 1024
    group = JunkGroup("downloads", "Large, forgotten downloads", f"Files over {ctx.cfg.space.large_file_mb} MB in Downloads that have not changed in {days} days. Videos, archives, datasets you probably finished with.",
                      "review", "downloads", note="Have a look at the list; these go to the recycle bin so they can come back.")
    for d in _download_dirs(ctx.cfg):
        for entry in _walk_shallow(d, 2):
            ctx.tick(entry.path)
            ext = entry.name.rsplit(".", 1)[-1].lower() if "." in entry.name else ""
            if ext in _INSTALLER_EXT:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_size >= min_bytes and age_days(st.st_mtime) >= days:
                group.items.append(JunkItem(path=entry.path, size=st.st_size, is_dir=False, mtime=st.st_mtime,
                                            detail=f"{int(age_days(st.st_mtime))} days old · {Path(d).name}"))
    return group if group.items else None


def _empty_folders(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "Empty folders"
    group = JunkGroup("empty", "Empty folders", "Folders in your watched locations with nothing inside.", "safe", "empty")
    for src in ctx.cfg.source_paths():
        if not src.is_dir():
            continue
        stack = [(str(src), 0)]
        while stack:
            cur, depth = stack.pop()
            for entry in iter_entries(cur):
                try:
                    if not entry.is_dir(follow_symlinks=False) or is_reparse_or_link(entry) or entry.name.startswith("."):
                        continue
                    children = os.listdir(entry.path)
                except OSError:
                    continue
                if not children:
                    group.items.append(JunkItem(path=entry.path, size=0, count=1, is_dir=True, mtime=mtime_of(entry.path), detail=_detail_age(entry.path)))
                elif depth + 1 <= 3:
                    stack.append((entry.path, depth + 1))
    return group if group.items else None


def _system_manual(ctx: _Ctx) -> JunkGroup | None:
    ctx.step = "System files"
    if IS_WIN:
        sysdrive = os.environ.get("SystemDrive", "C:") + "\\"
        sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        group = JunkGroup("system", "Needs Windows tools", "Protected system areas Windows must clean itself. Open Storage settings and use 'Cleanup recommendations' or 'Temporary files'.",
                          "manual", "system", hint="Settings > System > Storage > Cleanup recommendations", hint_url="ms-settings:storagesense")
        cands = [(Path(sysdrive) / "Windows.old", "Previous Windows installation · removed by Storage settings"),
                 (sysroot / "SoftwareDistribution/Download", "Windows Update downloads · Storage settings > Temporary files"),
                 (sysroot / "SoftwareDistribution/DeliveryOptimization", "Delivery Optimization cache · Storage settings"),
                 (sysroot / "Temp", "System temp · needs administrator"),
                 (sysroot / "LiveKernelReports", "Kernel reports · needs administrator"),
                 (sysroot / "Logs/CBS", "Servicing logs · needs administrator"),
                 (Path(sysdrive) / "hiberfil.sys", "Hibernation file · disable with: powercfg /h off (admin)"),
                 (Path(sysdrive) / "swapfile.sys", "Swap file · managed by Windows")]
        for p, label in cands:
            try:
                if not p.exists():
                    continue
                if p.is_file():
                    size, count = p.stat().st_size, 1
                else:
                    if os.access(p, os.W_OK) and p == sysroot / "Temp":
                        continue  # handled by the temp detector
                    size, count = dir_size(p, ctx.cancel)
            except OSError:
                continue
            if size >= 50 * 1024 * 1024:
                group.items.append(JunkItem(path=str(p), size=size, count=count, is_dir=p.is_dir(), detail=label, mtime=mtime_of(p)))
        return group if group.items else None
    if IS_MAC:
        group = JunkGroup("system", "Needs macOS tools", "Handled by macOS itself or Xcode.", "manual", "system",
                          hint="Apple menu > System Settings > General > Storage")
        for p, label in [(home() / "Library/Developer/Xcode/Archives", "Xcode archives · Organizer window"),
                         (Path("/Library/Updates"), "Pending macOS updates")]:
            item = ctx.item_for(p, label, min_bytes=50 * 1024 * 1024)
            if item:
                group.items.append(item)
        return group if group.items else None
    group = JunkGroup("system", "Needs root", "Package caches and logs owned by the system.", "manual", "system",
                      hint="sudo apt clean && sudo journalctl --vacuum-time=7d")
    for p, label in [(Path("/var/cache/apt/archives"), "APT package cache · sudo apt clean"),
                     (Path("/var/log/journal"), "systemd journal · sudo journalctl --vacuum-time=7d"),
                     (Path("/var/lib/snapd/snaps"), "Snap revisions · snap remove --revision"),
                     (Path("/var/cache/pacman/pkg"), "pacman cache · sudo paccache -r")]:
        item = ctx.item_for(p, label, min_bytes=50 * 1024 * 1024)
        if item:
            group.items.append(item)
    return group if group.items else None


DETECTORS: list[tuple[str, Callable[[_Ctx], JunkGroup | None]]] = [
    ("temp", _temp_files), ("browser", _browser_caches), ("apps", _app_caches), ("thumbs", _thumbnails),
    ("crash", _crash_reports), ("pkgcache", _package_caches), ("empty", _empty_folders), ("recycle", _recycle_bin),
    ("projects", _project_artifacts), ("deps", _dependency_stores), ("installers", _old_installers),
    ("downloads", _old_downloads), ("system", _system_manual),
]
GROUP_IDS = [gid for gid, _ in DETECTORS]


def analyze(cfg: Config, cancel: Cancel | None = None, progress: ProgressFn | None = None,
            only: Iterable[str] | None = None) -> Analysis:
    """Run every detector and return the graded groups. Individual detector failures never abort the run."""
    started = time.time()
    t0 = time.monotonic()
    ctx = _Ctx(cfg, cancel, progress)
    wanted = set(only) if only else None
    groups: list[JunkGroup] = []
    detectors = [(gid, fn) for gid, fn in DETECTORS if wanted is None or gid in wanted]
    total = len(detectors)
    try:
        for i, (gid, det) in enumerate(detectors):
            if cancel:
                cancel.check()
            d0 = time.monotonic()
            try:
                g = det(ctx)
            except Exception as exc:  # noqa: BLE001 - one broken detector must not hide the others
                if exc.__class__.__name__ == "Cancelled":
                    raise
                g = None
            if g is not None and g.items:
                g.duration = time.monotonic() - d0
                groups.append(g)
                ctx.emit({"phase": "group", "group": g.to_dict(), "index": i + 1, "total": total}, force=True)
            ctx.emit({"phase": "junk", "step": ctx.step, "index": i + 1, "total": total, "found": sum(x.size for x in groups)}, force=True)
    finally:
        ctx.close()
    return Analysis(groups=groups, duration=time.monotonic() - t0, started_at=started)
