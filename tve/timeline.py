"""Multi-track timeline widget.

Rows (top to bottom): ruler, range bar ([ ]), video tracks (top layer first), audio tracks.
Mouse:
  ruler / empty area ...... click or drag to move the playhead
  range bar ............... drag to select a range, drag [ or ] to resize, drag inside to move,
                            double-click to clear
  clip .................... click to select (with its linked audio/video; Alt = only this one),
                            drag to move, drag an edge to trim, right-click for a menu
  wheel / Ctrl+wheel ...... scroll / zoom
"""
from __future__ import annotations

import os
import tkinter as tk

from .project import EPS, MIN_CLIP
from .theme import (ACCENT, ACCENT_DIS, BG, BORDER, CARD, DANGER, FAINT, MUTED, OK, PANEL,
                    TEXT, WARN, font, rounded_rect)
from .timeutil import fmt_time

HEAD_W = 92
RULER_H = 24
RANGE_Y0 = 26
RANGE_H = 18
TRACKS_Y = RANGE_Y0 + RANGE_H + 6
VIDEO_H = 58
AUDIO_H = 46
ROW_GAP = 4
THUMB_W, THUMB_H = 68, 38
WAVE_PER_SEC = 50
SNAP_PX = 8

V_FILL, V_EDGE = "#26385c", "#3d5a91"
A_FILL, A_EDGE = "#1d3d37", "#2f6b5e"
ROW_A, ROW_B = "#181b21", "#1b1e25"


