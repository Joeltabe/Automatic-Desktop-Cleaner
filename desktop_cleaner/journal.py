"""Append-only JSON-lines journal of every move, powering undo and statistics."""
from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import journal_path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class MoveRecord:
    src: str
    dest: str
    rule_id: str
    rule_name: str
    size: int
    renamed: bool = False


@dataclass
class Batch:
    batch_id: str
    timestamp: str
    sources: list[str]
    moves: list[MoveRecord] = field(default_factory=list)
    trigger: str = "manual"        # manual | watch | cli | gui
    undone: bool = False
    undone_at: str | None = None

    @property
    def bytes_moved(self) -> int:
        return sum(m.size for m in self.moves)

    @staticmethod
    def new(sources: list[str], trigger: str = "manual") -> "Batch":
        return Batch(batch_id=uuid.uuid4().hex[:10], timestamp=now_iso(), sources=sources, trigger=trigger)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = "batch"
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Batch":
        moves = [MoveRecord(**{k: v for k, v in m.items() if k in MoveRecord.__dataclass_fields__}) for m in d.get("moves", [])]
        return Batch(
            batch_id=d["batch_id"], timestamp=d.get("timestamp", ""), sources=list(d.get("sources", [])),
            moves=moves, trigger=d.get("trigger", "manual"), undone=bool(d.get("undone", False)),
            undone_at=d.get("undone_at"),
        )


@dataclass
class UndoResult:
    batch_id: str
    restored: list[MoveRecord]
    failed: list[tuple[MoveRecord, str]]

    @property
    def ok(self) -> bool:
        return not self.failed


class Journal:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else journal_path()

    # ------------------------------------------------------------------ writing
    def append_batch(self, batch: Batch) -> None:
        if not batch.moves:
            return
        self._append(batch.to_dict())

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ reading
    def _records(self) -> Iterator[dict[str, Any]]:
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

    def batches(self) -> list[Batch]:
        """All batches, oldest first, with undo markers folded in."""
        result: dict[str, Batch] = {}
        for rec in self._records():
            kind = rec.get("type", "batch")
            if kind == "batch":
                b = Batch.from_dict(rec)
                result[b.batch_id] = b
            elif kind == "undo":
                b = result.get(rec.get("batch_id", ""))
                if b:
                    b.undone = True
                    b.undone_at = rec.get("timestamp")
        return list(result.values())

    def last_batch(self, include_undone: bool = False) -> Batch | None:
        for b in reversed(self.batches()):
            if include_undone or not b.undone:
                return b
        return None

    def get(self, batch_id: str) -> Batch | None:
        for b in self.batches():
            if b.batch_id == batch_id or b.batch_id.startswith(batch_id):
                return b
        return None

    # ------------------------------------------------------------------ undo
    def undo(self, batch_id: str | None = None) -> UndoResult:
        batch = self.get(batch_id) if batch_id else self.last_batch()
        if batch is None:
            raise LookupError("No batch to undo." if not batch_id else f"Batch {batch_id!r} not found.")
        if batch.undone:
            raise ValueError(f"Batch {batch.batch_id} was already undone.")

        restored: list[MoveRecord] = []
        failed: list[tuple[MoveRecord, str]] = []
        for move in reversed(batch.moves):
            dest, src = Path(move.dest), Path(move.src)
            if not dest.exists():
                failed.append((move, "file no longer at destination"))
                continue
            if src.exists():
                failed.append((move, "a file already exists at the original location"))
                continue
            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(src))
                restored.append(move)
            except OSError as exc:
                failed.append((move, str(exc)))

        # Remove now-empty destination folders we created (best effort, never the source).
        for move in restored:
            parent = Path(move.dest).parent
            try:
                while parent != parent.parent and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
            except OSError:
                pass

        self._append({"type": "undo", "batch_id": batch.batch_id, "timestamp": now_iso(),
                      "restored": len(restored), "failed": len(failed)})
        return UndoResult(batch_id=batch.batch_id, restored=restored, failed=failed)

    def clear(self) -> None:
        if self.path.exists():
            os.remove(self.path)
