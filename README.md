# Downloads Sorter

A small Windows tray app that keeps your Downloads folder organised. When it
starts, it sorts everything already there into sub-folders (Images,
Documents/Finance, Uni, ...), then moves each new download as soon as it
finishes.

- **Only moves, never deletes.** Name clashes become `file (1).pdf`.
- **Every move is logged** to a JSON log, and the tray menu can undo the last move, the last 10, or a whole bulk clean-up.
- **Waits for downloads to finish**: ignores `.crdownload`/`.part`/`.tmp`/`.download`
  files, waits until the size has been stable for a few seconds, and checks no other
  program has the file open.
- **Rules live in `config.yaml`**, checked top to bottom, first match wins.
- **Dry-run mode** logs what it would do without moving anything.

## Setup

Needs Python 3.10+ on Windows.

```bat
git clone https://github.com/Safwan10295/downloads-sorter.git
cd downloads-sorter
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Start the tray app (no console window):

```bat
pythonw DownloadsSorter.pyw
```

or `python -m downloads_sorter` to see log output in the console. Look for the
blue folder icon in the system tray (you may need to click the `^` arrow).
Left-click opens Downloads; right-click shows the menu.

When it starts, it sorts everything already in Downloads, then keeps sorting each
new download as it finishes. There's nothing to click. If you don't want the
start-up clean-up, set `sort_existing_on_start: false` in the config.

On first run a config file is created at `%APPDATA%\DownloadsSorter\config.yaml`.

> **Tip:** set `dry_run: true` in the config for your first day. Nothing moves,
> and the move log shows exactly what would have happened.

## Tray menu

| Item | What it does |
|---|---|
| Pause sorting | Stop sorting until you resume. On resume, anything that arrived while paused is sorted. |
| Sort everything now | Sorts every file currently in Downloads (the same thing that happens at start-up). In dry-run mode it opens the list of what would move in Notepad instead. |
| Undo last move / last 10 moves | Moves files back to where they were. If a file with that name has appeared since, the restored one gets a `(1)` suffix. Restored files are left alone by the watcher. |
| Undo last bulk sort | Puts back every file from the last start-up / "Sort everything now" clean-up, however many there were. |
| Open Downloads / config / move log / app log | Opens them in Explorer or Notepad. |
| Reload config | Re-reads `config.yaml` after you edit it. If it has a mistake you get a message and the old settings stay in use. |
| Start with Windows | Toggles the startup entry. |
| Quit | Stops the app. |

## Command line

Everything is also available from a terminal (`python -m downloads_sorter <command>`,
or `DownloadsSorterCLI.exe <command>` from the build):

```bat
python -m downloads_sorter sort-existing            :: preview, then asks before moving
python -m downloads_sorter sort-existing --dry-run  :: preview only
python -m downloads_sorter sort-existing --yes      :: preview, then move without asking
python -m downloads_sorter undo                     :: undo the last move
python -m downloads_sorter undo -n 50               :: undo the last 50
python -m downloads_sorter undo --bulk              :: undo the whole last bulk sort
python -m downloads_sorter startup on|off|status    :: run on Windows startup
python -m downloads_sorter explain "Invoice 12.pdf" "week3.pptx"   :: which rule matches?
python -m downloads_sorter watch                    :: sort in the console, no tray icon
python -m downloads_sorter paths                    :: where config and logs live
```

Add `--config path\to\config.yaml` before the command to use a different config
(the move log and app log then live next to that file).

## Editing the rules

Open the config from the tray menu, edit, save, then choose **Reload config**.
The file is commented; the important part is the `rules:` list:

```yaml
rules:
  - name: Finance                 # label for the logs
    pattern: 'invoice|receipt'    # regex, searched anywhere in the filename, ignores case
    folder: Documents/Finance     # relative to `destination` (default: the Downloads folder)

  - name: Uni
    pattern: 'lecture|week'
    folder: Uni

  - name: Images
    extensions: [jpg, jpeg, png, gif, webp, heic]
    folder: Images
    by_month: true                # -> Images/2026-10
