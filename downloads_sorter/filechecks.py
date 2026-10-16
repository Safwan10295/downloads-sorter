"""Checks that decide whether a file may be touched yet."""

from __future__ import annotations

import stat
import time
from fnmatch import fnmatch
from pathlib import Path

from .config import Config
from .winutil import is_locked


def skip_reason(path: Path, config: Config) -> str | None:
    """Why this path should never be sorted, or None if it is a candidate."""
    if path.is_symlink():
        return "symlink"
    if not path.is_file():
        return "not a file"
    lower = path.name.lower()
    if lower.endswith(config.in_progress_extensions):
        return "download in progress"
    if lower.endswith(config.skip_extensions):
        return "shortcut"
    if any(fnmatch(lower, pattern.lower()) for pattern in config.ignore_names):
        return "ignored name"
    if config.skip_hidden and is_hidden(path):
        return "hidden file"
    return None


def in_progress(path: Path, config: Config) -> bool:
    """True if a browser is still writing this file.

    Firefox, for example, creates an empty `file.pdf` as a placeholder and
    downloads into `file.pdf.part` next to it.
    """
    return any(path.with_name(path.name + ext).exists() for ext in config.in_progress_extensions)


def is_settled(path: Path, config: Config) -> bool:
    """Not modified in the last few seconds, and not open in another program."""
    age = time.time() - path.stat().st_mtime
    if age < config.min_age_seconds:
        return False
    return not is_locked(path)


def is_hidden(path: Path) -> bool:
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    hidden = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    system = getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)
    return bool(attributes & (hidden | system))
