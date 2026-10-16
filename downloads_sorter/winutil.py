"""Small Win32 helpers using ctypes. Each one degrades gracefully off Windows."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_ALREADY_EXISTS = 183
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"

MB_YESNO = 0x04
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_ICONQUESTION = 0x20
MB_SETFOREGROUND = 0x10000
MB_TOPMOST = 0x40000
IDYES = 6


def downloads_folder() -> Path:
    """The user's real Downloads folder, even if it has been moved (e.g. to D:)."""
    fallback = Path.home() / "Downloads"
    if not IS_WINDOWS:
        return fallback
    guid = _GUID.from_buffer_copy(uuid.UUID(FOLDERID_DOWNLOADS).bytes_le)
    result = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid), 0, None, ctypes.byref(result)
    )
    try:
        if hr == 0 and result.value:
            return Path(result.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(result)
    log.warning("Could not look up the Downloads folder; using %s", fallback)
    return fallback


def is_locked(path: Path) -> bool:
    """True if another program has the file open (so it may still be writing)."""
    if not IS_WINDOWS:
        try:
            with open(path, "rb"):
                return False
        except OSError:
            return True
    # Ask for exclusive access (share mode 0). This fails while any other
    # handle to the file is open, which is exactly what we want to detect.
    handle = _kernel32.CreateFileW(
        str(path), GENERIC_READ, 0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None
    )
    if handle == INVALID_HANDLE_VALUE or handle is None:
        return True
    _kernel32.CloseHandle(handle)
    return False


def message_box(title: str, text: str, *, ask: bool = False, warning: bool = False) -> bool:
    """Show a native message box. With ask=True, returns True if the user chose Yes."""
    if not IS_WINDOWS:
        log.info("%s: %s", title, text)
        return False
    icon = MB_ICONQUESTION if ask else (MB_ICONWARNING if warning else MB_ICONINFORMATION)
    flags = icon | MB_TOPMOST | MB_SETFOREGROUND | (MB_YESNO if ask else 0)
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags) == IDYES


def acquire_single_instance(name: str) -> object | None:
    """Create a named mutex. Returns None if another instance already holds it.

    Keep the returned handle alive for as long as the app runs.
    """
    if not IS_WINDOWS:
        return object()
    ctypes.set_last_error(0)
    handle = _kernel32.CreateMutexW(None, False, f"Local\\{name}")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        _kernel32.CloseHandle(handle)
        return None
    return handle


def open_path(path: Path) -> None:
    """Open a file or folder with its default app (Explorer, Notepad, ...)."""
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])
