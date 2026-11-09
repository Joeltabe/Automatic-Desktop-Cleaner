import os
import time
from pathlib import Path

import pytest

from desktop_cleaner.config import Config, config_from_dict, config_to_dict, default_config
from desktop_cleaner.space import junk
from desktop_cleaner.space.cleaner import SpaceLog, execute, mode_for, remove_paths
from desktop_cleaner.space.common import Cancel, Cancelled, category_of, dir_size
from desktop_cleaner.space.disks import list_volumes
from desktop_cleaner.space.duplicates import find_duplicates
from desktop_cleaner.space.junk import JunkGroup, JunkItem, analyze
from desktop_cleaner.space.scanner import scan_tree
from desktop_cleaner.space.trash import delete_permanently


def _write(path: Path, size: int, days_old: float = 0, content: bytes | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else os.urandom(size))
    if days_old:
        ts = time.time() - days_old * 86400
        os.utime(path, (ts, ts))
    return path


def _age(path: Path, days_old: float) -> None:
    ts = time.time() - days_old * 86400
    os.utime(path, (ts, ts))


# --------------------------------------------------------------------------- config

def test_space_options_roundtrip():
    cfg = default_config()
    cfg.space.stale_project_days = 10
    cfg.space.project_roots = ["~/code"]
    back = config_from_dict(config_to_dict(cfg))
    assert back.space.stale_project_days == 10 and back.space.project_roots == ["~/code"]
    assert config_from_dict({"version": 2}).space.permanent_delete is False


# --------------------------------------------------------------------------- scanner

def test_scan_tree_sizes_and_largest(tmp_path: Path):
    _write(tmp_path / "a" / "big.mp4", 5000)
    _write(tmp_path / "a" / "b" / "mid.zip", 3000)
    _write(tmp_path / "small.txt", 100)
    res = scan_tree(tmp_path)
    assert res.bytes == 8100 and res.files == 3 and res.dirs == 2
    assert res.root.file_bytes == 100
    a = next(c for c in res.root.children if c.name == "a")
    assert a.size == 8000 and a.files == 2
    assert res.largest[0]["name"] == "big.mp4" and res.largest[0]["category"] == "Video"
    cats = {t["category"]: t["bytes"] for t in res.by_type}
    assert cats["Video"] == 5000 and cats["Archives"] == 3000
    d = res.to_dict(depth=2)
    assert d["root"]["children"][0]["name"] == "a" and d["root"]["children"][0]["children"][0]["name"] == "b"
    assert res.root.find(str(tmp_path / "a" / "b")).size == 3000


def test_scan_tree_cancel(tmp_path: Path):
    for i in range(600):
        _write(tmp_path / f"f{i}.txt", 1)
    cancel = Cancel()
    cancel.set()
    with pytest.raises(Cancelled):
        scan_tree(tmp_path, cancel=cancel)


# --------------------------------------------------------------------------- duplicates

def test_find_duplicates_groups_identical_files_only(tmp_path: Path):
    payload = os.urandom(200_000)
    _write(tmp_path / "x" / "one.bin", 0, days_old=10, content=payload)
    _write(tmp_path / "y" / "copy.bin", 0, days_old=1, content=payload)
    _write(tmp_path / "z" / "same-size-different.bin", 0, content=os.urandom(200_000))
    _write(tmp_path / "tiny.bin", 0, content=b"x" * 10)
    res = find_duplicates([tmp_path], min_bytes=1000)
    assert len(res.groups) == 1
    g = res.groups[0]
    assert {Path(f.path).name for f in g.files} == {"one.bin", "copy.bin"}
    assert g.suggested_keep().endswith("one.bin")  # oldest copy is kept
    assert g.reclaimable == 200_000
    assert res.to_dict()["duplicates"] == 1


# --------------------------------------------------------------------------- trash / cleaner

def test_delete_permanently_handles_readonly(tmp_path: Path):
    f = _write(tmp_path / "ro" / "locked.txt", 10)
    os.chmod(f, 0o444)
    res = delete_permanently(str(tmp_path / "ro"))
    assert res.ok and res.freed == 10 and not (tmp_path / "ro").exists()


