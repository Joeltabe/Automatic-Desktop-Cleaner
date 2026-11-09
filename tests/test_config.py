import json
from pathlib import Path

from desktop_cleaner.config import (config_from_dict, config_to_dict, default_config, ensure_config, load_config,
                                    migrate_legacy, save_config)


def test_legacy_config_migrates(tmp_path: Path):
    legacy = {"rules": [
        {"file_type": "txt", "destination_folder": "Documents"},
        {"file_type": "docx", "destination_folder": "Documents"},
        {"file_type": "jpg", "destination_folder": "Images"},
    ]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(legacy))
    cfg = load_config(p)
    assert cfg.version == 2
    assert cfg.sources == ["~/Desktop"]
    by_dest = {r.destination: r.match.extensions for r in cfg.rules}
    assert by_dest == {"Documents": ["docx", "txt"], "Images": ["jpg"]}


def test_roundtrip_and_normalisation(tmp_path: Path):
    cfg = default_config()
    cfg.rules[0].match.extensions = [".PNG", "jpg ", "jpg"]
    cfg.options.conflict = "bogus"
    data = config_to_dict(cfg)
    back = config_from_dict(data)
    assert back.rules[0].match.extensions == ["jpg", "png"]
    assert back.options.conflict == "rename"
    p = save_config(back, tmp_path / "c.json")
    assert load_config(p).rules[0].name == cfg.rules[0].name


def test_ensure_config_creates_defaults(tmp_path: Path):
    p = tmp_path / "nested" / "config.json"
    cfg, path, created = ensure_config(p)
    assert created and path.exists() and len(cfg.rules) > 5
    cfg2, _, created2 = ensure_config(p)
    assert not created2 and len(cfg2.rules) == len(cfg.rules)


def test_unknown_keys_are_ignored():
    cfg = config_from_dict({"version": 2, "rules": [{"name": "X", "destination": "X", "match": {"extensions": ["a"], "nope": 1}, "extra": True}],
                            "options": {"conflict": "skip", "mystery": 1}})
    assert cfg.rules[0].match.extensions == ["a"]
    assert cfg.options.conflict == "skip"
    assert migrate_legacy({"rules": []}).rules == []
