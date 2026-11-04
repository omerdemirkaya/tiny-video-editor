"""Presets and choice lists shown in the UI (labels are user-facing, in English)."""
from __future__ import annotations

ASPECT_CHOICES = [
    ("16:9  ·  Landscape (YouTube)", (16, 9)),
    ("9:16  ·  Vertical (Reels / TikTok / Shorts)", (9, 16)),
    ("1:1  ·  Square", (1, 1)),
    ("4:5  ·  Instagram post", (4, 5)),
    ("4:3  ·  Classic", (4, 3)),
]

ROTATE_CHOICES = [
    ("Rotate 90° right", "cw"),
    ("Rotate 90° left", "ccw"),
    ("Rotate 180°", "180"),
    ("Mirror (left ↔ right)", "hflip"),
    ("Flip (top ↔ bottom)", "vflip"),
]

LAYOUT_CHOICES = [
    ("Fill the frame (crop edges)", "fill"),
    ("Fit inside (black bars)", "fit"),
    ("Small · top right", "pip_tr"),
    ("Small · top left", "pip_tl"),
    ("Small · bottom right", "pip_br"),
    ("Small · bottom left", "pip_bl"),
]

SPEED_CHOICES = [("0.25x  ·  very slow", 0.25), ("0.5x  ·  slow motion", 0.5), ("0.75x", 0.75),
                 ("1.25x", 1.25), ("1.5x", 1.5), ("2x  ·  double speed", 2.0), ("3x", 3.0),
                 ("4x", 4.0)]

VOLUME_CHOICES = [("25%", 0.25), ("50%", 0.5), ("75%", 0.75), ("125%", 1.25),
                  ("150%", 1.5), ("200%", 2.0), ("300%", 3.0)]

FPS_CHOICES = [("60 fps", 60), ("30 fps", 30), ("25 fps", 25), ("24 fps", 24),
               ("20 fps", 20), ("15 fps", 15), ("10 fps", 10)]

# (key, label, has strength setting)
FILTER_PRESETS = [
    ("bw", "Black & white", False),
    ("sepia", "Sepia (old photo)", False),
    ("vintage", "Vintage colors", False),
    ("brighter", "Brighter", True),
    ("darker", "Darker", True),
    ("contrast", "More contrast", True),
    ("vivid", "Vivid colors", True),
    ("faded", "Faded colors", True),
    ("blur", "Blur", True),
    ("sharpen", "Sharpen", True),
    ("denoise", "Reduce noise (grain)", True),
    ("vignette", "Dark corners (vignette)", True),
    ("negative", "Negative", False),
    ("mirror", "Mirror image", False),
]
FILTER_LABELS = {k: label for k, label, _ in FILTER_PRESETS}
FILTER_HAS_STRENGTH = {k: s for k, _, s in FILTER_PRESETS}

TEXT_POSITIONS = {
    "Bottom center": ("(w-text_w)/2", "h-text_h-h/14"),
    "Top center": ("(w-text_w)/2", "h/14"),
    "Center": ("(w-text_w)/2", "(h-text_h)/2"),
    "Top left": ("w/40", "h/30"),
    "Top right": ("w-text_w-w/40", "h/30"),
    "Bottom left": ("w/40", "h-text_h-h/30"),
    "Bottom right": ("w-text_w-w/40", "h-text_h-h/30"),
}
TEXT_SIZES = {"Small": "h/28", "Medium": "h/18", "Large": "h/11"}
TEXT_COLORS = {"White": "white", "Yellow": "yellow", "Black": "black",
               "Red": "red", "Blue": "0x3399FF", "Green": "0x33CC33"}
