"""Finds pauses (silence between speech) from per-window loudness values.

The threshold adapts to each recording: it sits between the background-noise level and
the speech level, so a quiet hum or room noise is not mistaken for talking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

WIN = 0.02            # seconds per value (must match builder.LEVEL_WINDOW)
FLOOR_DB = -100.0
MIN_SPEECH = 0.08     # louder bits shorter than this (clicks, taps) do not count as speech

SENSITIVITY = {"less": 0.2, "normal": 0.3, "more": 0.42}


@dataclass
class PauseResult:
    ranges: List[Tuple[float, float]] = field(default_factory=list)
    removed: float = 0.0
    noise_db: float = 0.0
    speech_db: float = 0.0
    threshold_db: float = 0.0
    message: str = ""


def parse_levels(lines, total: float) -> List[float]:
    """Reads ffmpeg `ametadata=print` output into one dB value per 20 ms window."""
    n = int(total / WIN + 0.5) + 1
    levels = [FLOOR_DB] * n
    t = None
    for line in lines:
        if line.startswith("frame:"):
            i = line.find("pts_time:")
            if i >= 0:
                try:
                    t = float(line[i + 9:].split()[0])
                except ValueError:
                    t = None
        elif line.startswith("lavfi.astats.Overall.RMS_level=") and t is not None:
            k = int(t / WIN + 0.5)
            if 0 <= k < n:
                v = line.split("=", 1)[1].strip()
                try:
                    db = float(v)
                except ValueError:
                    db = FLOOR_DB           # "-inf" for digital silence
                levels[k] = max(FLOOR_DB, db)
    return levels


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return FLOOR_DB
    i = min(len(sorted_vals) - 1, max(0, int(q * (len(sorted_vals) - 1))))
    return sorted_vals[i]


def find_pauses(levels: List[float], total: float, min_pause: float = 0.5,
                keep: float = 0.2, sensitivity: str = "normal") -> PauseResult:
    """Returns the ranges to remove.

    min_pause   only quiet parts at least this long are removed
    keep        safe zone: seconds of quiet kept before and after speech, so word starts
                and endings are never clipped (between two words 2 x keep remains)
    sensitivity "less" / "normal" / "more" - how quiet a part must be to count as a pause
    """
    res = PauseResult()
    if not levels:
        res.message = "There is no sound to analyse."
        return res
    # parts with no sound at all (gaps between clips, digital silence) would pull the noise
    # estimate down to nothing, so the noise/speech levels are measured on real sound only
    sv = sorted(v for v in levels if v > FLOOR_DB + 1)
    if len(sv) < 10:
        res.message = "The sound is completely silent."
        return res
    noise = _percentile(sv, 0.05)
    speech = _percentile(sv, 0.95)
    res.noise_db, res.speech_db = noise, speech
    gap = speech - noise
    if speech <= FLOOR_DB + 1:
        res.message = "The sound is completely silent."
        return res
    if gap < 12:
        res.message = ("Speech and background are almost equally loud, so no clear pauses "
                       "were found. Try “Remove more”.") if sensitivity != "more" else \
                      "No clear pauses were found in this recording."
        if sensitivity != "more" or gap < 6:
            return res
    thr = noise + SENSITIVITY.get(sensitivity, 0.3) * gap
    thr = min(thr, speech - 10)
    res.threshold_db = thr

    voiced = [v > thr for v in levels]
    # ignore very short loud bits (clicks, taps, a cough start) inside silence
    min_run = max(1, int(round(MIN_SPEECH / WIN)))
    i, n = 0, len(voiced)
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            if j - i < min_run:
                for k in range(i, j):
                    voiced[k] = False
            i = j
        else:
            i += 1

    end = min(total, n * WIN)
    i = 0
    while i < n:
        if voiced[i]:
            i += 1
            continue
        j = i
        while j < n and not voiced[j]:
            j += 1
        s, e = i * WIN, min(end, j * WIN)
        if e - s >= min_pause - 1e-9:
            a = s if i == 0 else s + keep           # nothing to protect before the start
            b = e if j >= n else e - keep           # ... or after the end
            if b - a >= 0.1:
                res.ranges.append((round(a, 3), round(b, 3)))
        i = j
    res.removed = sum(b - a for a, b in res.ranges)
    if not res.ranges:
        res.message = "No pauses longer than the chosen length were found."
    return res
