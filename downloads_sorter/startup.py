"""Run-on-startup via the per-user registry Run key (no admin rights needed)."""

from __future__ import annotations

import sys
from pathlib import Path

from .config import default_config_path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DownloadsSorter"
GUI_EXE_NAME = "DownloadsSorter.exe"
LAUNCHER = Path(__file__).resolve().parent.parent / "DownloadsSorter.pyw"


def launch_command(config_path: Path | None = None) -> str:
    """The command Windows runs at sign-in: the tray app, with no console window."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        gui = exe.with_name(GUI_EXE_NAME)  # prefer the windowed build over the CLI one
        command = f'"{gui if gui.exists() else exe}"'
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        interpreter = pythonw if pythonw.exists() else Path(sys.executable)
        command = f'"{interpreter}" "{LAUNCHER}"'
    if config_path is not None and config_path.resolve() != default_config_path().resolve():
        command += f' --config "{config_path.resolve()}"'
    return command


def registered_command() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None


def is_enabled() -> bool:
    return registered_command() is not None


def enable(config_path: Path | None = None) -> str:
    import winreg

    command = launch_command(config_path)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
    return command


def disable() -> bool:
    """Remove the startup entry. Returns False if there wasn't one."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
