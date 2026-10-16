"""System tray app: the watcher plus a right-click menu."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

import pystray
from pystray import Menu, MenuItem

from . import startup
from .config import Config, ConfigError, load_config
from .icon import make_icon, save_png
from .mover import Mover, PlannedMove, UndoResult
from .movelog import MoveLog
from .notify import Notifier
from .watcher import DownloadWatcher
from .winutil import message_box, open_path

log = logging.getLogger(__name__)

TITLE = "Downloads Sorter"


def write_preview(moves: list[PlannedMove], config: Config, path: Path) -> Path:
    """Write a human-readable list of planned moves (opened in Notepad)."""
    lines = [
        f"Downloads Sorter: {len(moves)} file(s) would be moved",
        f"From: {config.watch_folder}",
        "",
    ]
    for move in moves:
        try:
            shown = move.dst.relative_to(config.destination)
        except ValueError:
            shown = move.dst
        lines.append(f"{move.src.name}\n    -> {shown}    [rule: {move.rule}]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TrayApp:
    def __init__(self, config: Config, config_path: Path | None = None) -> None:
        self.config = config
        self.config_path = config_path
        icon_png = save_png(config.data_dir / "icon.png")
        self.notifier = Notifier(icon_png)
        self.mover = Mover(config, MoveLog(config.move_log_path), self.notifier)
        self.watcher = DownloadWatcher(self.mover)
        self._busy = threading.Lock()  # one sort/undo job at a time
        self.icon = pystray.Icon("DownloadsSorter", make_icon(), self._tooltip(), menu=self._menu())

    def run(self) -> None:
        self.watcher.start()
        if self.config.sort_existing_on_start:
            self._background(self._sort_everything)()
        try:
            self.icon.run()
        finally:
            self.watcher.stop()

    # --- menu ---------------------------------------------------------------

    def _menu(self) -> Menu:
        return Menu(
            MenuItem(lambda _item: self._status(), None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("Pause sorting", self._toggle_pause, checked=lambda _item: self.watcher.paused),
            MenuItem("Sort everything now", self._background(self._sort_everything)),
            MenuItem("Undo last move", self._background(lambda: self._undo(1))),
            MenuItem("Undo last 10 moves", self._background(lambda: self._undo(10))),
            MenuItem("Undo last bulk sort", self._background(self._undo_bulk)),
            Menu.SEPARATOR,
            MenuItem("Open Downloads", lambda: open_path(self.config.watch_folder), default=True),
            MenuItem("Open config", lambda: open_path(self.config.path)),
            MenuItem("Reload config", self._reload),
            MenuItem("Open move log", lambda: self._open_log(self.config.move_log_path)),
            MenuItem("Open app log", lambda: self._open_log(self.config.app_log_path)),
            Menu.SEPARATOR,
            MenuItem("Start with Windows", self._toggle_startup, checked=lambda _item: startup.is_enabled()),
            MenuItem("Quit", self._quit),
        )

    def _status(self) -> str:
        if self.watcher.paused:
            return "Paused"
        suffix = " (dry run)" if self.config.dry_run else ""
        return f"Watching {self.config.watch_folder.name}{suffix}"

    def _tooltip(self) -> str:
        return f"{TITLE}: {self._status()}"

    def _refresh(self) -> None:
        self.icon.icon = make_icon(paused=self.watcher.paused)
        self.icon.title = self._tooltip()
        self.icon.update_menu()

    def _background(self, job: Callable[[], None]) -> Callable[[], None]:
        """Run slow menu actions off the tray thread so the menu stays responsive."""

        def start() -> None:
            if not self._busy.acquire(blocking=False):
                message_box(TITLE, "Still working on the previous action, try again in a moment.")
                return

            def run() -> None:
                try:
                    job()
                except Exception as exc:
                    log.exception("Menu action failed")
                    message_box(TITLE, f"Something went wrong:\n{exc}", warning=True)
                finally:
                    self._busy.release()

            threading.Thread(target=run, daemon=True).start()

        return start

    # --- actions ------------------------------------------------------------

    def _toggle_pause(self) -> None:
        if self.watcher.paused:
            self.watcher.resume()
            if self.config.sort_existing_on_start:  # catch up on what arrived while paused
                self._background(self._sort_everything)()
        else:
            self.watcher.pause()
        self._refresh()

    def _sort_everything(self) -> None:
        """Sort every file already sitting in the watch folder."""
        moves = self.mover.plan_existing()
        if not moves:
            log.info("Nothing to sort in %s", self.config.watch_folder)
            return
        if self.config.dry_run:
            self.mover.apply(moves, dry_run=True)
            open_path(write_preview(moves, self.config, self.config.data_dir / "sort-preview.txt"))
            self.notifier.show(TITLE, f"Dry run: {len(moves)} file(s) would be sorted (list opened in Notepad)")
            return

        done = self.mover.apply(moves, dry_run=False)
        failed = len(moves) - len(done)
        summary = f"Sorted {len(done)} file(s) into folders"
        if failed:
            summary += f", {failed} failed (see app log)"
        if self.config.notifications:
            self.notifier.show(TITLE, summary + ". Undo from the tray menu.", self.config.destination)

    def _undo(self, count: int) -> None:
        self._report_undo(self.mover.undo(count))

    def _undo_bulk(self) -> None:
        self._report_undo(self.mover.undo_last_batch())

    def _report_undo(self, results: list[UndoResult]) -> None:
        if not results:
            message_box(TITLE, "There is nothing to undo.")
            return
        restored = [r for r in results if r.ok]
        problems = [r.message for r in results if not r.ok]
        text = f"Moved {len(restored)} file(s) back."
        if problems:
            message_box(TITLE, text + "\n\n" + "\n".join(problems), warning=True)
        elif self.config.notifications:
            self.notifier.show(TITLE, text, self.config.watch_folder)
        else:
            message_box(TITLE, text)

    def _reload(self) -> None:
        try:
            config = load_config(self.config_path)
        except ConfigError as exc:
            message_box(TITLE, f"The config has a problem, so the old settings are still in use:\n\n{exc}",
                        warning=True)
            return
        self.config = config
        self.mover.config = config
        self.mover.log = MoveLog(config.move_log_path)
        self.watcher.restart()
        self._refresh()
        log.info("Config reloaded")
        if config.notifications:
            self.notifier.show(TITLE, "Config reloaded")

    def _open_log(self, path: Path) -> None:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        open_path(path)

    def _toggle_startup(self) -> None:
        try:
            if startup.is_enabled():
                startup.disable()
            else:
                startup.enable(self.config_path)
        except OSError as exc:
            message_box(TITLE, f"Could not change the startup setting:\n{exc}", warning=True)
        self._refresh()

    def _quit(self) -> None:
        self.icon.stop()
