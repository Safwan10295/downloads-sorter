"""Rule matching: decide which folder a file belongs in.

Everything here is pure (no file moves), so it is easy to unit test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from .config import Config

FALLBACK_RULE_NAME = "fallback"


@dataclass(frozen=True)
class Rule:
    """One entry from the `rules:` list in config.yaml.

    A rule matches when every condition it defines matches:
    `extensions` (any of them) and/or `pattern` (regex searched in the filename).
    """

    name: str
    folder: str
    extensions: tuple[str, ...] = ()
    pattern: re.Pattern[str] | None = None
    by_month: bool | None = None  # None means "use the global month_subfolders setting"

    def matches(self, filename: str) -> bool:
        if not self.extensions and self.pattern is None:
            return False
        if self.extensions and not filename.lower().endswith(self.extensions):
            return False
        if self.pattern is not None and not self.pattern.search(filename):
            return False
        return True


@dataclass(frozen=True)
class Target:
    """Where a file should go, and which rule sent it there."""

    folder: Path
    rule_name: str


def normalize_extension(ext: object) -> str:
    """Turn 'PDF', '.pdf' or ' pdf ' into '.pdf'."""
    text = str(ext).strip().lower()
    return text if text.startswith(".") else f".{text}"


def find_rule(filename: str, rules: Sequence[Rule]) -> Rule | None:
    """Return the first rule that matches, checking top to bottom."""
    return next((rule for rule in rules if rule.matches(filename)), None)


def target_for(path: Path, config: Config, when: datetime | None = None) -> Target | None:
    """Work out the destination folder for `path`, or None to leave it alone.

    `when` is the date used for month sub-folders; it defaults to the file's
    modification time (roughly when it was downloaded).
    """
    rule = find_rule(path.name, config.rules)
    if rule is not None:
        subfolder, rule_name = rule.folder, rule.name
        by_month = config.month_subfolders if rule.by_month is None else rule.by_month
    elif config.fallback_folder:
        subfolder, rule_name = config.fallback_folder, FALLBACK_RULE_NAME
        by_month = config.month_subfolders
    else:
        return None

    folder = config.destination / subfolder
    if by_month:
        if when is None:
            when = datetime.fromtimestamp(path.stat().st_mtime)
        folder = folder / when.strftime(config.month_format)
    return Target(folder, rule_name)
