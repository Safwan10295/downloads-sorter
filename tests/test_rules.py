from datetime import datetime
from pathlib import Path

import pytest
import yaml

from downloads_sorter.config import DEFAULT_CONFIG_TEMPLATE, Config, ConfigError
from downloads_sorter.rules import FALLBACK_RULE_NAME, find_rule, target_for

OCT_2026 = datetime(2026, 10, 16)


def make_config(tmp_path: Path, **overrides) -> Config:
    data = yaml.safe_load(DEFAULT_CONFIG_TEMPLATE.read_text(encoding="utf-8"))
    data["watch_folder"] = str(tmp_path)
    data.update(overrides)
    return Config.from_dict(data, tmp_path / "config.yaml")


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


def folder_for(config: Config, filename: str) -> Path | None:
    target = target_for(config.watch_folder / filename, config, when=OCT_2026)
    return None if target is None else target.folder.relative_to(config.destination)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("holiday.JPG", Path("Images/2026-10")),  # case-insensitive, Images sorts by month
        ("song.mp3", Path("Audio")),
        ("report.pdf", Path("Documents")),
        ("budget.xlsx", Path("Spreadsheets")),
        ("pitch.pptx", Path("Presentations")),
        ("backup.tar.gz", Path("Archives")),
        ("setup.msi", Path("Installers")),
        ("script.py", Path("Code")),
        ("Inter.woff2", Path("Fonts")),
        ("mystery.xyz", Path("Other")),  # fallback
        ("no_extension", Path("Other")),
    ],
)
def test_extension_rules(config: Config, filename: str, expected: Path) -> None:
    assert folder_for(config, filename) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Invoice-2026-001.pdf", Path("Documents/Finance")),
        ("amazon_RECEIPT.png", Path("Documents/Finance")),  # pattern beats the Images rule
        ("Lecture 4 - Graphs.pptx", Path("Uni")),
        ("week3_notes.docx", Path("Uni")),
    ],
)
def test_pattern_rules_win_over_extensions(config: Config, filename: str, expected: Path) -> None:
    assert folder_for(config, filename) == expected


def test_first_match_wins(config: Config) -> None:
    # Matches both Finance and Uni; Finance is higher up.
    assert find_rule("week 2 invoice.pdf", config.rules).name == "Finance"


def test_rule_with_extension_and_pattern_needs_both(tmp_path: Path) -> None:
    config = make_config(tmp_path, rules=[
        {"name": "Bank PDFs", "pattern": "bank", "extensions": ["pdf"], "folder": "Bank"},
    ])
    assert find_rule("bank statement.pdf", config.rules).name == "Bank PDFs"
    assert find_rule("bank statement.png", config.rules) is None
    assert find_rule("statement.pdf", config.rules) is None


def test_extensions_must_match_the_end_of_the_name(config: Config) -> None:
    assert find_rule("pdf-reader.zip", config.rules).name == "Archives"
    assert find_rule("photo.png.exe", config.rules).name == "Installers"


def test_case_sensitive_pattern(tmp_path: Path) -> None:
    config = make_config(tmp_path, rules=[
        {"name": "CV", "pattern": "CV", "case_sensitive": True, "folder": "CV"},
    ])
    assert find_rule("My CV.pdf", config.rules) is not None
    assert find_rule("recv.log", config.rules) is None


def test_month_subfolders_global_and_rule_override(tmp_path: Path) -> None:
    config = make_config(tmp_path, month_subfolders=True)
    assert folder_for(config, "clip.mp4") == Path("Videos/2026-10")
    config = make_config(tmp_path, month_subfolders=True, rules=[
        {"name": "Docs", "extensions": ["pdf"], "folder": "Docs", "by_month": False},
    ])
    assert folder_for(config, "a.pdf") == Path("Docs")


def test_no_fallback_leaves_unmatched_files_alone(tmp_path: Path) -> None:
    config = make_config(tmp_path, fallback_folder=None)
    assert target_for(tmp_path / "mystery.xyz", config) is None
    assert target_for(tmp_path / "a.pdf", config).rule_name == "Documents"


def test_fallback_rule_name(config: Config) -> None:
    assert target_for(config.watch_folder / "x.unknown", config).rule_name == FALLBACK_RULE_NAME


def test_custom_destination(tmp_path: Path) -> None:
    config = make_config(tmp_path, destination=str(tmp_path / "Sorted"))
    target = target_for(tmp_path / "a.pdf", config)
    assert target.folder == tmp_path / "Sorted" / "Documents"


@pytest.mark.parametrize(
    "bad_rule",
    [
        {"name": "No folder", "extensions": ["pdf"]},
        {"name": "No condition", "folder": "X"},
        {"name": "Bad regex", "pattern": "(unclosed", "folder": "X"},
        {"name": "Escape", "extensions": ["pdf"], "folder": "../outside"},
    ],
)
def test_bad_rules_are_rejected(tmp_path: Path, bad_rule: dict) -> None:
    with pytest.raises(ConfigError):
        make_config(tmp_path, rules=[bad_rule])
