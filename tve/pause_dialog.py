"""“Remove pauses” window: finds the quiet parts between speech and cuts them out.

The audio is scanned once (in the background, at low priority); after that every setting
change updates the result and the red marks on the timeline instantly.
"""
from __future__ import annotations

import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

from .builder import build_level_scan
from .ffmpeg_utils import popen_kwargs
from .pauses import find_pauses, parse_levels
from .theme import PANEL, dark_titlebar
from .timeline import TRACKS_Y
from .timeutil import fmt_time

MIN_PAUSES = [("0.3 s", 0.3), ("0.5 s  (recommended)", 0.5), ("0.8 s", 0.8),
              ("1.2 s", 1.2), ("2 s", 2.0)]
KEEPS = [("0.1 s  ·  tight", 0.1), ("0.2 s  ·  natural (recommended)", 0.2),
         ("0.3 s", 0.3), ("0.5 s  ·  relaxed", 0.5)]
SENSES = [("Remove less  (keeps breaths and quiet words)", "less"),
          ("Normal  (recommended)", "normal"),
          ("Remove more  (for a noisy room)", "more")]


class PauseDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.withdraw()
        self.title("Remove pauses")
        self.configure(bg=PANEL)
        self.transient(app)
        self.resizable(False, False)
        self.events = queue.Queue()
        self.proc = None
        self.cache = {}            # tracks key -> (levels, total)
        self.levels = None
        self.result = None
        self._scan_id = 0

        p = app.project
        self.sources = []
        auds = [t for t in p.audio_tracks() if t.clips]
        for t in auds:
            names = sorted({os.path.basename(c.path) for c in t.clips})
            label = f"{p.track_name(t)}  ·  {names[0]}" + (f" +{len(names) - 1}" if len(names) > 1 else "")
            self.sources.append((label, [t]))
        if len(auds) > 1:
            self.sources.append(("All audio tracks together", auds))

        b = ttk.Frame(self, padding=(22, 16, 22, 16))
        b.pack(fill="both", expand=True)
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="Remove pauses", style="H.TLabel").grid(row=0, column=0, columnspan=2,
                                                                 sticky="w")
        ttk.Label(b, text="Finds the quiet parts between speech and cuts them out of every "
                          "track, so picture and sound stay together and the video flows "
                          "without gaps. A little quiet is kept before and after speech, so "
                          "no word gets clipped.",
                  style="Muted.TLabel", wraplength=540, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 14))

        self.src_cb = self._combo(b, 2, "Listen to", [l for l, _ in self.sources], 0,
                                  self._start_scan)
        self.min_cb = self._combo(b, 3, "Remove pauses longer than", [l for l, _ in MIN_PAUSES],
                                  1, self._recompute)
        self.keep_cb = self._combo(b, 4, "Keep around speech", [l for l, _ in KEEPS], 1,
                                   self._recompute)
        self.sens_cb = self._combo(b, 5, "Sensitivity", [l for l, _ in SENSES], 1,
                                   self._recompute)
        if len(auds) > 1:
            ttk.Label(b, text="Tip: listen to the track with the voice, not the music.",
                      style="Muted.TLabel").grid(row=6, column=1, sticky="w")

        self.progress = ttk.Progressbar(b, maximum=100)
        self.progress.grid(row=7, column=0, columnspan=2, sticky="we", pady=(12, 2))
        self.result_var = tk.StringVar()
        self.detail_var = tk.StringVar()
        ttk.Label(b, textvariable=self.result_var, style="H.TLabel", wraplength=540,
                  justify="left").grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.detail = ttk.Label(b, textvariable=self.detail_var, style="Muted.TLabel",
                                wraplength=540, justify="left")
        self.detail.grid(row=9, column=0, columnspan=2, sticky="w", pady=(2, 0))

        btns = ttk.Frame(b)
        btns.grid(row=10, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Cancel", command=self.close).pack(side="right")
        self.go = ttk.Button(btns, text="Remove pauses", style="Accent.TButton",
                             command=self._apply)
        self.go.pack(side="right", padx=(0, 8))
        self.go.state(["disabled"])

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.update_idletasks()
        # sit above the tracks, so the red marks on the timeline stay visible
        tl = app.timeline
        x = app.winfo_rootx() + 30
        y = tl.winfo_rooty() + TRACKS_Y - 10 - self.winfo_reqheight()
        y = max(app.winfo_rooty() + 40, y)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        dark_titlebar(self)
        self.grab_set()
        self.after(60, self._poll)
        self._start_scan()

    # ---------------------------------------------------------------- helpers
    def _combo(self, parent, row, label, values, default, command):
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w",
                                                                padx=(0, 14), pady=4)
        var = tk.StringVar(value=values[default])
        cb = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=40)
        cb.grid(row=row, column=1, sticky="we", pady=4)
        var.trace_add("write", lambda *_: command())
        cb.var = var
        return cb

    @staticmethod
    def _pick(cb, pairs):
        return dict(pairs).get(cb.var.get(), pairs[0][1])

    # ------------------------------------------------------------------ scan
    def _start_scan(self):
        self._stop_proc()
        tracks = self._pick(self.src_cb, self.sources)
        key = tuple(t.id for t in tracks)
        self.go.state(["disabled"])
        self._set_preview(None)
        if key in self.cache:
            self.levels, self.total = self.cache[key]
            self.progress["value"] = 100
            self._recompute()
            return
        built = build_level_scan(self.app.project, tracks)
        if not built:
            self.levels = None
            self.result_var.set("There is no sound on this track.")
            self.detail_var.set("")
            return
        args, files, total = built
        for name, content in files.items():
            with open(os.path.join(self.app.work_dir, name), "w", encoding="utf-8") as fh:
                fh.write(content)
        self.levels = None
        self.total = total
        self.progress["value"] = 0
        self.result_var.set("Listening to the audio…")
        self.detail_var.set("")
        self._scan_id += 1
        scan_id = self._scan_id
        try:
            self.proc = subprocess.Popen([self.app.ff.ffmpeg] + args, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         cwd=self.app.work_dir, text=True, encoding="utf-8",
                                         errors="replace", **popen_kwargs(True))
        except OSError as e:
            self.result_var.set(f"Could not start FFmpeg: {e}")
            return
        proc = self.proc

        def read():
            lines = []
            for line in proc.stdout:
                lines.append(line)
                if len(lines) % 4000 == 0:
                    for back in lines[-1:-6:-1]:
                        i = back.find("pts_time:")
                        if i >= 0:
                            try:
                                self.events.put(("progress", scan_id,
                                                 float(back[i + 9:].split()[0]) / total))
                            except ValueError:
                                pass
                            break
            err = proc.stderr.read()
            rc = proc.wait()
            if rc != 0:
                self.events.put(("error", scan_id, err.strip()[-400:]))
            else:
                self.events.put(("done", scan_id, (key, parse_levels(lines, total))))
        threading.Thread(target=read, daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, sid, data = self.events.get_nowait()
                if sid != self._scan_id:
                    continue
                if kind == "progress":
                    self.progress["value"] = min(99, data * 100)
                    self.result_var.set(f"Listening to the audio…  {int(data * 100)}%")
                elif kind == "done":
                    key, levels = data
                    self.proc = None
                    self.cache[key] = (levels, self.total)
                    self.levels = levels
                    self.progress["value"] = 100
                    self._recompute()
                    self.app.timeline.fit()
                elif kind == "error":
                    self.proc = None
                    self.result_var.set("The audio could not be read.")
                    self.detail_var.set(data)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _stop_proc(self):
        if self.proc and self.proc.poll() is None:
            self._scan_id += 1          # ignore whatever the old scan still sends
            try:
                self.proc.terminate()
            except OSError:
                pass
        self.proc = None

    # ---------------------------------------------------------------- result
    def _recompute(self):
        if self.levels is None:
            return
        res = find_pauses(self.levels, self.total,
                          min_pause=self._pick(self.min_cb, MIN_PAUSES),
                          keep=self._pick(self.keep_cb, KEEPS),
                          sensitivity=self._pick(self.sens_cb, SENSES))
        self.result = res
        n = len(res.ranges)
        if n:
            self.result_var.set(f"Found {n} pause{'s' if n != 1 else ''}  ·  "
                                f"{fmt_time(res.removed, 1)} will be removed")
            total = self.app.project.end
            new_len = max(0.0, total - res.removed)
            warn = ""
            if total > 0 and res.removed / total > 0.8:
                warn = (f"⚠ This removes {res.removed / total:.0%} of the video. Make sure the "
                        f"right track is chosen.  ")
            self.detail_var.set(
                f"{warn}New length {fmt_time(new_len, 1)} (now {fmt_time(total, 1)}). "
                f"The red marks on the timeline show what will be cut. "
                f"Speech ≈ {res.speech_db:.0f} dB, background ≈ {res.noise_db:.0f} dB.")
            self.detail.configure(style="Err.TLabel" if warn else "Muted.TLabel")
            self.go.configure(text=f"Remove {n} pause{'s' if n != 1 else ''}")
            self.go.state(["!disabled"])
        else:
            self.result_var.set("No pauses to remove")
            self.detail_var.set(res.message)
            self.detail.configure(style="Muted.TLabel")
            self.go.configure(text="Remove pauses")
            self.go.state(["disabled"])
        self._set_preview(res.ranges)

    def _set_preview(self, ranges):
        self.app.pause_preview = ranges or None
        self.app.timeline.redraw()

    def _apply(self):
        res = self.result
        if not res or not res.ranges:
            return
        self.close()
        self.app.apply_pause_removal(res.ranges)

    def close(self):
        self._stop_proc()
        self._scan_id += 1
        self._set_preview(None)
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.app.pause_dlg = None
        self.destroy()
