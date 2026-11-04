"""Draws the Tiny Video Editor icon and writes assets/icon-*.png and assets/app.ico.

Usage:  pip install pillow  &&  python tools/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
ACCENT_TOP = (110, 154, 255)
ACCENT_BOTTOM = (64, 108, 238)


def draw_master(size=1024):
    """Rounded blue square, a white play triangle and two white 'cut' bars."""
    s = size * 4                                   # draw large, then downsample (anti-alias)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / (s - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(ACCENT_TOP, ACCENT_BOTTOM))
        gd.line([(0, y), (s, y)], fill=c + (255,))
    mask = Image.new("L", (s, s), 0)
    m = s * 0.06
    ImageDraw.Draw(mask).rounded_rectangle([m, m, s - m, s - m], radius=s * 0.22, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    cx = cy = s / 2
    r = (s - 2 * m) / 2
    tri = r * 0.42
    d.polygon([(cx - tri * 0.62, cy - tri), (cx - tri * 0.62, cy + tri), (cx + tri, cy)],
              fill=(255, 255, 255, 255))
    bw, bh = r * 0.09, r * 0.62
    for x in (cx - r * 0.62, cx + r * 0.62):
        d.rounded_rectangle([x - bw / 2, cy - bh, x + bw / 2, cy + bh], radius=bw / 2,
                            fill=(255, 255, 255, 235))
    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    master = draw_master()
    for size in (32, 64, 256, 512, 1024):
        master.resize((size, size), Image.LANCZOS).save(OUT / f"icon-{size}.png")
    master.save(OUT / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                        (128, 128), (256, 256)])
    print("Icons written to", OUT)


if __name__ == "__main__":
    main()