class TimelineView(tk.Canvas):
    def __init__(self, master, app):
        super().__init__(master, bg=BG, highlightthickness=0, height=200, takefocus=1)
        self.app = app
        self.pps = 40.0          # pixels per second
        self.t0 = 0.0            # time at left edge of the content area
        self.y0 = 0              # vertical scroll of the track area (pixels)
        self.mode = None
        self.hover = None
        self._snap = None
        self._track = None
        self._changed = False
        self._redraw_after = None
        self.scrollbar = None
        self.bind("<Configure>", lambda e: (setattr(self, "y0", min(self.y0, self.max_y0())),
                                            self.redraw()))
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<Motion>", self._hover)
        self.bind("<Button-3>", self._menu)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda e: self._wheel(e, 120))
        self.bind("<Button-5>", lambda e: self._wheel(e, -120))

    # ------------------------------------------------------------- geometry
    @property
    def project(self):
        return self.app.project

    def rows(self, scrolled=True):
        out, y = [], TRACKS_Y - (self.y0 if scrolled else 0)
        if not self.project:
            return out
        for tr in list(reversed(self.project.video_tracks())) + self.project.audio_tracks():
            h = VIDEO_H if tr.kind == "video" else AUDIO_H
            out.append((tr, y, h))
            y += h + ROW_GAP
        return out

    def needed_height(self):
        r = self.rows(scrolled=False)
        return (r[-1][1] + r[-1][2] + 6) if r else TRACKS_Y + 40

    def max_y0(self):
        return max(0, self.needed_height() - max(self.winfo_height(), 100))

    def scroll_y(self, dy):
        self.y0 = max(0, min(self.max_y0(), self.y0 + dy))
        self.redraw()

    def content_w(self):
        return max(100, self.winfo_width() - HEAD_W - 12)

    def x_of(self, t):
        return HEAD_W + (t - self.t0) * self.pps

    def t_of(self, x):
        return max(0.0, self.t0 + (x - HEAD_W) / self.pps)

    def total_len(self):
        end = self.project.end if self.project else 0
        return max(end * 1.1 + 5, self.content_w() / self.pps, 1.0)

    # --------------------------------------------------------- zoom/scroll
    def fit(self):
        end = max(self.project.end if self.project else 1, 1.0)
        self.pps = max(0.5, (self.content_w() - 20) / end)
        self.t0 = 0.0
        self.redraw()

    def zoom(self, factor, anchor_x=None):
        if anchor_x is None:
            anchor_x = self.x_of(self.app.cur_time)
            if not HEAD_W <= anchor_x <= self.winfo_width():
                anchor_x = HEAD_W + self.content_w() / 2
        ta = self.t_of(anchor_x)
        self.pps = max(0.5, min(600.0, self.pps * factor))
        self.t0 = max(0.0, ta - (anchor_x - HEAD_W) / self.pps)
        self.redraw()

    def scroll_to(self, t0):
        vis = self.content_w() / self.pps
        self.t0 = max(0.0, min(t0, self.total_len() - vis))
        self.redraw()

    def xview_cmd(self, *args):
        vis = self.content_w() / self.pps
        total = self.total_len()
        if args[0] == "moveto":
            self.scroll_to(float(args[1]) * total)
        elif args[0] == "scroll":
            step = vis * (0.9 if args[2] == "pages" else 0.1)
            self.scroll_to(self.t0 + int(args[1]) * step)

    def ensure_visible(self, t):
        vis = self.content_w() / self.pps
        if t < self.t0 or t > self.t0 + vis * 0.97:
            self.scroll_to(max(0.0, t - vis * 0.1))

    def _wheel(self, e, delta=None):
        d = delta if delta is not None else e.delta
        if e.state & 0x4:   # Ctrl -> zoom
            self.zoom(1.25 if d > 0 else 0.8, e.x)
        elif self.max_y0() > 0 and not (e.state & 0x1) and e.y >= TRACKS_Y:
            self.scroll_y(-40 if d > 0 else 40)
        else:
            self.scroll_to(self.t0 - (d / 120.0) * (self.content_w() / self.pps) * 0.15)

    # ---------------------------------------------------------------- draw
    def schedule_redraw(self):
        if not self._redraw_after:
            self._redraw_after = self.after(80, self.redraw)

    def redraw(self):
        self._redraw_after = None
        self.delete("all")
        W = max(self.winfo_width(), 300)
        Hh = max(self.winfo_height(), 100)
        if not self.project:
            return
        rows = self.rows()
        x_end = self.x_of(self.project.end)

        # track rows
        for i, (tr, y, h) in enumerate(rows):
            self.create_rectangle(HEAD_W, y, W, y + h, fill=ROW_A if i % 2 else ROW_B, outline="")
        if x_end < W:
            self.create_line(x_end, TRACKS_Y, x_end, Hh, fill=BORDER, dash=(3, 3))

        # clips
        sel = self.app.selected
        for tr, y, h in rows:
            for c in tr.clips:
                x0, x1 = self.x_of(c.start), self.x_of(c.end)
                if x1 < HEAD_W or x0 > W:
                    continue
                self._draw_clip(c, tr, x0, x1, y, h, c.id in sel)

        # range overlay
        rng = self.app.range
        if rng:
            xa, xb = self.x_of(rng[0]), self.x_of(rng[1])
            if rows:
                ybot = rows[-1][1] + rows[-1][2]
                self.create_rectangle(xa, TRACKS_Y - 2, xb, ybot, fill=ACCENT, outline="",
                                      stipple="gray12")
                for x in (xa, xb):
                    self.create_line(x, TRACKS_Y - 2, x, ybot, fill=ACCENT, width=1)

        # ruler (also masks track rows scrolled under it)
        self.create_rectangle(0, 0, W, TRACKS_Y - 2, fill=BG, outline="")
        self.create_rectangle(0, 0, W, RULER_H, fill=PANEL, outline="")
        vis = self.content_w() / self.pps
        step = next((s for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900,
                                 1800, 3600) if s * self.pps >= 70), 7200)
        t = (int(self.t0 / step)) * step
        while t <= self.t0 + vis + step:
            x = self.x_of(t)
            if x >= HEAD_W:
                self.create_line(x, RULER_H - 7, x, RULER_H, fill=FAINT)
                self.create_text(x + 3, 4, text=fmt_time(t, 1 if step < 1 else 0), anchor="nw",
                                 fill=MUTED, font=font(8))
                for k in range(1, 5):
                    xm = self.x_of(t + step * k / 5)
                    if xm >= HEAD_W:
                        self.create_line(xm, RULER_H - 3, xm, RULER_H, fill=BORDER)
            t += step

        # range bar
        self.create_rectangle(HEAD_W, RANGE_Y0, W, RANGE_Y0 + RANGE_H, fill="#171a20", outline="")
        if rng:
            xa, xb = self.x_of(rng[0]), self.x_of(rng[1])
            self.create_rectangle(xa, RANGE_Y0 + 2, xb, RANGE_Y0 + RANGE_H - 2, fill=ACCENT_DIS,
                                  outline="")
            for which, x in (("ra", xa), ("rb", xb)):
                col = "#ffffff" if self.hover == which or self.mode == which else ACCENT
                self._bracket(x, which == "ra", col)
            if xb - xa > 70:
                self.create_text((xa + xb) / 2, RANGE_Y0 + RANGE_H / 2,
                                 text=fmt_time(rng[1] - rng[0], 1), fill=TEXT, font=font(8, "bold"))
        else:
            self.create_text(HEAD_W + 10, RANGE_Y0 + RANGE_H / 2, anchor="w", fill=FAINT,
                             font=font(8), text="[ ]  Drag here to select a range — then press S "
                                                 "to cut it out as its own clip")

        # headers
        self.create_rectangle(0, 0, HEAD_W - 1, Hh, fill=PANEL, outline="")
        self.create_line(HEAD_W - 1, 0, HEAD_W - 1, Hh, fill=BORDER)
        self.create_text(12, RANGE_Y0 + RANGE_H / 2, text="Range", anchor="w", fill=FAINT,
                         font=font(8))
        for tr, y, h in rows:
            if y + h / 2 < TRACKS_Y:
                continue
            name = self.project.track_name(tr)
            dragging = self.mode == "track" and self._track is tr
            if dragging:
                self.create_rectangle(0, y, HEAD_W - 1, y + h, fill=CARD, outline=ACCENT)
            for gx in (8, 12):
                for gy in (-5, 0, 5):
                    self.create_oval(gx - 1, y + h / 2 + gy - 1, gx + 1, y + h / 2 + gy + 1,
                                     fill=ACCENT if dragging else FAINT, outline="")
            self.create_text(20, y + h / 2, text=name, anchor="w", fill=TEXT,
                             font=font(10, "bold"))
            if tr.kind == "video":
                icon = "○" if tr.hidden else "◉"
            else:
                icon = "✕" if tr.hidden else "♪"
            self.create_text(58, y + h / 2, text=icon, fill=MUTED if tr.hidden else TEXT,
                             font=font(11))
            if self._removable(tr):
                self.create_text(80, y + h / 2, text="×", fill=MUTED, font=font(13))

        # playhead
        self._draw_playhead()

        if self.max_y0() > 0:
            self.create_text(W - 8, Hh - 4, anchor="se", fill=FAINT, font=font(8),
                             text="more tracks: scroll ↕")
        if self.scrollbar is not None:
            total = self.total_len()
            lo = self.t0 / total
            self.scrollbar.set(lo, min(1.0, lo + vis / total))

    def _removable(self, tr):
        lst = self.project.video_tracks() if tr.kind == "video" else self.project.audio_tracks()
        return len(lst) > 1

    def _draw_playhead(self):
        self.delete("ph")
        x = self.x_of(self.app.cur_time)
        if x < HEAD_W - 1 or x > self.winfo_width():
            return
        bot = max(self.winfo_height(), 100)
        self.create_line(x, 6, x, bot, fill="#ffffff", width=2, tags="ph")
        self.create_polygon(x - 6, 2, x + 6, 2, x + 6, 9, x, 15, x - 6, 9, fill="#ffffff",
                            outline="", tags="ph")

    def set_playhead(self):
        self._draw_playhead()

    def _bracket(self, x, left, color):
        y0, y1 = RANGE_Y0, RANGE_Y0 + RANGE_H
        w, t = 6, 3
        if left:
            pts = [x - t, y0, x + w, y0, x + w, y0 + t, x, y0 + t, x, y1 - t, x + w, y1 - t,
                   x + w, y1, x - t, y1]
        else:
            pts = [x + t, y0, x - w, y0, x - w, y0 + t, x, y0 + t, x, y1 - t, x - w, y1 - t,
                   x - w, y1, x + t, y1]
        self.create_polygon(pts, fill=color, outline="")

    def _draw_clip(self, c, tr, x0, x1, y, h, selected):
        W = self.winfo_width()
        vx0, vx1 = max(x0, HEAD_W), min(x1, W + 4)
        video = c.kind == "video"
        fill, edge = (V_FILL, V_EDGE) if video else (A_FILL, A_EDGE)
        partner = (not selected) and c.link is not None and c.link in self.app.selected
        rounded_rect(self, x0 + 1, y + 2, x1 - 1, y + h - 2, 6, fill=fill,
                     outline="#ffffff" if selected else ("#8fa9e8" if partner else edge),
                     width=2 if selected else 1)
        if video:
            thumbs = self.app.thumbs.get(c.path)
            if thumbs:
                ty = y + 16
                n = len(thumbs)
                dur = max(self.project.media_duration(c.path), 0.001)
                x = x0 + 3
                while x + THUMB_W <= x1 - 3:
                    if x + THUMB_W >= vx0 and x <= vx1:
                        tmid = c.start + (x + THUMB_W / 2 - x0) / self.pps
                        idx = min(n - 1, max(0, int(c.src_at(tmid) / dur * n)))
                        img = thumbs[idx]
                        if img is not None:
                            self.create_image(x, ty, image=img, anchor="nw")
                    x += THUMB_W + 1
        else:
            peaks = self.app.waves.get(c.path)
            mid = y + h / 2 + 5
            amp = (h - 22) / 2
            if peaks:
                top, bot = [], []
                xs = vx0 + 2
                while xs <= vx1 - 2:
                    t = c.start + (xs - x0) / self.pps
                    i = int(c.src_at(t) * WAVE_PER_SEC)
                    j = int(c.src_at(t + 2 / self.pps) * WAVE_PER_SEC)
                    seg = peaks[max(0, i):max(i + 1, j)] if i < len(peaks) else []
                    p = max(seg) if seg else 0.0
                    p = min(1.0, p * c.volume) * amp
                    top += [xs, mid - p]
                    bot += [xs, mid + p]
                    xs += 2
                if len(top) >= 4:
                    pts = top + [v for k in range(len(bot) - 2, -1, -2) for v in bot[k:k + 2]]
                    self.create_polygon(pts, fill=FAINT if (c.muted or tr.hidden) else OK,
                                        outline="")
            else:
                self.create_line(vx0 + 2, mid, vx1 - 2, mid, fill=A_EDGE)
        # label + badges
        badges = []
        if abs(c.speed - 1) > EPS:
            badges.append(f"{c.speed:g}x")
        if c.effect:
            badges.append("✦")
        if c.text:
            badges.append("T")
        if c.rotate:
            badges.append("⟳")
        if c.custom:
            badges.append("⚙")
        if c.muted:
            badges.append("muted")
        if not video and abs(c.volume - 1) > EPS:
            badges.append(f"{int(round(c.volume * 100))}%")
        if c.link is None and self.project.main_path and c.path == self.project.main_path:
            pass
        label = os.path.basename(c.path)
        if badges:
            label = "  ".join(badges) + "   " + label
        avail = int((vx1 - vx0 - 12) / 6.5)
        if avail > 3:
            if len(label) > avail:
                label = label[:max(1, avail - 1)] + "…"
            self.create_text(vx0 + 7, y + 9, text=label, anchor="w",
                             fill="#ffffff" if selected else TEXT, font=font(8))
        if tr.hidden:
            self.create_rectangle(vx0, y + 2, vx1, y + h - 2, fill="#000000", outline="",
                                  stipple="gray50")

    # ---------------------------------------------------------- hit tests
    def row_at(self, y):
        if y < TRACKS_Y - 2:
            return None
        for tr, y0, h in self.rows():
            if y0 <= y <= y0 + h:
                return tr, y0, h
        return None

    def clip_at(self, x, y):
        r = self.row_at(y)
        if not r:
            return None, None
        tr = r[0]
        t = self.t_of(x)
        for c in tr.clips:
            if c.start - 2 / self.pps <= t <= c.end + 2 / self.pps:
                return tr, c
        return tr, None

    def _range_hit(self, x):
        rng = self.app.range
        if not rng:
            return None
        xa, xb = self.x_of(rng[0]), self.x_of(rng[1])
        if abs(x - xa) <= 8:
            return "ra"
        if abs(x - xb) <= 8:
            return "rb"
        if xa < x < xb:
            return "rmove"
        return None

    def _clip_part(self, c, x):
        x0, x1 = self.x_of(c.start), self.x_of(c.end)
        edge = min(7, (x1 - x0) / 4)
        if x - x0 <= edge:
            return "trim_l"
        if x1 - x <= edge:
            return "trim_r"
        return "move"

    # -------------------------------------------------------------- mouse
    def _press(self, e):
        self.focus_set()
        app = self.app
        if not self.project:
            return
        app.stop_play()
        self.mode = None
        self._changed = False
        t = self.t_of(e.x)
        if e.x < HEAD_W:
            r = self.row_at(e.y)
            if r:
                tr = r[0]
                if 48 <= e.x <= 68:
                    app.toggle_track(tr)
                elif e.x >= 72 and self._removable(tr):
                    app.remove_track(tr)
                else:
                    # drag the track name up/down to change the track order
                    self.mode = "track"
                    self._track = tr
                    self._snap = self.project.snapshot()
                    self.redraw()
            return
        if e.y < RULER_H:
            self.mode = "scrub"
            app.seek(t)
            return
        if e.y < TRACKS_Y - 2:
            hit = self._range_hit(e.x)
            if hit:
                self.mode = hit
                self._range_off = t - app.range[0]
            else:
                self.mode = "rnew"
                self._anchor = t
            return
        tr, c = self.clip_at(e.x, e.y)
        if c is None:
            app.select([])
            self.mode = "scrub"
            app.seek(t)
            return
        alt = bool(e.state & 0x20000) or bool(e.state & 0x8)
        ctrl = bool(e.state & 0x4)
        if ctrl:
            ids = set(app.selected) ^ {c.id}
            app.select([self.project.clip(i) for i in ids])
        elif c.id not in app.selected:
            app.select([c])
        group = [c] if alt else [c] + ([self.project.linked(c)] if self.project.linked(c) else [])
        self.mode = self._clip_part(c, e.x)
        self._snap = self.project.snapshot()
        self._press_t = t
        self._press_track = tr
        self._clip = c
        if self.mode == "move":
            moving = [self.project.clip(i) for i in app.selected]
            if not alt:   # the linked video/sound moves along so they stay in sync
                moving += [self.project.linked(m) for m in list(moving) if m and m.link]
        else:
            moving = group
        self._moving = list({m.id: m for m in moving if m is not None}.values())
        self._orig = {m.id: (m.start, m.src_in, m.src_out, self.project.track_of(m))
                      for m in self._moving}

    def _motion(self, e):
        app = self.app
        if not self.mode or not self.project:
            return
        t = self.t_of(e.x)
        if self.mode == "scrub":
            app.seek(t)
        elif self.mode == "rnew":
            if abs(t - self._anchor) * self.pps > 3:
                a, b = sorted((self._anchor, t))
                app.set_range(a, b)
                app.seek(t)
        elif self.mode in ("ra", "rb", "rmove"):
            a, b = app.range
            if self.mode == "ra":
                a = min(t, b - MIN_CLIP)
                app.seek(a)
            elif self.mode == "rb":
                b = max(t, a + MIN_CLIP)
                app.seek(b)
            else:
                ln = b - a
                a = max(0.0, t - self._range_off)
                a = self._snap_time(a, ln, exclude=())
                b = a + ln
                app.seek(a)
            app.set_range(max(0.0, a), b)
        elif self.mode == "track":
            r = self.row_at(e.y)
            if r and r[0] is not self._track and r[0].kind == self._track.kind:
                self.project.swap_tracks(self._track, r[0])
                self._changed = True
                self.redraw()
        elif self.mode == "move":
            self._do_move(e, t)
        elif self.mode in ("trim_l", "trim_r"):
            self._do_trim(t)

    def _snap_targets(self, exclude):
        pts = [0.0, self.app.cur_time]
        if self.app.range:
            pts += list(self.app.range)
        for c in self.project.all_clips():
            if c.id not in exclude:
                pts += [c.start, c.end]
        return pts

    def _snap_time(self, start, length, exclude):
        best, bd = start, SNAP_PX / self.pps
        for p in self._snap_targets(exclude):
            for edge, off in ((start, 0.0), (start + length, length)):
                if abs(edge - p) < bd:
                    bd = abs(edge - p)
                    best = p - off
        return max(0.0, best)

    def _do_move(self, e, t):
        proj = self.project
        dt = t - self._press_t
        ids = {m.id for m in self._moving}
        lead = self._orig[self._clip.id][0]
        new_lead = self._snap_time(lead + dt, self._clip.duration, ids)
        dt = new_lead - lead
        if min(self._orig[m.id][0] for m in self._moving) + dt < 0:
            dt = -min(self._orig[m.id][0] for m in self._moving)
        # vertical move of the grabbed clip to another track of the same kind
        target = self._orig[self._clip.id][3]
        r = self.row_at(e.y)
        if r and r[0].kind == self._clip.kind:
            target = r[0]
        plan = []
        for m in self._moving:
            home = self._orig[m.id][3]
            tr = target if m is self._clip else home
            ns = self._orig[m.id][0] + dt
            if not proj.is_free(tr, ns, ns + m.duration, ignore=ids):
                if tr is home or not proj.is_free(home, ns, ns + m.duration, ignore=ids):
                    return
                tr = home       # that track is busy here: stay on our own track
            plan.append((m, tr, ns))
        for m, tr, ns in plan:
            cur = proj.track_of(m)
            if cur is not tr:
                cur.clips.remove(m)
                tr.clips.append(m)
            m.start = ns
        self._changed = True
        self.redraw()
        self.app.on_live_edit()

    def _do_trim(self, t):
        proj = self.project
        ids = {m.id for m in self._moving}
        if self.mode == "trim_l":
            new_start = self._snap_time(t, 0.0, ids)
            for m in self._moving:
                s0, i0, o0, tr = self._orig[m.id]
                lo = s0 - i0 / m.speed
                for o in tr.clips:
                    if o.id not in ids and o.end <= s0 + EPS:
                        lo = max(lo, o.end)
                hi = s0 + (o0 - i0) / m.speed - MIN_CLIP
                new_start = max(lo, min(hi, new_start))
            for m in self._moving:
                s0, i0, o0, tr = self._orig[m.id]
                m.start = new_start
                m.src_in = i0 + (new_start - s0) * m.speed
            self.app.seek(new_start, live=True)
        else:
            new_end = self._snap_time(t, 0.0, ids)
            for m in self._moving:
                s0, i0, o0, tr = self._orig[m.id]
                hi = s0 + (proj.media_duration(m.path) - i0) / m.speed
                for o in tr.clips:
                    if o.id not in ids and o.start >= s0 + (o0 - i0) / m.speed - EPS:
                        hi = min(hi, o.start)
                lo = s0 + MIN_CLIP
                new_end = max(lo, min(hi, new_end))
            for m in self._moving:
                s0, i0, o0, tr = self._orig[m.id]
                m.src_out = i0 + (new_end - s0) * m.speed
            self.app.seek(max(0.0, new_end - 0.001), live=True)
        self._changed = True
        self.redraw()
        self.app.on_live_edit()

    def _release(self, _e):
        if self.mode == "track" and self._changed:
            self.app.commit_edit(self._snap, f"Track moved — it is now "
                                             f"{self.project.track_name(self._track)}.")
        elif self.mode in ("move", "trim_l", "trim_r") and self._changed:
            self.app.commit_edit(self._snap, "Clip moved" if self.mode == "move" else "Clip trimmed")
        elif self.mode in ("ra", "rb", "rmove", "rnew"):
            self.app.on_range_done()
        self.mode = None
        self._snap = None
        self._track = None
        self.redraw()

    def _double(self, e):
        if RANGE_Y0 <= e.y <= RANGE_Y0 + RANGE_H and e.x > HEAD_W:
            self.app.clear_range()

    def _hover(self, e):
        if self.mode or not self.project:
            return
        cur, hov = "", None
        if e.x >= HEAD_W:
            if RANGE_Y0 <= e.y <= RANGE_Y0 + RANGE_H:
                hov = self._range_hit(e.x)
                cur = "sb_h_double_arrow" if hov in ("ra", "rb") else (
                    "fleur" if hov == "rmove" else "crosshair")
            elif e.y >= TRACKS_Y:
                _, c = self.clip_at(e.x, e.y)
                if c:
                    part = self._clip_part(c, e.x)
                    cur = "sb_h_double_arrow" if part != "move" else "fleur"
        else:
            cur = "sb_v_double_arrow" if (e.x < 46 and self.row_at(e.y)) else "hand2"
        self.configure(cursor=cur)
        if hov != self.hover:
            self.hover = hov
            self.redraw()

    def _menu(self, e):
        app = self.app
        if not self.project:
            return
        app.stop_play()
        if e.x < HEAD_W:
            r = self.row_at(e.y)
            if r:
                tr = r[0]
                same = [x[0] for x in self.rows() if x[0].kind == tr.kind]
                i = same.index(tr)
                m = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT,
                            activeforeground="#ffffff", bd=0, relief="flat", font=font(10))
                if i > 0:
                    m.add_command(label="Move track up", command=lambda: app.swap_tracks(tr, same[i - 1]))
                if i < len(same) - 1:
                    m.add_command(label="Move track down",
                                  command=lambda: app.swap_tracks(tr, same[i + 1]))
                if self._removable(tr):
                    m.add_separator()
                    m.add_command(label=f"Remove track {self.project.track_name(tr)}",
                                  command=lambda: app.remove_track(tr))
                m.tk_popup(e.x_root, e.y_root)
            return
        m = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT,
                    activeforeground="#ffffff", bd=0, relief="flat", font=font(10))
        tr, c = self.clip_at(e.x, e.y) if e.y >= TRACKS_Y else (None, None)
        if c:
            if c.id not in app.selected:
                app.select([c])
            m.add_command(label="Split at playhead      S", command=app.split)
            m.add_command(label="Delete                 Del", command=lambda: app.delete(True))
            m.add_command(label="Delete, leave a gap    Shift+Del",
                          command=lambda: app.delete(False))
            if c.link:
                m.add_command(label="Unlink audio and video", command=lambda: app.unlink(c))
            m.add_separator()
        m.add_command(label="Move playhead here", command=lambda: app.seek(self.t_of(e.x)))
        if app.range:
            m.add_command(label="Clear range", command=app.clear_range)
        m.tk_popup(e.x_root, e.y_root)
