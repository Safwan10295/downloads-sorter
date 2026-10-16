"""Windows toast notifications (via winotify), batched so a burst of downloads
produces one toast instead of twenty."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

APP_ID = "Downloads Sorter"


@dataclass(frozen=True)
class _Sorted:
    name: str
    folder_label: str
    folder: Path
    dry_run: bool


class Notifier:
    BATCH_SECONDS = 2.0

    def __init__(self, icon: Path | None = None) -> None:
        self.icon = icon
        self._lock = threading.Lock()
        self._batch: list[_Sorted] = []
        self._timer: threading.Timer | None = None

    def file_sorted(self, name: str, folder_label: str, folder: Path, *, dry_run: bool = False) -> None:
        with self._lock:
            self._batch.append(_Sorted(name, folder_label, folder, dry_run))
            if self._timer is None:
                self._timer = threading.Timer(self.BATCH_SECONDS, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            batch, self._batch, self._timer = self._batch, [], None
        if not batch:
            return
        verb = "Would sort" if any(item.dry_run for item in batch) else "Sorted"
        if len(batch) == 1:
            item = batch[0]
            self.show(f"{verb} {item.name}", f"→ {item.folder_label}", item.folder)
            return
        names = ", ".join(item.name for item in batch[:3])
        more = f" and {len(batch) - 3} more" if len(batch) > 3 else ""
        folders = {item.folder for item in batch}
        self.show(f"{verb} {len(batch)} files", names + more, folders.pop() if len(folders) == 1 else None)

    def show(self, title: str, message: str, folder: Path | None = None) -> None:
        """Show a toast in the background. Failures are logged, never raised."""
        threading.Thread(target=self._show, args=(title, message, folder), daemon=True).start()

    def _show(self, title: str, message: str, folder: Path | None) -> None:
        try:
            from winotify import Notification
        except ImportError:
            log.debug("winotify is not installed; skipping notification: %s", title)
            return
        try:
            toast = Notification(
                app_id=APP_ID,
                title=title,
                msg=message,
                icon=str(self.icon) if self.icon and self.icon.exists() else "",
            )
            if folder is not None:
                toast.add_actions(label="Open folder", launch=folder.as_uri())
            toast.show()
        except Exception:  # a broken toast must never take the sorter down
            log.warning("Could not show notification", exc_info=True)