def test_mode_for_confidence():
    safe = JunkGroup("temp", "t", "d", "safe", "temp")
    review = JunkGroup("inst", "t", "d", "review", "installers")
    manual = JunkGroup("sys", "t", "d", "manual", "system")
    item = JunkItem(path="x", size=1, kind="contents")
    assert mode_for(safe, item, False) == "contents"
    assert mode_for(safe, JunkItem(path="x", size=1), False) == "delete"
    assert mode_for(review, JunkItem(path="x", size=1), False) == "trash"
    assert mode_for(review, JunkItem(path="x", size=1), True) == "delete"
    assert mode_for(manual, JunkItem(path="x", size=1), True) == "skip"
    assert mode_for(review, JunkItem(path="Recycle Bin", size=1, kind="recycle_bin"), False) == "empty_bin"


def test_temp_detector_and_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    temp = tmp_path / "Temp"
    old = _write(temp / "old-installer-cache" / "blob.bin", 4096, days_old=3)
    _age(temp / "old-installer-cache", 3)
    fresh = _write(temp / "fresh.tmp", 2048, days_old=0)
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setattr(junk, "local_appdata", lambda: tmp_path / "nope")
    cfg = Config(sources=[str(tmp_path / "Desktop")])
    (tmp_path / "Desktop").mkdir()
    progress: list[dict] = []
    a = analyze(cfg, progress=progress.append, only=["temp"])
    g = a.group("temp")
    assert g is not None and g.confidence == "safe"
    assert [Path(i.path).name for i in g.items] == ["old-installer-cache"]
    assert g.size == 4096 and any(p.get("phase") == "group" for p in progress)
    log = SpaceLog(tmp_path / "space.jsonl")
    res = execute(a, ["temp"], [], log=log, trigger="test")
    assert res.freed == 4096 and res.removed == 1 and not res.failed
    assert not old.exists() and fresh.exists()  # anything younger than a day is left alone
    summary = log.summary()
    assert summary["freed_total"] == 4096 and summary["cleans"] == 1 and summary["by_group"][0]["id"] == "temp"


def test_project_artifacts_detector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj = tmp_path / "code" / "webapp"
    _write(proj / "package.json", 50, days_old=120)
    _write(proj / "src" / "index.js", 50, days_old=120)
    _write(proj / "node_modules" / "left-pad" / "index.js", 400_000)
    _age(proj / "src", 120)
    _age(proj, 120)
    # a folder named node_modules without package.json beside it is not an artifact
    _write(tmp_path / "code" / "random" / "node_modules" / "x.js", 400_000)
    cfg = Config(sources=[])
    cfg.space.project_roots = [str(tmp_path / "code")]
    cfg.space.stale_project_days = 60
    a = analyze(cfg, only=["projects"])
    g = a.group("projects")
    assert g is not None and len(g.items) == 1
    item = g.items[0]
    assert item.path.endswith("node_modules") and "webapp" in item.path
    assert item.stale is True and "npm install" in item.detail


def test_old_installers_and_downloads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dl = tmp_path / "Downloads"
    _write(dl / "setup-old.exe", 1000, days_old=45)
    _write(dl / "setup-new.exe", 1000, days_old=2)
    _write(dl / "movie.mkv", 3 * 1024 * 1024, days_old=120)
    monkeypatch.setattr(junk, "_downloads", lambda: dl)
    cfg = Config(sources=[])
    cfg.space.large_file_mb = 1
    cfg.space.old_download_days = 90
    a = analyze(cfg, only=["installers", "downloads"])
    assert [Path(i.path).name for i in a.group("installers").items] == ["setup-old.exe"]
    assert [Path(i.path).name for i in a.group("downloads").items] == ["movie.mkv"]


def test_remove_paths_permanent_logs(tmp_path: Path):
    f = _write(tmp_path / "dupe.bin", 500)
    log = SpaceLog(tmp_path / "log.jsonl")
    res = remove_paths([str(f), str(tmp_path / "missing.bin")], permanent=True, log=log, label="duplicates")
    assert res.freed == 500 and res.removed == 1 and res.failed[0]["error"] == "no longer exists"
    assert log.summary()["by_group"][0]["id"] == "duplicates"


# --------------------------------------------------------------------------- misc

def test_category_and_dir_size(tmp_path: Path):
    assert category_of("movie.MKV") == "Video" and category_of("weird.xyz") == "Other" and category_of("noext") == "Other"
    _write(tmp_path / "a.txt", 10)
    _write(tmp_path / "sub" / "b.txt", 20)
    assert dir_size(tmp_path) == (30, 2)


def test_list_volumes_has_system_volume():
    vols = list_volumes()
    assert vols and vols[0]["total"] > 0 and "percent" in vols[0]
