"""Append-only JSON log of every move, used for undo.

The file is JSON Lines: one JSON object per line. Nothing is ever rewritten or
removed; undoing a move appends an "undo" record that points at the move's id.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

Entry = dict[str, Any]


def _now() -> datetime:
    return datetime.now().astimezone()


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


class MoveLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record_move(
        self, original: Path, new: Path, rule: str, *, dry_run: bool, batch: str | None = None
    ) -> Entry:
        entry: Entry = {
            "action": "move",
            "id": uuid.uuid4().hex[:12],
            "timestamp": _now().isoformat(timespec="seconds"),
            "original_path": str(original),
            "new_path": str(new),
            "rule": rule,
            "dry_run": dry_run,
        }
        if batch:
            entry["batch"] = batch  # set for bulk "sort existing" runs, so they can be undone together
        self._append(entry)
        return entry

    def record_undo(self, move_id: str, restored_path: Path | None, *, status: str = "restored") -> Entry:
        entry: Entry = {
            "action": "undo",
            "id": move_id,
            "timestamp": _now().isoformat(timespec="seconds"),
            "restored_path": str(restored_path) if restored_path else None,
            "status": status,
        }
        self._append(entry)
        return entry

    def entries(self) -> list[Entry]:
        if not self.path.exists():
            return []
        result: list[Entry] = []
        with self._lock, self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("Skipping unreadable line %d in %s", number, self.path)
        return result

    def undoable(self, count: int | None = None) -> list[Entry]:
        """The most recent real (non dry-run) moves that have not been undone, newest first."""
        entries = self.entries()
        undone = {e.get("id") for e in entries if e.get("action") == "undo"}
        moves = [
            e for e in entries
            if e.get("action") == "move" and not e.get("dry_run") and e.get("id") not in undone
        ]
        return moves[::-1][:count]

    def last_batch(self) -> list[Entry]:
        """The not-yet-undone moves from the most recent bulk sort, newest first."""
        moves = self.undoable()
        batch = next((e["batch"] for e in moves if e.get("batch")), None)
        return [e for e in moves if batch and e.get("batch") == batch]

    def recently_restored(self, path: Path, within_seconds: float) -> bool:
        """True if undo put a file at `path` recently, so the watcher should leave it be."""
        cutoff = _now() - timedelta(seconds=within_seconds)
        for entry in reversed(self.entries()):
            if entry.get("action") != "undo" or not entry.get("restored_path"):
                continue
            try:
                when = datetime.fromisoformat(entry["timestamp"])
            except (KeyError, ValueError):
                continue
            if when < cutoff:
                return False  # entries are in time order, nothing newer below
            if _same_path(entry["restored_path"], path):
                return True
        return False

    def _append(self, entry: Entry) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
