"""Watches the Downloads folder and sorts files once they've finished downloading.

watchdog tells us when something appears. We don't move it straight away:
each new file sits in a queue until its size and timestamp have been stable
for `settle_seconds`, no browser temp file sits next to it, and no other
program has it open.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .config import Config
from .filechecks import in_progress, is_settled, skip_reason
from .mover import Mover

log = logging.getLogger(__name__)


@dataclass
class _Pending:
    path: Path
    size: int = -1
    mtime: float = 0.0
    stable_since: float = 0.0
    failures: int = 0


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: DownloadWatcher) -> None:
        self.watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.watcher.enqueue(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.watcher.enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        # Browsers download to "file.pdf.crdownload" and rename it when done.
        if not event.is_directory:
            self.watcher.enqueue(event.dest_path)


class DownloadWatcher:
    POLL_SECONDS = 1.0
    MAX_FAILURES = 10
    # Files put back by "undo" are left alone for this long.
    RESTORE_GRACE_SECONDS = 600

    def __init__(self, mover: Mover) -> None:
        self.mover = mover
        self._pending: dict[Path, _Pending] = {}
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._observer: Observer | None = None
        self._thread: threading.Thread | None = None

    @property
    def config(self) -> Config:
        return self.mover.config

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        folder = self.config.watch_folder
        self._stop.clear()
        self._observer = Observer()
        self._observer.schedule(_Handler(self), str(folder), recursive=False)
        self._observer.start()
        self._thread = threading.Thread(target=self._run, name="settle-checker", daemon=True)
        self._thread.start()
        mode = " (dry run)" if self.config.dry_run else ""
        log.info("Watching %s%s", folder, mode)

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with self._lock:
            self._pending.clear()

    def restart(self) -> None:
        """Pick up a changed config (e.g. a new watch folder)."""
        self.stop()
        self.start()

    def pause(self) -> None:
        self._paused.set()
        with self._lock:
            self._pending.clear()
        log.info("Paused")

    def resume(self) -> None:
        self._paused.clear()
        log.info("Resumed")

    # --- queue --------------------------------------------------------------

    def enqueue(self, raw_path: str | bytes) -> None:
        if self.paused:
            return
        path = Path(raw_path.decode() if isinstance(raw_path, bytes) else raw_path)
        if path.parent != self.config.watch_folder:
            return  # only top-level files; our own sub-folders are off limits
        if path.name.lower().endswith(self.config.in_progress_extensions):
            return  # we'll hear about it again when it's renamed
        with self._lock:
            self._pending.setdefault(path, _Pending(path))

    def _run(self) -> None:
        while not self._stop.wait(self.POLL_SECONDS):
            if self.paused:
                continue
            with self._lock:
                items = list(self._pending.values())
            for item in items:
                if self._stop.is_set() or self.paused:
                    break
                try:
                    finished = self._check(item)
                except Exception:
                    log.exception("Unexpected error while handling %s", item.path)
                    finished = True
                if finished:
                    with self._lock:
                        self._pending.pop(item.path, None)

    def _check(self, item: _Pending) -> bool:
        """Try to sort one queued file. Returns True when it can leave the queue."""
        path = item.path
        try:
            stat = path.stat()
        except FileNotFoundError:
            return True  # renamed, moved or deleted by someone else
        if skip_reason(path, self.config):
            return True
        if in_progress(path, self.config):
            return False

        now = time.monotonic()
        if (stat.st_size, stat.st_mtime) != (item.size, item.mtime):
            item.size, item.mtime, item.stable_since = stat.st_size, stat.st_mtime, now
            return False
        if now - item.stable_since < self.config.settle_seconds:
            return False
        if not is_settled(path, self.config):
            return False

        if self.mover.log.recently_restored(path, self.RESTORE_GRACE_SECONDS):
            log.info("Leaving %s alone: it was just restored by undo", path.name)
            return True

        try:
            self.mover.sort_file(path)
        except OSError as exc:
            item.failures += 1
            if item.failures >= self.MAX_FAILURES:
                log.error("Giving up on %s after %d tries: %s", path.name, item.failures, exc)
                return True
            log.warning("Could not move %s yet (%s); will retry", path.name, exc)
            item.stable_since = now
            return False
        return True
