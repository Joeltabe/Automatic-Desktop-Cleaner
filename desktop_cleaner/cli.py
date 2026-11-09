"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .config import Config, config_to_dict, default_config_path, ensure_config, load_config, save_config
from .engine import ApplyResult, Plan, apply_plan, build_plan
from .journal import Journal
from .rules import human_size
from .stats import summarize
from .watcher import Watcher

# ----------------------------------------------------------------------------- output helpers

USE_COLOR = sys.stdout.isatty()


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def bold(t: str) -> str:
    return c(t, "1")


def dim(t: str) -> str:
    return c(t, "2")


def green(t: str) -> str:
    return c(t, "32")


def yellow(t: str) -> str:
    return c(t, "33")


def red(t: str) -> str:
    return c(t, "31")


def cyan(t: str) -> str:
    return c(t, "36")


def print_plan(plan: Plan, show_skipped: bool = False) -> None:
    for missing in plan.missing_sources:
        print(yellow(f"! source not found: {missing}"))
    if not plan.actions:
        print(dim("Nothing to move. Everything is already tidy."))
    else:
        print(bold(f"{len(plan.actions)} file(s), {human_size(plan.bytes_total)} would move:"))
        for rule_name, actions in plan.by_rule().items():
            print(f"\n  {cyan(rule_name)}  {dim(f'({len(actions)})')}")
            for a in actions:
                src = Path(a.source)
                dest_dir = Path(a.destination).parent
                try:
                    rel = dest_dir.relative_to(a.source_dir)
                except ValueError:
                    rel = dest_dir
                print(f"    {src.name}  {dim('->')}  {rel}/  {dim(human_size(a.size))}")
    if show_skipped and plan.skipped:
        print(f"\n{dim('Skipped:')}")
        for s in plan.skipped:
            print(f"    {dim(Path(s.path).name)}  {dim('-')}  {dim(s.reason)}")


def print_result(result: ApplyResult) -> None:
    moved = result.moved
    if moved:
        print(green(f"Moved {len(moved)} file(s), {human_size(result.bytes_moved)}.") + dim(f"  batch {result.batch.batch_id}"))
    for a in result.skipped_conflicts:
        print(yellow(f"  skipped (exists): {Path(a.source).name}"))
    for a, err in result.failed:
        print(red(f"  failed: {Path(a.source).name}: {err}"))
    if moved:
        print(dim("Undo with: desktop-cleaner undo"))


# ----------------------------------------------------------------------------- commands


def _load(args: argparse.Namespace) -> Config:
    path = Path(args.config) if args.config else None
    cfg, _, created = ensure_config(path)
    if created and not getattr(args, "json", False):
        print(dim(f"Created default config at {path or default_config_path()}"))
    return cfg


def cmd_preview(args: argparse.Namespace) -> int:
    cfg = _load(args)
    plan = build_plan(cfg)
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2))
    else:
        print_plan(plan, show_skipped=args.show_skipped)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load(args)
    plan = build_plan(cfg)
    if not plan.actions:
        if args.json:
            print(json.dumps({"moved": [], "plan": plan.to_dict()}))
        else:
            print_plan(plan)
        return 0
    if not args.yes and not args.json:
        print_plan(plan)
        answer = input(bold("\nProceed? [y/N] ")).strip().lower()
        if answer not in ("y", "yes"):
            print(dim("Cancelled."))
            return 1
    result = apply_plan(plan, cfg, journal=Journal(), trigger="cli")
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print_result(result)
    return 0 if not result.failed else 2


def cmd_undo(args: argparse.Namespace) -> int:
    journal = Journal()
    try:
        res = journal.undo(args.batch)
    except (LookupError, ValueError) as exc:
        print(red(str(exc)))
        return 1
    if args.json:
        print(
            json.dumps(
                {"batch_id": res.batch_id, "restored": len(res.restored), "failed": [{"file": m.dest, "error": e} for m, e in res.failed]},
                indent=2,
            )
        )
        return 0 if res.ok else 2
    print(green(f"Restored {len(res.restored)} file(s) from batch {res.batch_id}."))
    for m, err in res.failed:
        print(red(f"  could not restore {Path(m.dest).name}: {err}"))
    return 0 if res.ok else 2