```

- **Order matters.** Rules are checked top to bottom and the first match wins, so put
  specific pattern rules above the broad extension rules. With the defaults,
  `receipt.png` goes to `Documents/Finance`, not `Images`.
- A rule can have `extensions`, `pattern`, or **both** (then both must match, e.g.
  "PDFs with *bank* in the name").
- Files that match no rule go to `fallback_folder` (default `Other`). Set it to
  empty to leave them in Downloads.
- `month_subfolders: true` turns on month folders for every rule; a rule's own
  `by_month` overrides it. The month comes from the file's modified date.
- Patterns are [Python regular expressions](https://docs.python.org/3/library/re.html):
  `|` means "or", `\d` is a digit, `^` is the start of the name. Put them in
  **single quotes** so backslashes work. Add `case_sensitive: true` to a rule if needed.
- Check your rules without moving anything:
  `python -m downloads_sorter explain "Week 5 lecture.pdf"`.

Other settings (all in the config): `watch_folder` (`auto` = your Windows Downloads
folder), `destination`, `dry_run`, `notifications`, `sort_existing_on_start`, `settle_seconds`,
`min_age_seconds`, `in_progress_extensions`, `skip_extensions`, `ignore_names`,
`skip_hidden`.

## What is never touched

- Folders, symlinks, shortcuts (`.lnk`, `.url`), hidden and system files, `desktop.ini`.
- Files still downloading, or with a `.part`/`.crdownload`/... file next to them.
- Files modified in the last few seconds or open in another program.
- Anything in sub-folders of Downloads: only the top level is watched.

## The move log

`%APPDATA%\DownloadsSorter\moves.jsonl` holds one JSON object per line:

```json
{"action": "move", "id": "3f2a9c1b7d4e", "timestamp": "2026-10-16T14:02:11+01:00", "original_path": "C:\\Users\\you\\Downloads\\invoice.pdf", "new_path": "C:\\Users\\you\\Downloads\\Documents\\Finance\\invoice.pdf", "rule": "Finance", "dry_run": false}
{"action": "undo", "id": "3f2a9c1b7d4e", "timestamp": "2026-10-16T14:05:40+01:00", "restored_path": "C:\\Users\\you\\Downloads\\invoice.pdf", "status": "restored"}
```

It is only ever appended to. Undo uses it to find the most recent moves that
haven't been undone yet (dry-run entries are skipped). Moves made by a bulk sort
also carry a `"batch"` id, which is how "Undo last bulk sort" finds them. The app's own log is
`sorter.log` in the same folder.

## Run on startup

Tick **Start with Windows** in the tray menu, or run
`python -m downloads_sorter startup on`. This adds a value under
`HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run` (no admin rights
needed) that launches the tray app with `pythonw`, or the `.exe` if you're using
the build. `startup off` removes it.

If you move the project folder or the exe, run `startup on` again.

## Building an .exe

```bat
build.bat
```

This installs the dev requirements, runs the tests, and uses PyInstaller to make:

- `dist\DownloadsSorter.exe` – the tray app (no console window). Put it anywhere and double-click.
- `dist\DownloadsSorterCLI.exe` – the same program for the command-line commands above.

Keep both in the same folder; `startup on` from the CLI exe registers the tray exe.

## Tests

```bat
pip install -r requirements-dev.txt
python -m pytest
```

`tests/test_rules.py` covers rule matching (order, patterns vs extensions, month
folders, fallback, bad rules). `tests/test_mover.py` covers the safety behaviour
(no overwrites, dry run, skipped files, undo).

## Project layout

```
DownloadsSorter.pyw         double-click launcher (tray app, no console)
downloads_sorter/
  __main__.py               `python -m downloads_sorter`; PyInstaller entry point
  cli.py                    command line + logging setup
  config.py                 loads and validates config.yaml
  default_config.yaml       template copied to %APPDATA% on first run
  rules.py                  rule matching (pure functions)
  filechecks.py             "is this file safe to touch yet?" checks
  mover.py                  safe moves, collision names, sort-existing, undo
  movelog.py                append-only JSON Lines move log
  watcher.py                watchdog observer + wait-until-finished queue
  tray.py                   pystray icon and menu
  notify.py                 batched Windows toast notifications
  startup.py                run-on-startup registry entry
  winutil.py                Win32 helpers (Downloads folder, file locks, message boxes)
  icon.py                   draws the tray icon
tests/
build.bat
```

## Troubleshooting

- **Nothing happens:** check the app log (tray → Open app log). Make sure sorting isn't
  paused and `dry_run` is `false`.
- **A file isn't moved:** it might still be open (e.g. a PDF viewer the browser opened
  automatically). It moves once you close it.
- **No notifications:** check `notifications: true`, and that Windows notifications and
  Focus Assist / Do Not Disturb allow them.
- **Config error on start:** you'll get a message box explaining which line is wrong.
  A common one is backslashes in double quotes; use `'C:\Path'` or `C:/Path`.

## License

MIT, see [LICENSE](LICENSE). Use it, change it, share it.
