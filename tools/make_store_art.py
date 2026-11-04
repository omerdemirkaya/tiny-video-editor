"""Store listing art: 9:16 poster (1440x2160) and 1:1 box art (2160x2160) -> store/art/

Usage:  pip install pillow  &&  python tools/make_store_art.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icons import draw_master  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "store" / "art"
TOP, BOTTOM = (28, 33, 44), (14, 16, 21)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def background(w, h):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / (h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    # a subtle "timeline" of clips near the bottom
    rows = [(0.80, (61, 90, 145)), (0.85, (47, 107, 94))]
    for fy, col in rows:
        y = int(h * fy)
        x = int(w * 0.08)
        for frac in (0.22, 0.34, 0.20):
            x2 = x + int(w * frac)
            d.rounded_rectangle([x, y, x2, y + int(h * 0.035)], radius=int(h * 0.008), fill=col)
            x = x2 + int(w * 0.012)
    px = int(w * 0.45)
    d.line([(px, int(h * 0.77)), (px, int(h * 0.90))], fill=(255, 255, 255), width=max(3, w // 300))
    return img


def make(w, h, logo_frac, name):
    img = background(w, h).convert("RGBA")
    size = int(min(w, h) * logo_frac)
    logo = draw_master(1024).resize((size, size), Image.LANCZOS)
    cy = int(h * (0.36 if h > w else 0.33))
    img.alpha_composite(logo, ((w - size) // 2, cy - size // 2))
    d = ImageDraw.Draw(img)
    fsize = int(w * (0.105 if h > w else 0.085))
    font = ImageFont.truetype(FONT, fsize)
    text = "Tiny Video Editor"
    tw = d.textlength(text, font=font)
    if tw > w * 0.9:
        font = ImageFont.truetype(FONT, int(fsize * w * 0.9 / tw))
        tw = d.textlength(text, font=font)
    d.text(((w - tw) / 2, cy + size // 2 + int(h * 0.04)), text, font=font, fill=(232, 234, 240))
    OUT.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT / name, optimize=True)
    print("wrote", OUT / name)


if __name__ == "__main__":
    make(1440, 2160, 0.42, "poster-1440x2160.png")
    make(2160, 2160, 0.36, "boxart-2160x2160.png")
