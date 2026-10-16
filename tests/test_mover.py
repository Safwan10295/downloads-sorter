"""Safety checks for moving and undoing, using real files in a temp folder."""

import os
import time
from pathlib import Path

import pytest

from downloads_sorter.config import Config
from downloads_sorter.mover import Mover, split_name, unique_path
from downloads_sorter.movelog import MoveLog
from downloads_sorter.rules import Rule


def make_file(path: Path, content: str = "data", age: float = 60) -> Path:
    path.write_text(content)
    old = time.time() - age
    os.utime(path, (old, old))  # look like it finished downloading a while ago
    return path


@pytest.fixture
def mover(tmp_path: Path) -> Mover:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    config = Config(
        path=tmp_path / "config.yaml",
        watch_folder=downloads,
        destination=downloads,
        rules=[Rule(name="Documents", folder="Documents", extensions=(".pdf",))],
        fallback_folder="Other",
    )
    return Mover(config, MoveLog(config.move_log_path))


def test_split_and_unique_names(tmp_path: Path) -> None:
    assert split_name("backup.tar.gz") == ("backup", ".tar.gz")
    assert split_name("report.pdf") == ("report", ".pdf")
    make_file(tmp_path / "report.pdf")
    make_file(tmp_path / "report (1).pdf")
    assert unique_path(tmp_path / "report.pdf") == tmp_path / "report (2).pdf"
    assert unique_path(tmp_path / "new.pdf") == tmp_path / "new.pdf"


def test_never_overwrites_existing_file(mover: Mover) -> None:
    downloads = mover.config.watch_folder
    (downloads / "Documents").mkdir()
    make_file(downloads / "Documents" / "a.pdf", "old")
    make_file(downloads / "a.pdf", "new")

    dst = mover.sort_file(downloads / "a.pdf")

    assert dst == downloads / "Documents" / "a (1).pdf"
    assert (downloads / "Documents" / "a.pdf").read_text() == "old"
    assert dst.read_text() == "new"


def test_dry_run_only_logs(mover: Mover) -> None:
    mover.config.dry_run = True
    src = make_file(mover.config.watch_folder / "a.pdf")

    mover.sort_file(src)

    assert src.exists()
    [entry] = mover.log.entries()
    assert entry["dry_run"] is True
    assert mover.log.undoable(10) == []


def test_skips_shortcuts_partials_and_folders(mover: Mover) -> None:
    downloads = mover.config.watch_folder
    make_file(downloads / "app.lnk")
    make_file(downloads / "big.zip.crdownload")
    make_file(downloads / "firefox.pdf", "")  # placeholder...
    make_file(downloads / "firefox.pdf.part")  # ...while the real download is in here
    (downloads / "Some folder").mkdir()
    make_file(downloads / "fresh.pdf", age=0)  # still being written

    assert mover.plan_existing() == []


def test_sort_existing_then_undo_restores_everything(mover: Mover) -> None:
    downloads = mover.config.watch_folder
    names = ["a.pdf", "b.pdf", "c.weird"]
    for name in names:
        make_file(downloads / name, name)

    moves = mover.plan_existing()
    assert len(mover.apply(moves, dry_run=False)) == 3
    assert not any((downloads / name).exists() for name in names)
    assert (downloads / "Other" / "c.weird").exists()

    [last] = mover.undo(1)
    assert last.ok and last.restored == downloads / "c.weird"
    assert len(mover.undo(10)) == 2
    assert all((downloads / name).read_text() == name for name in names)
    assert mover.undo(10) == []  # nothing left to undo
    assert mover.log.recently_restored(downloads / "a.pdf", within_seconds=60)


def test_undo_last_bulk_sort_leaves_single_moves_alone(mover: Mover) -> None:
    downloads = mover.config.watch_folder
    mover.sort_file(make_file(downloads / "earlier.pdf"))  # a normal one-file move
    for i in range(15):
        make_file(downloads / f"file{i}.pdf")
    assert len(mover.apply(mover.plan_existing(), dry_run=False)) == 15

    results = mover.undo_last_batch()

    assert len(results) == 15 and all(r.ok for r in results)
    assert all((downloads / f"file{i}.pdf").exists() for i in range(15))
    assert (downloads / "Documents" / "earlier.pdf").exists()
    assert mover.undo_last_batch() == []


def test_undo_does_not_overwrite_a_new_file_with_the_same_name(mover: Mover) -> None:
    downloads = mover.config.watch_folder
    mover.sort_file(make_file(downloads / "a.pdf", "first"))
    make_file(downloads / "a.pdf", "second download")

    [result] = mover.undo(1)

    assert result.restored == downloads / "a (1).pdf"
    assert (downloads / "a.pdf").read_text() == "second download"
    assert result.restored.read_text() == "first"


def test_undo_skips_files_that_were_moved_by_hand(mover: Mover) -> None:
    downloads = mover.config.watch_folder
    dst = mover.sort_file(make_file(downloads / "a.pdf"))
    dst.rename(downloads / "elsewhere.pdf")

    [result] = mover.undo(1)

    assert not result.ok
    assert mover.undo(1) == []  # marked as handled, not retried forever
