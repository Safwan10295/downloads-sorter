"""Command line entry point. With no command it starts the tray app."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, startup
from .config import Config, ConfigError, default_config_path, load_config
from .mover import Mover
from .movelog import MoveLog
from .rules import target_for
from .winutil import acquire_single_instance, message_box

log = logging.getLogger("downloads_sorter")

HAS_CONSOLE = sys.stdout is not None  # False in the windowed .exe / pythonw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="downloads-sorter",
        description="Automatically sort your Downloads folder into sub-folders.",
    )
    parser.add_argument("--config", type=Path, help=f"config file to use (default: {default_config_path()})")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("tray", help="run in the system tray (the default)")
    sub.add_parser("watch", help="watch and sort in this console window, without a tray icon")

    sort = sub.add_parser("sort-existing", help="tidy up files already in Downloads (shows a dry run first)")
    sort.add_argument("-y", "--yes", action="store_true", help="move without asking after the preview")
    sort.add_argument("--dry-run", action="store_true", help="only show what would happen")

    undo = sub.add_parser("undo", help="move recently sorted files back")
    undo.add_argument("-n", "--count", type=int, default=1, help="how many moves to undo (default 1)")
    undo.add_argument("--bulk", action="store_true", help="undo the whole last bulk sort instead")

    start = sub.add_parser("startup", help="turn run-on-Windows-startup on or off")
    start.add_argument("state", choices=["on", "off", "status"])

    explain = sub.add_parser("explain", help="show which rule a filename would match (for testing rules)")
    explain.add_argument("filenames", nargs="+")

    sub.add_parser("paths", help="show where the config and logs live")
    return parser


def setup_logging(log_file: Path, console_level: int) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handlers: list[logging.Handler] = [file_handler]
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setLevel(console_level)
        console.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        handlers.append(console)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "tray"
    config_path: Path = args.config or default_config_path()
    noisy = command in ("tray", "watch")
    setup_logging(config_path.parent / "sorter.log", logging.INFO if noisy else logging.WARNING)

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        log.error("Config problem in %s: %s", config_path, exc)
        if not HAS_CONSOLE:
            message_box("Downloads Sorter", f"Config problem in {config_path}:\n\n{exc}", warning=True)
        return 2

    handlers = {
        "tray": cmd_tray,
        "watch": cmd_watch,
        "sort-existing": cmd_sort_existing,
        "undo": cmd_undo,
        "startup": cmd_startup,
        "explain": cmd_explain,
        "paths": cmd_paths,
    }
    return handlers[command](config, args)


def cmd_tray(config: Config, args: argparse.Namespace) -> int:
    from .tray import TrayApp  # imported here so CLI commands don't need pystray

    instance = acquire_single_instance("DownloadsSorterTray")
    if instance is None:
        message_box("Downloads Sorter", "Downloads Sorter is already running (look for it in the system tray).")
        return 1
    TrayApp(config, args.config).run()
    return 0


def cmd_watch(config: Config, args: argparse.Namespace) -> int:
    from .icon import save_png
    from .notify import Notifier
    from .watcher import DownloadWatcher

    notifier = Notifier(save_png(config.data_dir / "icon.png"))
    watcher = DownloadWatcher(Mover(config, MoveLog(config.move_log_path), notifier))
    watcher.start()
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
    return 0


def cmd_sort_existing(config: Config, args: argparse.Namespace) -> int:
    mover = Mover(config, MoveLog(config.move_log_path))
    moves = mover.plan_existing()
    if not moves:
        print(f"Nothing to sort in {config.watch_folder}.")
        return 0

    print(f"Dry run: {len(moves)} file(s) in {config.watch_folder} would be moved:\n")
    for move in moves:
        print(f"  {move.src.name}\n      -> {move.dst}   [{move.rule}]")
    print()

    if args.dry_run or config.dry_run:
        if config.dry_run:
            mover.apply(moves, dry_run=True)  # dry-run mode: log what would happen
            print("dry_run is on in the config, so nothing was moved.")
        return 0
    if not args.yes:
        if not sys.stdin or not sys.stdin.isatty():
            print("Re-run with --yes to move these files.")
            return 0
        if input(f"Move these {len(moves)} file(s)? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing moved.")
            return 0

    done = mover.apply(moves, dry_run=False)
    print(f"Moved {len(done)} of {len(moves)} file(s). Undo with: undo --bulk")
    return 0 if len(done) == len(moves) else 1


def cmd_undo(config: Config, args: argparse.Namespace) -> int:
    mover = Mover(config, MoveLog(config.move_log_path))
    results = mover.undo_last_batch() if args.bulk else mover.undo(max(args.count, 1))
    if not results:
        print("There is nothing to undo.")
        return 0
    for result in results:
        print(("  ok   " if result.ok else "  FAIL ") + result.message)
    return 0 if all(r.ok for r in results) else 1


def cmd_startup(config: Config, args: argparse.Namespace) -> int:
    if args.state == "on":
        print(f"Will start with Windows using:\n  {startup.enable(args.config)}")
    elif args.state == "off":
        print("Removed from startup." if startup.disable() else "It wasn't set to start with Windows.")
    else:
        command = startup.registered_command()
        print(f"On:\n  {command}" if command else "Off")
    return 0


def cmd_explain(config: Config, args: argparse.Namespace) -> int:
    for name in args.filenames:
        target = target_for(config.watch_folder / Path(name).name, config, when=datetime.now())
        if target is None:
            print(f"{name}\n    no rule matches, and fallback_folder is off: stays where it is")
        else:
            print(f"{name}\n    rule '{target.rule_name}' -> {target.folder}")
    return 0


def cmd_paths(config: Config, args: argparse.Namespace) -> int:
    print(f"Config:      {config.path}")
    print(f"Move log:    {config.move_log_path}")
    print(f"App log:     {config.app_log_path}")
    print(f"Watching:    {config.watch_folder}")
    print(f"Sorting to:  {config.destination}")
    return 0
