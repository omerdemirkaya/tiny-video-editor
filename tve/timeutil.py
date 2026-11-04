"""Time text <-> seconds conversions."""
from __future__ import annotations


def parse_time(text) -> float:
    """Converts values like '90', '1:30', '01:01:30.5' or '1:30,5' to seconds."""
    s = str(text).strip().replace(",", ".")
    if not s:
        raise ValueError("Empty time")
    parts = s.split(":")
    if len(parts) > 3:
        raise ValueError("Invalid time")
    total = 0.0
    for part in parts:
        part = part.strip() or "0"
        total = total * 60 + float(part)
    if total < 0:
        raise ValueError("Time can't be negative")
    return total


def fmt_time(sec, decimals: int = 1) -> str:
    if sec is None:
        return "--:--"
    sec = round(max(0.0, float(sec)), decimals)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    if decimals:
        s_txt = f"{s:0{3 + decimals}.{decimals}f}"
    else:
        s_txt = f"{int(s):02d}"
    if h:
        return f"{h}:{m:02d}:{s_txt}"
    return f"{m:02d}:{s_txt}"
