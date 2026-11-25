"""Timeline data model: media, tracks and clips, plus editing operations.

Times:
  clip.start  -> position on the timeline (seconds)
  clip.src_in / clip.src_out -> the used part of the source file (seconds)
  clip.speed  -> playback speed; timeline duration = (src_out - src_in) / speed
"""
from __future__ import annotations

import bisect
import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional

MIN_CLIP = 0.1
EPS = 1e-6


@dataclass
class Clip:
    id: int
    kind: str                 # "video" | "audio"
    path: str
    start: float
    src_in: float
    src_out: float
    speed: float = 1.0
    link: Optional[int] = None
    # video properties
    effect: Optional[str] = None
    strength: int = 5
    text: Optional[dict] = None
    rotate: Optional[str] = None
    layout: str = "fill"
    # audio properties
    volume: float = 1.0
    muted: bool = False
    # advanced
    custom: str = ""

    @property
    def duration(self) -> float:
        return (self.src_out - self.src_in) / self.speed

    @property
    def end(self) -> float:
        return self.start + self.duration

    def src_at(self, t: float) -> float:
        return self.src_in + (t - self.start) * self.speed

    def has_edits(self) -> bool:
        return bool(abs(self.speed - 1) > EPS or self.effect or self.text or self.rotate
                    or self.layout != "fill" or abs(self.volume - 1) > EPS or self.muted
                    or self.custom)


@dataclass
class Track:
    id: int
    kind: str                 # "video" | "audio"
    clips: List[Clip] = field(default_factory=list)
    hidden: bool = False      # video: hidden, audio: muted

    def sorted(self) -> List[Clip]:
        return sorted(self.clips, key=lambda c: c.start)


