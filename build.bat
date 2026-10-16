@echo off
rem Builds dist\DownloadsSorter.exe (tray app, no console) and
rem dist\DownloadsSorterCLI.exe (same app, for the command line).
setlocal
cd /d "%~dp0"

python -m pip install -r requirements-dev.txt || goto :error
python -m pytest -q || goto :error
python -m downloads_sorter.icon build\icon.ico || goto :error

rem The entry is __main__.py, not DownloadsSorter.pyw: PyInstaller always makes
rem .pyw scripts windowed, which would break the console build.
set COMMON=--noconfirm --onefile --icon build\icon.ico --paths . ^
  --add-data "downloads_sorter\default_config.yaml;downloads_sorter" ^
  --hidden-import pystray._win32 ^
  --hidden-import watchdog.observers.read_directory_changes

python -m PyInstaller %COMMON% --windowed --name DownloadsSorter downloads_sorter\__main__.py || goto :error
python -m PyInstaller %COMMON% --console --name DownloadsSorterCLI downloads_sorter\__main__.py || goto :error

echo.
echo Done: dist\DownloadsSorter.exe and dist\DownloadsSorterCLI.exe
exit /b 0

:error
echo.
echo Build failed.
exit /b 1
