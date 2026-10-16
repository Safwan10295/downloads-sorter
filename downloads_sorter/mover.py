"""Moving files safely: never overwrite, never delete, log everything."""

from __future__ import annotations

import errno
import logging
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .filechecks import in_progress, is_settled, skip_reason
from .movelog import MoveLog
from .notify import Notifier
from .rules import target_for

log = logging.getLogger(__name__)

COMPOUND_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")
ERROR_NOT_SAME_DEVICE = 17  # Windows error code for a move across drives


@dataclass(frozen=True)
class PlannedMove:
    src: Path
    dst: Path
    rule: str


@dataclass(frozen=True)
class UndoResult:
    original: Path
    restored: Path | None
    message: str

    @property
    def ok(self) -> bool:
        return self.restored is not None


def split_name(name: str) -> tuple[str, str]:
    """('report', '.pdf') for 'report.pdf', ('backup', '.tar.gz') for 'backup.tar.gz'."""
    lower = name.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], name[-len(suffix):]
    path = Path(name)
    return path.stem, path.suffix


def unique_path(path: Path) -> Path:
    """`path` if it's free, otherwise 'name (1).ext', 'name (2).ext', ..."""
    if not path.exists():
        return path
    stem, suffix = split_name(path.name)
    counter = 1
    while True:
        candidate = path.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def safe_move(src: Path, dst: Path) -> None:
    """Move src to dst. Raises FileExistsError rather than ever overwriting dst."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise FileExistsError(errno.EEXIST, "Destination already exists", str(dst))
    try:
        os.rename(src, dst)  # same drive: instant, and refuses to overwrite on Windows
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != ERROR_NOT_SAME_DEVICE:
            raise

    # Different drive: copy (with 'x' so we can't clobber anything), verify,
    # and only then remove the original.
    with src.open("rb") as fsrc, dst.open("xb") as fdst:
        shutil.copyfileobj(fsrc, fdst, 1024 * 1024)
    shutil.copystat(src, dst)
    if dst.stat().st_size != src.stat().st_size:
        raise OSError(f"Copy to {dst} is incomplete; the original was left in place")
    src.unlink()


def _display_folder(folder: Path, base: Path) -> str:
    try:
        return str(folder.relative_to(base)) or "."
    except ValueError:
        return str(folder)


class Mover:
    """Plans and performs moves, and undoes them, using the rules in `config`."""

    def __init__(self, config: Config, move_log: MoveLog, notifier: Notifier | None = None) -> None:
        self.config = config
        self.log = move_log
        self.notifier = notifier
        self._lock = threading.RLock()

    # --- planning -----------------------------------------------------------

    def plan(self, path: Path) -> PlannedMove | None:
        """Where `path` would go, or None if it should stay put."""
        if skip_reason(path, self.config) or in_progress(path, self.config):
            return None
        target = target_for(path, self.config)
        if target is None or target.folder == path.parent:  # already where it belongs
            return None
        return PlannedMove(path, unique_path(target.folder / path.name), target.rule_name)

    def plan_existing(self) -> list[PlannedMove]:
        """Plan moves for the files already sitting in the watch folder."""
        moves: list[PlannedMove] = []
        for path in sorted(self.config.watch_folder.iterdir(), key=lambda p: p.name.lower()):
            try:
                move = self.plan(path)
                if move is None:
                    continue
                if not is_settled(path, self.config):
                    log.info("Skipping %s: modified just now or open in another program", path.name)
                    continue
            except OSError as exc:
                log.warning("Skipping %s: %s", path.name, exc)
                continue
            moves.append(move)
        return moves

    # --- moving -------------------------------------------------------------

    def execute(self, move: PlannedMove, *, dry_run: bool, batch: str | None = None) -> Path:
        """Perform (or, in dry-run, only log) one move. Returns the final path."""
        with self._lock:
            dst = unique_path(move.dst)
            if dry_run:
                self.log.record_move(move.src, dst, move.rule, dry_run=True, batch=batch)
                log.info("[dry run] Would move %s -> %s (rule: %s)", move.src, dst, move.rule)
                return dst
            for _attempt in range(10):
                try:
                    safe_move(move.src, dst)
                    break
                except FileExistsError:
                    dst = unique_path(move.dst)  # something appeared there meanwhile
            else:
                raise FileExistsError(f"Could not find a free name for {move.dst}")
            self.log.record_move(move.src, dst, move.rule, dry_run=False, batch=batch)
            log.info("Moved %s -> %s (rule: %s)", move.src, dst, move.rule)
            return dst

    def sort_file(self, path: Path) -> Path | None:
        """Sort one newly downloaded file. Used by the watcher."""
        move = self.plan(path)
        if move is None:
            return None
        dst = self.execute(move, dry_run=self.config.dry_run)
        if self.notifier and self.config.notifications:
            folder = _display_folder(dst.parent, self.config.destination)
            self.notifier.file_sorted(dst.name, folder, dst.parent, dry_run=self.config.dry_run)
        return dst

    def apply(self, moves: list[PlannedMove], *, dry_run: bool) -> list[tuple[PlannedMove, Path]]:
        """Run a batch of planned moves, carrying on past individual failures.

        The moves share a batch id so "undo last bulk sort" can reverse them together.
        """
        batch = uuid.uuid4().hex[:12]
        done: list[tuple[PlannedMove, Path]] = []
        for move in moves:
            try:
                done.append((move, self.execute(move, dry_run=dry_run, batch=batch)))
            except OSError as exc:
                log.error("Could not move %s: %s", move.src, exc)
        return done

    # --- undo ---------------------------------------------------------------

    def undo(self, count: int = 1) -> list[UndoResult]:
        """Put the last `count` moved files back where they came from."""
        with self._lock:
            return self._undo_entries(self.log.undoable(count))

    def undo_last_batch(self) -> list[UndoResult]:
        """Undo every move from the most recent bulk sort."""
        with self._lock:
            return self._undo_entries(self.log.last_batch())

    def _undo_entries(self, entries: list[dict]) -> list[UndoResult]:
        results: list[UndoResult] = []
        for entry in entries:
            original = Path(entry["original_path"])
            current = Path(entry["new_path"])
            if not current.exists():
                # Moved or renamed by hand since; mark it so it isn't retried forever.
                self.log.record_undo(entry["id"], None, status="missing")
                results.append(UndoResult(original, None, f"{current.name} is no longer in {current.parent}"))
                continue
            restored = unique_path(original)
            try:
                safe_move(current, restored)
            except OSError as exc:
                results.append(UndoResult(original, None, f"Could not restore {current.name}: {exc}"))
                continue
            self.log.record_undo(entry["id"], restored)
            log.info("Undo: moved %s back to %s", current, restored)
            results.append(UndoResult(original, restored, f"Restored {restored.name}"))
        return results
