"""Double-click launcher: starts the tray app with no console window.

The Windows startup entry uses this too when running from source.
"""

from downloads_sorter.cli import main

raise SystemExit(main())
