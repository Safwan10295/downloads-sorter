"""Loading and validating config.yaml."""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any

import yaml

from .rules import Rule, normalize_extension
from .winutil import downloads_folder

log = logging.getLogger(__name__)

APP_NAME = "DownloadsSorter"
DEFAULT_CONFIG_TEMPLATE = Path(__file__).with_name("default_config.yaml")

DEFAULT_IN_PROGRESS = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload")
DEFAULT_SKIP = (".lnk", ".url")
DEFAULT_IGNORE_NAMES = ("desktop.ini", "thumbs.db", "~$*")

KNOWN_KEYS = {
    "watch_folder", "destination", "dry_run", "notifications", "sort_existing_on_start", "month_subfolders",
    "month_format", "fallback_folder", "settle_seconds", "min_age_seconds",
    "in_progress_extensions", "skip_extensions", "ignore_names", "skip_hidden", "rules",
}


class ConfigError(Exception):
    """config.yaml is missing something or has a bad value."""


def app_dir() -> Path:
    """%APPDATA%\\DownloadsSorter, where the config and logs live by default."""
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home() / ".config") / APP_NAME


def default_config_path() -> Path:
    return app_dir() / "config.yaml"


@dataclass
class Config:
    path: Path
    watch_folder: Path
    destination: Path
    rules: list[Rule]
    fallback_folder: str | None = "Other"
    dry_run: bool = False
    notifications: bool = True
    sort_existing_on_start: bool = True
    month_subfolders: bool = False
    month_format: str = "%Y-%m"
    settle_seconds: float = 3.0
    min_age_seconds: float = 5.0
    in_progress_extensions: tuple[str, ...] = DEFAULT_IN_PROGRESS
    skip_extensions: tuple[str, ...] = DEFAULT_SKIP
    ignore_names: tuple[str, ...] = DEFAULT_IGNORE_NAMES
    skip_hidden: bool = True

    @property
    def data_dir(self) -> Path:
        """Folder for the move log, app log and previews (next to config.yaml)."""
        return self.path.parent

    @property
    def move_log_path(self) -> Path:
        return self.data_dir / "moves.jsonl"

    @property
    def app_log_path(self) -> Path:
        return self.data_dir / "sorter.log"

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path) -> Config:
        if not isinstance(data, dict):
            raise ConfigError("The config file must be a set of 'key: value' settings.")
        unknown = sorted(set(data) - KNOWN_KEYS)
        if unknown:
            log.warning("Ignoring unknown config keys: %s", ", ".join(unknown))

        watch = resolve_folder(data.get("watch_folder") or "auto")
        dest_raw = data.get("destination")
        destination = resolve_folder(dest_raw) if dest_raw else watch

        rules_raw = data.get("rules") or []
        if not isinstance(rules_raw, list):
            raise ConfigError("'rules' must be a list (each rule starts with '- ').")
        rules = [parse_rule(raw, index) for index, raw in enumerate(rules_raw, start=1)]

        fallback = data.get("fallback_folder", "Other")
        if fallback:
            check_folder(str(fallback), "fallback_folder")

        return cls(
            path=path,
            watch_folder=watch,
            destination=destination,
            rules=rules,
            fallback_folder=str(fallback) if fallback else None,
            dry_run=_bool(data, "dry_run", False),
            notifications=_bool(data, "notifications", True),
            sort_existing_on_start=_bool(data, "sort_existing_on_start", True),
            month_subfolders=_bool(data, "month_subfolders", False),
            month_format=str(data.get("month_format") or "%Y-%m"),
            settle_seconds=_number(data, "settle_seconds", 3.0),
            min_age_seconds=_number(data, "min_age_seconds", 5.0),
            in_progress_extensions=_extensions(data, "in_progress_extensions", DEFAULT_IN_PROGRESS),
            skip_extensions=_extensions(data, "skip_extensions", DEFAULT_SKIP),
            ignore_names=tuple(str(n) for n in data.get("ignore_names", DEFAULT_IGNORE_NAMES) or ()),
            skip_hidden=_bool(data, "skip_hidden", True),
        )


def load_config(path: Path | None = None) -> Config:
    """Read config.yaml, creating it from the bundled template on first run."""
    path = path or default_config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_CONFIG_TEMPLATE, path)
        log.info("Created a default config at %s", path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML:\n{exc}") from exc

    config = Config.from_dict(data, path)
    if not config.watch_folder.is_dir():
        raise ConfigError(f"watch_folder does not exist: {config.watch_folder}")
    return config


def resolve_folder(value: object) -> Path:
    """'auto' means the Windows Downloads folder; also expands ~ and %VARS%."""
    text = str(value).strip()
    if text.lower() == "auto":
        return downloads_folder()
    return Path(os.path.expandvars(text)).expanduser()


def parse_rule(raw: object, index: int) -> Rule:
    if not isinstance(raw, dict):
        raise ConfigError(f"Rule #{index} must have settings like 'name:', 'folder:' and 'extensions:'.")
    name = str(raw.get("name") or f"Rule #{index}")

    folder = raw.get("folder")
    if not folder:
        raise ConfigError(f"Rule '{name}' needs a 'folder'.")
    check_folder(str(folder), f"Rule '{name}'")

    exts = raw.get("extensions") or []
    if isinstance(exts, str):
        exts = re.split(r"[,\s]+", exts)
    extensions = tuple(normalize_extension(e) for e in exts if str(e).strip())

    pattern = None
    if raw.get("pattern"):
        flags = 0 if raw.get("case_sensitive") else re.IGNORECASE
        try:
            pattern = re.compile(str(raw["pattern"]), flags)
        except re.error as exc:
            raise ConfigError(f"Rule '{name}' has an invalid pattern: {exc}") from exc

    if not extensions and pattern is None:
        raise ConfigError(f"Rule '{name}' needs 'extensions', a 'pattern', or both.")

    by_month = raw.get("by_month")
    if by_month is not None and not isinstance(by_month, bool):
        raise ConfigError(f"Rule '{name}': 'by_month' must be true or false.")

    return Rule(name=name, folder=str(folder), extensions=extensions, pattern=pattern, by_month=by_month)


def check_folder(folder: str, owner: str) -> None:
    if ".." in PurePath(folder).parts:
        raise ConfigError(f"{owner}: folder '{folder}' must not contain '..'.")


def _bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"'{key}' must be true or false (got {value!r}).")
    return value


def _number(data: dict[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ConfigError(f"'{key}' must be a number of seconds (got {value!r}).")
    return float(value)


def _extensions(data: dict[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = data.get(key, default)
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"'{key}' must be a list like [.part, .tmp].")
    return tuple(normalize_extension(e) for e in value)
