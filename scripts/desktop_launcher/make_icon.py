"""Generate the desktop launcher's icon.

`csc /win32icon:` needs a real `.ico`, and the repository only ships a PNG
(`assets/web-ui-welcome.png`). Rather than commit a binary nobody can regenerate,
the icon is drawn from the project's own palette and written on demand.

Run:  python scripts/desktop_launcher/make_icon.py [output.ico]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# The Web UI's theme colours (.streamlit/config.toml), so the taskbar icon matches
# the app rather than being a generic red square.
MARVEL_RED = (200, 16, 46)
BACKGROUND = (10, 10, 10)
FOREGROUND = (245, 241, 235)

#: Windows picks the closest size; shipping the common ones avoids a blurry icon.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("arialbd.ttf", "seguisb.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), BACKGROUND + (255,))
    draw = ImageDraw.Draw(image)

    # Rounded red tile with a subtle border.
    inset = max(1, size // 16)
    radius = max(2, size // 5)
    draw.rounded_rectangle(
        [inset, inset, size - inset, size - inset],
        radius=radius,
        fill=MARVEL_RED,
        outline=(255, 255, 255, 40),
        width=max(1, size // 32),
    )

    text = "M"
    font = _font(int(size * 0.62))
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]),
        text,
        font=font,
        fill=FOREGROUND,
    )
    return image


def main() -> int:
    default = Path(__file__).resolve().parent.parent.parent / "assets" / "marvel.ico"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    target.parent.mkdir(parents=True, exist_ok=True)

    # Draw once at 256px and downsample, so the letterform stays crisp at 16px
    # instead of being rendered at a size its hinting was never meant for.
    master = _draw(256)
    images = [master.resize(size, Image.LANCZOS) for size in SIZES]

    images[-1].save(target, format="ICO", sizes=SIZES)
    print(f"wrote {target} ({target.stat().st_size} bytes, {len(SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
