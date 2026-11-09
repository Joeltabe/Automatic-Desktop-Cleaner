import os
import time
from pathlib import Path

import pytest

from desktop_cleaner.config import Config, Options, Rule
from desktop_cleaner.engine import apply_plan, build_plan, resolve_conflict
from desktop_cleaner.journal import Journal
from desktop_cleaner.rules import FileInfo, rule_matches


def touch(path: Path, content: bytes = b"x", age_seconds: float = 3600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    old = time.time() - age_seconds
    os.utime(path, (old, old))
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Config, Journal]:
    src = tmp_path / "Desktop"
    src.mkdir()
    cfg = Config(
        sources=[str(src)],
        rules=[
            Rule.new("Images", "Images", ["jpg", "png"]),
            Rule.new("Docs", "Documents", ["txt", "pdf"]),
            Rule.new("Big videos", "Videos", ["mp4"], min_size_mb=0.0),
        ],
        options=Options(skip_recent_seconds=30, ignore=["desktop.ini", "*.lnk", ".*"]),
    )
    return src, cfg, Journal(tmp_path / "journal.jsonl")


def test_plan_matches_rules_and_skips_protected(workspace):
    src, cfg, _ = workspace
    touch(src / "photo.JPG")
    touch(src / "notes.txt")
    touch(src / "shortcut.lnk")
    touch(src / "desktop.ini")
    touch(src / ".hidden")
    touch(src / "unknown.xyz")
    (src / "Some Folder").mkdir()
    touch(src / "fresh.png", age_seconds=1)

    plan = build_plan(cfg)
    names = {Path(a.source).name for a in plan.actions}
    assert names == {"photo.JPG", "notes.txt"}
    reasons = {Path(s.path).name: s.reason for s in plan.skipped}
    assert reasons["shortcut.lnk"] == "ignored by pattern"
    assert reasons["desktop.ini"] == "ignored by pattern"
    assert reasons[".hidden"] == "ignored by pattern"
    assert reasons["unknown.xyz"] == "no matching rule"
    assert "folder" in reasons["Some Folder"]
    assert "modified in the last" in reasons["fresh.png"]
    assert Path(next(a for a in plan.actions if a.rule_name == "Images").destination) == src / "Images" / "photo.JPG"


def test_apply_moves_files_and_journals(workspace):
    src, cfg, journal = workspace
    touch(src / "a.jpg", b"12345")
    touch(src / "b.txt")
    plan = build_plan(cfg)
    result = apply_plan(plan, cfg, journal=journal, trigger="test")

    assert len(result.moved) == 2 and not result.failed
    assert (src / "Images" / "a.jpg").exists()
    assert (src / "Documents" / "b.txt").exists()
    assert not (src / "a.jpg").exists()
    assert result.bytes_moved == 6
    batches = journal.batches()
    assert len(batches) == 1 and batches[0].batch_id == result.batch.batch_id


def test_undo_restores_and_removes_empty_dirs(workspace):
    src, cfg, journal = workspace
    touch(src / "a.jpg")
    apply_plan(build_plan(cfg), cfg, journal=journal)
    assert not (src / "a.jpg").exists()

    res = journal.undo()
    assert res.ok and len(res.restored) == 1
    assert (src / "a.jpg").exists()
    assert not (src / "Images").exists()
    assert journal.batches()[0].undone is True
    with pytest.raises(ValueError):
        journal.undo(journal.batches()[0].batch_id)


def test_conflict_rename_skip_overwrite(workspace, tmp_path):
    src, cfg, journal = workspace
    touch(src / "Images" / "a.jpg", b"old")
    touch(src / "a.jpg", b"new")

    cfg.options.conflict = "rename"
    r = apply_plan(build_plan(cfg), cfg, journal=journal)
    assert r.moved[0].renamed is True
    assert (src / "Images" / "a (1).jpg").read_bytes() == b"new"

    touch(src / "a.jpg", b"newer")
    cfg.options.conflict = "skip"
    r = apply_plan(build_plan(cfg), cfg, journal=journal)
    assert r.skipped_conflicts and (src / "a.jpg").exists()

    cfg.options.conflict = "overwrite"
    r = apply_plan(build_plan(cfg), cfg, journal=journal)
    assert r.moved and (src / "Images" / "a.jpg").read_bytes() == b"newer"


def test_resolve_conflict_numbers(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("1")
    (tmp_path / "f (1).txt").write_text("2")
    assert resolve_conflict(p, "rename") == tmp_path / "f (2).txt"
    assert resolve_conflict(p, "skip") is None
    assert resolve_conflict(tmp_path / "new.txt", "skip") == tmp_path / "new.txt"


def test_date_subfolders_and_patterns(tmp_path):
    src = tmp_path / "D"
    src.mkdir()
    shot = touch(src / "Screenshot 2024-01-02.png")
    other = touch(src / "holiday.png")
    rules = [
        Rule.new("Shots", "Shots", ["png"], patterns=["Screenshot*"]),
        Rule.new("Images", "Images", ["png"]),
    ]
    rules[1].date_subfolders = "year-month"
    cfg = Config(sources=[str(src)], rules=rules, options=Options(skip_recent_seconds=0))
    plan = build_plan(cfg)
    dest = {Path(a.source).name: Path(a.destination) for a in plan.actions}
    assert dest[shot.name].parent == src / "Shots"
    stamp = time.localtime(other.stat().st_mtime)
    assert dest[other.name].parent == src / "Images" / f"{stamp.tm_year:04d}" / f"{stamp.tm_mon:02d}"


def test_rule_matching_criteria(tmp_path):
    f = touch(tmp_path / "report_final.pdf", b"0" * (2 * 1024 * 1024), age_seconds=10 * 86400)
    info = FileInfo.from_path(f)
    assert rule_matches(Rule.new("a", "x", ["pdf"]), info)
    assert not rule_matches(Rule.new("b", "x", ["doc"]), info)
    assert rule_matches(Rule.new("c", "x", [], regex=r"final\.pdf$"), info)
    assert rule_matches(Rule.new("d", "x", ["pdf"], min_age_days=5), info)
    assert not rule_matches(Rule.new("e", "x", ["pdf"], min_age_days=30), info)
    assert rule_matches(Rule.new("f", "x", ["pdf"], min_size_mb=1), info)
    assert not rule_matches(Rule.new("g", "x", ["pdf"], max_size_mb=1), info)
    assert not rule_matches(Rule.new("h", "x", []), info), "empty rule must never match"


def test_unmatched_catch_all_and_missing_source(tmp_path):
    src = tmp_path / "D"
    src.mkdir()
    touch(src / "weird.xyz")
    cfg = Config(sources=[str(src), str(tmp_path / "nope")], rules=[],
                 options=Options(skip_recent_seconds=0, unmatched_destination="Other"))
    plan = build_plan(cfg)
    assert plan.missing_sources == [str(tmp_path / "nope")]
    assert len(plan.actions) == 1 and plan.actions[0].rule_name == "Other"
