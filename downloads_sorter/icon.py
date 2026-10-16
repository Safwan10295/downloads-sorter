"""Draws the tray icon (a folder with a down arrow) so no image files are needed.

Run `python -m downloads_sorter.icon out.ico` to export an .ico for PyInstaller.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ACTIVE = (37, 99, 235, 255)
PAUSED = (128, 128, 128, 255)
WHITE = (255, 255, 255, 255)


def make_icon(size: int = 64, *, paused: bool = False) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = size / 64
    color = PAUSED if paused else ACTIVE

    draw.rounded_rectangle([4 * s, 10 * s, 30 * s, 24 * s], radius=4 * s, fill=color)  # tab
    draw.rounded_rectangle([4 * s, 17 * s, 60 * s, 56 * s], radius=6 * s, fill=color)  # body

    if paused:
        draw.rectangle([23 * s, 26 * s, 29 * s, 48 * s], fill=WHITE)
        draw.rectangle([35 * s, 26 * s, 41 * s, 48 * s], fill=WHITE)
    else:
        draw.rectangle([28 * s, 24 * s, 36 * s, 38 * s], fill=WHITE)
        draw.polygon([(19 * s, 36 * s), (45 * s, 36 * s), (32 * s, 50 * s)], fill=WHITE)
    return image


def save_png(path: Path, size: int = 128) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    make_icon(size).save(path)
    return path


def save_ico(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    make_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "icon.ico")
    print(save_ico(out))