class Project:
    MAX_TRACKS = 3

    def __init__(self):
        self.media: Dict[str, object] = {}   # path -> VideoInfo
        self.tracks: List[Track] = []
        self.main_path: Optional[str] = None
        self._next = 1

    # ------------------------------------------------------------ basics
    def new_id(self) -> int:
        self._next += 1
        return self._next

    def video_tracks(self) -> List[Track]:
        return [t for t in self.tracks if t.kind == "video"]

    def audio_tracks(self) -> List[Track]:
        return [t for t in self.tracks if t.kind == "audio"]

    def track_name(self, track: Track) -> str:
        lst = self.video_tracks() if track.kind == "video" else self.audio_tracks()
        return ("V" if track.kind == "video" else "A") + str(lst.index(track) + 1)

    def add_track(self, kind: str) -> Optional[Track]:
        if len(self.video_tracks() if kind == "video" else self.audio_tracks()) >= self.MAX_TRACKS:
            return None
        t = Track(self.new_id(), kind)
        self.tracks.append(t)
        return t

    def remove_track(self, track: Track) -> None:
        for c in list(track.clips):
            partner = self.linked(c)
            if partner:
                partner.link = None
        self.tracks.remove(track)

    def prune_empty_tracks(self) -> bool:
        """Remove empty tracks, keeping at least one video and one audio track."""
        removed = False
        for kind in ("video", "audio"):
            lst = [t for t in self.tracks if t.kind == kind]
            for t in list(lst):
                if not t.clips and len(lst) > 1:
                    self.tracks.remove(t)
                    lst.remove(t)
                    removed = True
        return removed

    def swap_tracks(self, a: Track, b: Track) -> None:
        """Swap two tracks of the same kind (changes layer order / track numbers)."""
        if a is b or a.kind != b.kind:
            return
        ia, ib = self.tracks.index(a), self.tracks.index(b)
        self.tracks[ia], self.tracks[ib] = b, a

    def all_clips(self) -> List[Clip]:
        return [c for t in self.tracks for c in t.clips]

    def clip(self, cid: int) -> Optional[Clip]:
        return next((c for c in self.all_clips() if c.id == cid), None)

    def track_of(self, clip: Clip) -> Optional[Track]:
        return next((t for t in self.tracks if clip in t.clips), None)

    def linked(self, clip: Clip) -> Optional[Clip]:
        return self.clip(clip.link) if clip.link else None

    @property
    def end(self) -> float:
        return max((c.end for c in self.all_clips()), default=0.0)

    def media_duration(self, path: str) -> float:
        info = self.media.get(path)
        return float(getattr(info, "duration", 0.0) or 0.0)

    # ---------------------------------------------------------- placement
    def is_free(self, track: Track, start: float, end: float, ignore=()) -> bool:
        if start < -EPS:
            return False
        for c in track.clips:
            if c.id in ignore:
                continue
            if start < c.end - EPS and end > c.start + EPS:
                return False
        return True

    def add_media_clips(self, path: str, vtrack: Optional[Track], atrack: Optional[Track],
                        start: float):
        info = self.media[path]
        dur = float(info.duration)
        v = a = None
        if vtrack is not None and getattr(info, "has_video", True):
            v = Clip(self.new_id(), "video", path, start, 0.0, dur)
            vtrack.clips.append(v)
        if atrack is not None and info.has_audio:
            a = Clip(self.new_id(), "audio", path, start, 0.0, dur)
            atrack.clips.append(a)
        if v and a:
            v.link, a.link = a.id, v.id
        return v, a

    def track_end(self, track: Track) -> float:
        return max((c.end for c in track.clips), default=0.0)

    # ------------------------------------------------------------ editing
    def split(self, clips: List[Clip], t: float) -> List[Clip]:
        """Split each clip at timeline time t. Returns the new right-hand pieces."""
        rights: Dict[int, Clip] = {}
        for c in clips:
            if not (c.start + MIN_CLIP / 2 < t < c.end - MIN_CLIP / 2):
                continue
            r = copy.deepcopy(c)
            r.id = self.new_id()
            r.start = t
            r.src_in = c.src_at(t)
            c.src_out = r.src_in
            r.link = None
            self.track_of(c).clips.append(r)
            rights[c.id] = r
        for left_id, r in rights.items():
            left = self.clip(left_id)
            if left and left.link and left.link in rights:
                r.link = rights[left.link].id
        return list(rights.values())

    def delete(self, clips: List[Clip], ripple: bool) -> None:
        by_track: Dict[int, List[Clip]] = {}
        for c in clips:
            t = self.track_of(c)
            if t:
                by_track.setdefault(t.id, []).append(c)
        for t in self.tracks:
            gone = sorted(by_track.get(t.id, []), key=lambda c: c.start, reverse=True)
            for c in gone:
                t.clips.remove(c)
                partner = self.linked(c)
                if partner and partner not in clips:
                    partner.link = None
                if ripple:
                    for o in t.clips:
                        if o.start >= c.end - EPS:
                            o.start -= c.duration
        for t in self.tracks:   # tidy tiny negative values
            for c in t.clips:
                if c.start < 0:
                    c.start = 0.0

    def remove_ranges(self, ranges) -> float:
        """Cut the given timeline ranges [(a, b), ...] out of EVERY track and close the gaps,
        so picture and sound stay in sync. Clips are split where needed; linked video/sound
        pieces stay linked. Returns the total removed time."""
        rs = []
        for a, b in sorted((max(0.0, a), b) for a, b in ranges if b - a > EPS):
            if rs and a <= rs[-1][1] + EPS:
                rs[-1][1] = max(rs[-1][1], b)
            else:
                rs.append([a, b])
        if not rs:
            return 0.0
        starts = [a for a, _ in rs]
        cum = [0.0]
        for a, b in rs:
            cum.append(cum[-1] + b - a)

        def removed_before(t):            # how much is removed before timeline time t
            i = bisect.bisect_right(starts, t) - 1
            if i < 0:
                return 0.0
            a, b = rs[i]
            return cum[i] + min(b, t) - a

        new_ids = {}                      # (old clip id, new start) -> new clip id
        old_links = {}
        for tr in self.tracks:
            out = []
            for c in tr.sorted():
                pieces, pos = [], c.start
                i = max(0, bisect.bisect_right(starts, c.start) - 1)
                while i < len(rs) and rs[i][0] < c.end:
                    a, b = rs[i]
                    if b > pos:
                        if a > pos:
                            pieces.append((pos, min(a, c.end)))
                        pos = max(pos, b)
                    i += 1
                if pos < c.end:
                    pieces.append((pos, c.end))
                for s0, e0 in pieces:
                    if e0 - s0 < 0.02:
                        continue
                    n = copy.deepcopy(c)
                    n.id = self.new_id()
                    n.src_in, n.src_out = c.src_at(s0), c.src_at(e0)
                    n.start = s0 - removed_before(s0)
                    n.link = None
                    key = round(n.start, 4)
                    new_ids[(c.id, key)] = n.id
                    old_links[n.id] = (c.link, key)
                    out.append(n)
            tr.clips = out
        for tr in self.tracks:            # re-link pieces of linked clips that start together
            for n in tr.clips:
                old, key = old_links.get(n.id, (None, None))
                if old is not None:
                    n.link = new_ids.get((old, key))
        for tr in self.tracks:            # links must be mutual
            for n in tr.clips:
                if n.link is not None:
                    p = self.clip(n.link)
                    if p is None or p.link != n.id:
                        n.link = None
        return cum[-1]

    def unlink(self, clip: Clip) -> None:
        p = self.linked(clip)
        if p:
            p.link = None
        clip.link = None

    # --------------------------------------------------------- snapshots
    def snapshot(self):
        return copy.deepcopy((self.tracks, self._next))

    def restore(self, snap) -> None:
        self.tracks, self._next = copy.deepcopy(snap)