def cmd_history(args: argparse.Namespace) -> int:
    journal = Journal()
    batches = journal.batches()
    if args.json:
        print(json.dumps([b.to_dict() for b in batches], indent=2))
        return 0
    if not batches:
        print(dim("No history yet."))
        return 0
    for b in reversed(batches[-args.limit :]):
        status = yellow("undone") if b.undone else green("applied")
        print(f"{bold(b.batch_id)}  {b.timestamp}  {status}  {len(b.moves)} file(s)  {human_size(b.bytes_moved)}  {dim(b.trigger)}")
        if args.verbose:
            for m in b.moves:
                print(f"    {Path(m.src).name}  {dim('->')}  {m.dest}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    s = summarize(Journal(), days=args.days)
    if args.json:
        print(json.dumps(s, indent=2))
        return 0
    print(bold("Desktop Cleaner stats"))
    print(f"  files organised : {s['files_moved']}")
    print(f"  space organised : {human_size(s['bytes_moved'])}")
    print(f"  runs            : {s['runs']}  ({s['undone_runs']} undone)")
    print(f"  streak          : {s['streak_days']} day(s)")
    if s["by_rule"]:
        print("  by category     :")
        for row in s["by_rule"][:10]:
            print(f"    {row['rule']:<22} {row['files']:>5}  {human_size(row['bytes'])}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = _load(args)
    interval = args.interval or cfg.options.watch_interval_seconds

    def on_batch(plan: Plan, result: ApplyResult) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"{dim(stamp)} " + green(f"moved {len(result.moved)} file(s)") + f" {dim(human_size(result.bytes_moved))}")
        for m in result.moved:
            print(f"    {Path(m.src).name}  {dim('->')}  {m.rule_name}")

    watcher = Watcher(cfg, journal=Journal(), interval=interval, on_batch=on_batch)
    print(bold(f"Watching {', '.join(cfg.sources)} every {interval:g}s. Ctrl+C to stop."))
    try:
        watcher.run_forever()
    except KeyboardInterrupt:
        print(dim("\nStopped."))
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui.app import launch
    except ImportError as exc:  # pragma: no cover - depends on optional dependency
        print(red("The GUI needs pywebview. Install it with: pip install pywebview"))
        print(dim(str(exc)))
        return 1
    return launch(Path(args.config) if args.config else None)


def cmd_config(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else default_config_path()
    if args.action == "path":
        print(path)
        return 0
    if args.action == "init":
        cfg, p, created = ensure_config(path)
        if not created and not args.force:
            print(yellow(f"Config already exists at {p} (use --force to overwrite with defaults)."))
            return 1
        if not created:
            from .config import default_config

            save_config(default_config(), p)
        print(green(f"Wrote default config to {p}"))
        return 0
    if args.action == "show":
        cfg = load_config(path if path.exists() else None)
        print(json.dumps(config_to_dict(cfg), indent=2))
        return 0
    if args.action == "import":
        src = Path(args.file)
        if not src.exists():
            print(red(f"{src} does not exist"))
            return 1
        cfg = load_config(src)
        save_config(cfg, path)
        print(green(f"Imported {len(cfg.rules)} rule(s) into {path}"))
        return 0
    print(red(f"unknown config action {args.action}"))
    return 1


# ----------------------------------------------------------------------------- free up space

_UTF = (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "").startswith("utf")


def _bar(fraction: float, width: int = 24) -> str:
    n = max(0, min(width, int(round(fraction * width))))
    full, empty = ("█", "░") if _UTF else ("#", "-")
    return full * n + dim(empty * (width - n))


def _print_volumes(volumes: list[dict]) -> None:
    for v in volumes:
        colour = red if v["status"] == "critical" else yellow if v["status"] == "warn" else green
        pct = colour(f"{v['percent']:.0f}%")
        print(
            f"  {bold(v['path']):<6} {v['label'][:18]:<18} {_bar(v['used'] / v['total'] if v['total'] else 0)} "
            f"{pct}  {human_size(v['free'])} free of {human_size(v['total'])}"
        )


def _clear_line(args: argparse.Namespace) -> None:
    if not (getattr(args, "quiet", False) or getattr(args, "json", False)):
        sys.stderr.write("\r" + " " * 100 + "\r")


def _progress_printer():
    import shutil as _sh

    width = max(40, _sh.get_terminal_size((100, 20)).columns - 2)

    def show(payload: dict) -> None:
        phase = payload.get("phase")
        if phase == "junk":
            line = f"analysing: {payload.get('step', '')}  {dim(str(payload.get('current', ''))[-60:])}"
        elif phase == "group":
            g = payload["group"]
            line = f"  found {bold(human_size(g['size']))} in {g['title']}"
            sys.stderr.write("\r" + " " * width + "\r" + line + "\n")
            return
        elif phase == "scan":
            where = dim(str(payload.get("current", ""))[-50:])
            line = f"scanning: {payload.get('files', 0):,} files, {human_size(payload.get('bytes', 0))}  {where}"
        elif phase == "collect":
            line = f"collecting: {payload.get('files', 0):,} files, {payload.get('candidates', 0):,} same-size candidates"
        elif phase == "hash":
            line = f"hashing: {payload.get('done', 0)}/{payload.get('total', 0)}  {human_size(payload.get('bytes', 0))}"
        elif phase == "clean":
            freed = human_size(payload.get("freed", 0))
            line = f"cleaning {payload.get('index', 0)}/{payload.get('total', 0)}: {payload.get('name', '')}  freed {freed}"
        else:
            sys.stderr.write("\r" + " " * width + "\r")
            return
        sys.stderr.write("\r" + line[:width].ljust(width))
        sys.stderr.flush()

    return show


def cmd_space(args: argparse.Namespace) -> int:
    from .space.cleaner import SpaceLog
    from .space.disks import list_volumes
    from .space.junk import analyze

    cfg = _load(args)
    volumes = list_volumes()
    if args.json:
        a = analyze(cfg)
        print(json.dumps({"volumes": volumes, "analysis": a.to_dict(), "history": SpaceLog().summary()}, indent=2))
        return 0
    print(bold("Drives"))
    _print_volumes(volumes)
    print()
    a = analyze(cfg, progress=None if args.quiet else _progress_printer())
    _clear_line(args)
    print(bold(f"You could free about {human_size(a.total('safe') + a.total('review'))}") + dim(f"  (analysed in {a.duration:.0f}s)"))
    print(
        f"  {green(human_size(a.total('safe')))} safe to delete now    {yellow(human_size(a.total('review')))} worth a look    "
        f"{dim(human_size(a.total('manual')))} needs system tools"
    )
    for g in sorted(a.groups, key=lambda g: ({"safe": 0, "review": 1, "manual": 2}[g.confidence], -g.size)):
        tag = green("safe") if g.confidence == "safe" else yellow("review") if g.confidence == "review" else dim("manual")
        print(f"\n  [{tag}] {bold(g.title)}  {human_size(g.size)}  {dim(f'{g.count:,} items')}")
        for it in sorted(g.items, key=lambda i: (-int(i.stale), -i.size))[: args.top]:
            name = it.to_dict()["name"]
            print(f"      {human_size(it.size):>9}  {name[:44]:<44} {dim(it.detail[:60])}")
        if len(g.items) > args.top:
            print(dim(f"      ... and {len(g.items) - args.top} more"))
    hist = SpaceLog().summary()
    if hist["cleans"]:
        print(dim(f"\nFreed so far with Desktop Cleaner: {human_size(hist['freed_total'])} over {hist['cleans']} clean-up(s)."))
    print(dim("\nClean the safe groups:  desktop-cleaner junk --clean\nInclude review groups:  desktop-cleaner junk --clean --review"))
    return 0


def cmd_junk(args: argparse.Namespace) -> int:
    from .space.cleaner import SpaceLog, execute
    from .space.junk import analyze

    cfg = _load(args)
    a = analyze(cfg, progress=None if (args.json or args.quiet) else _progress_printer(), only=args.group or None)
    _clear_line(args)
    chosen = [g for g in a.groups if g.confidence == "safe" or (args.review and g.confidence == "review")]
    if args.group:
        chosen = [g for g in a.groups if g.id in args.group and g.confidence != "manual"]
    if not args.clean:
        if args.json:
            print(json.dumps(a.to_dict(), indent=2))
            return 0
        for g in chosen:
            print(f"  {g.id:<11} {g.title:<42} {human_size(g.size):>10}  {dim(g.confidence)}")
        print(bold(f"\n{human_size(sum(g.size for g in chosen))} would be removed.") + dim("  Add --clean to do it."))
        return 0
    total = sum(g.size for g in chosen)
    if not chosen or total == 0:
        print(dim("Nothing to clean."))
        return 0
    if not args.yes and not args.json:
        for g in chosen:
            print(f"  {g.title:<42} {human_size(g.size):>10}  {dim(g.confidence)}")
        mode = "deleted permanently" if args.permanent else "deleted (safe) / moved to the recycle bin (review)"
        answer = input(bold(f"\nRemove {human_size(total)}? Items are {mode}. [y/N] ")).strip().lower()
        if answer not in ("y", "yes"):
            print(dim("Cancelled."))
            return 1
    res = execute(
        a,
        [g.id for g in chosen],
        [],
        permanent_review=args.permanent,
        log=SpaceLog(),
        trigger="cli",
        progress=None if (args.json or args.quiet) else _progress_printer(),
    )
    _clear_line(args)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(green(f"Freed {human_size(res.freed)}") + dim(f"  ({res.removed} items removed, {len(res.failed)} skipped)"))
        for f in res.failed[:15]:
            print(yellow(f"  skipped: {Path(f['path']).name}: {f['error']}"))
    return 0 if not res.failed else 2


def cmd_scan(args: argparse.Namespace) -> int:
    from .space.scanner import scan_tree

    target = Path(args.path or Path.home()).expanduser()
    if not target.is_dir():
        print(red(f"{target} is not a folder"))
        return 1
    res = scan_tree(target, progress=None if (args.json or args.quiet) else _progress_printer())
    _clear_line(args)
    if args.json:
        print(json.dumps(res.to_dict(depth=args.depth), indent=2))
        return 0
    print(bold(f"{target}") + f"  {human_size(res.bytes)} in {res.files:,} files, {res.dirs:,} folders" + dim(f"  ({res.duration:.1f}s)"))
    if res.denied:
        print(yellow(f"  {res.denied} folder(s) could not be read"))
    print(bold("\nLargest folders"))
    kids = sorted(res.root.children, key=lambda c: -c.size)[: args.top]
    for c in kids:
        frac = c.size / res.bytes if res.bytes else 0
        print(f"  {_bar(frac, 18)} {human_size(c.size):>9}  {c.name}")
    if res.root.file_bytes:
        print(
            f"  {_bar(res.root.file_bytes / res.bytes if res.bytes else 0, 18)} {human_size(res.root.file_bytes):>9}  "
            f"{dim('(files directly here)')}"
        )
    print(bold("\nLargest files"))
    for f in res.largest[: args.top]:
        print(f"  {human_size(f['size']):>9}  {f['name'][:50]:<50} {dim(str(Path(f['path']).parent)[-40:])}")
    print(bold("\nBy type"))
    for t in res.by_type[:8]:
        files_label = dim(f"{t['files']:,} files")
        print(f"  {_bar(t['bytes'] / res.bytes if res.bytes else 0, 18)} {human_size(t['bytes']):>9}  {t['category']}  {files_label}")
    return 0


def cmd_dupes(args: argparse.Namespace) -> int:
    from .space.cleaner import SpaceLog, remove_paths
    from .space.duplicates import find_duplicates

    cfg = _load(args)
    roots = args.paths or cfg.space.duplicate_roots or [str(p) for p in cfg.source_paths()] + [str(Path.home() / "Downloads")]
    res = find_duplicates(
        roots,
        min_bytes=int(args.min_mb * 1024 * 1024),
        exclude_names=set(cfg.space.exclude),
        progress=None if (args.json or args.quiet) else _progress_printer(),
    )
    _clear_line(args)
    if args.json and not args.remove:
        print(json.dumps(res.to_dict(), indent=2))
        return 0
    groups = sorted(res.groups, key=lambda g: -g.reclaimable)
    if not groups:
        print(dim("No duplicates found."))
        return 0
    print(
        bold(f"{len(groups)} duplicate group(s), {human_size(res.reclaimable)} reclaimable")
        + dim(f"  ({res.scanned_files:,} files checked in {res.duration:.1f}s)")
    )
    for g in groups[: args.top]:
        keep = g.suggested_keep()
        print(f"\n  {human_size(g.size):>9} × {len(g.files)}  {bold(Path(keep).name)}")
        for f in sorted(g.files, key=lambda f: (f.mtime, f.path)):
            mark = green("keep ") if f.path == keep else dim("dupe ")
            print(f"      {mark} {f.path}")
    if len(groups) > args.top:
        print(dim(f"\n  ... and {len(groups) - args.top} more groups"))
    if not args.remove:
        print(dim("\nRemove all duplicates (keeping the oldest copy of each): desktop-cleaner dupes --remove"))
        return 0
    victims = [f.path for g in groups for f in g.files if f.path != g.suggested_keep()]
    if not args.yes:
        answer = (
            input(bold(f"\nMove {len(victims)} duplicate(s), {human_size(res.reclaimable)}, to the recycle bin? [y/N] ")).strip().lower()
        )
        if answer not in ("y", "yes"):
            print(dim("Cancelled."))
            return 1
    r = remove_paths(victims, permanent=args.permanent, log=SpaceLog(), label="duplicates", trigger="cli")
    if args.json:
        print(json.dumps(r.to_dict(), indent=2))
    else:
        print(green(f"Freed {human_size(r.freed)}") + dim(f"  ({r.removed} removed, {len(r.failed)} failed)"))
    return 0 if not r.failed else 2


# ----------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="desktop-cleaner",
        description="Tidy your Desktop and Downloads with rules, preview and undo, and free up disk space safely.",
    )
    p.add_argument("--version", action="version", version=f"desktop-cleaner {__version__}")
    p.add_argument("--config", help="path to config.json (default: per-user config dir)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("preview", help="show what would move without moving anything")
    sp.add_argument("--show-skipped", action="store_true", help="also list files that were left alone and why")
    sp.set_defaults(func=cmd_preview)

    sp = sub.add_parser("run", help="organise now")
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("undo", help="put files from the last run (or a batch id) back")
    sp.add_argument("batch", nargs="?", help="batch id (prefix is fine); default: last run")
    sp.set_defaults(func=cmd_undo)

    sp = sub.add_parser("history", help="list previous runs")
    sp.add_argument("-n", "--limit", type=int, default=20)
    sp.add_argument("-v", "--verbose", action="store_true")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("stats", help="lifetime statistics")
    sp.add_argument("--days", type=int, default=30)
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("watch", help="keep running and organise files as they appear")
    sp.add_argument("--interval", type=float, help="seconds between scans")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("gui", help="open the desktop app")
    sp.set_defaults(func=cmd_gui)

    sp = sub.add_parser("space", help="drives overview and how much junk could be freed")
    sp.add_argument("--top", type=int, default=5, help="items to show per group")
    sp.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    sp.set_defaults(func=cmd_space)

    sp = sub.add_parser("junk", help="list or remove junk (safe groups by default)")
    sp.add_argument("--clean", action="store_true", help="actually remove it")
    sp.add_argument(
        "--review", action="store_true", help="also include 'review' groups (old installers, idle node_modules, recycle bin ...)"
    )
    sp.add_argument("--group", action="append", help="only this group id (repeatable), e.g. --group temp --group browser")
    sp.add_argument("--permanent", action="store_true", help="delete review items permanently instead of using the recycle bin")
    sp.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    sp.add_argument("-q", "--quiet", action="store_true")
    sp.set_defaults(func=cmd_junk)

    sp = sub.add_parser("scan", help="what is taking up space in a folder (default: your home folder)")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--top", type=int, default=15)
    sp.add_argument("--depth", type=int, default=2, help="tree depth for --json output")
    sp.add_argument("-q", "--quiet", action="store_true")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("dupes", help="find byte-identical duplicate files")
    sp.add_argument("paths", nargs="*", help="folders to search (default: watched folders + Downloads)")
    sp.add_argument("--min-mb", type=float, default=1.0, help="ignore files smaller than this (default 1 MB)")
    sp.add_argument("--top", type=int, default=20)
    sp.add_argument("--remove", action="store_true", help="move duplicates to the recycle bin, keeping the oldest copy")
    sp.add_argument("--permanent", action="store_true")
    sp.add_argument("-y", "--yes", action="store_true")
    sp.add_argument("-q", "--quiet", action="store_true")
    sp.set_defaults(func=cmd_dupes)

    sp = sub.add_parser("config", help="manage the config file")
    sp.add_argument("action", choices=["path", "init", "show", "import"])
    sp.add_argument("file", nargs="?", help="file to import (for 'import')")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_config)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # never crash on a console that cannot show a character
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        # Default: GUI when available, else preview.
        try:
            import webview  # noqa: F401

            args.command = "gui"
            return cmd_gui(args)
        except ImportError:
            args.show_skipped = False
            return cmd_preview(args)
    return int(args.func(args) or 0)
