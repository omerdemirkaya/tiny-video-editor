"""Tiny Video Editor main window (Tkinter).

Flow: the welcome screen asks for a video -> the editor opens with the video on track V1
and its sound on track A1. Everything the user does lives in `self.project`
(tracks and clips) and is only rendered when exporting.
"""
from __future__ import annotations

import base64
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import builder as _builder
from .builder import (BuildError, OutputSettings, ProjectSettings, build_export, build_frame,
                      build_play, copy_segments, output_extension, preview_size)
from .dialogs import ask_form
from .dnd import hook_file_drop
from .ffmpeg_utils import HW_NAMES, IS_WIN, FFJob, FFmpeg, ProbeError, find_ffmpeg, popen_kwargs
from .operations import (ASPECT_CHOICES, FILTER_HAS_STRENGTH, FILTER_PRESETS, FPS_CHOICES,
                         LAYOUT_CHOICES, ROTATE_CHOICES, SPEED_CHOICES, TEXT_COLORS,
                         TEXT_POSITIONS, TEXT_SIZES, VOLUME_CHOICES)
from .player import Player
from .project import EPS, MIN_CLIP, Project
from .theme import (ACCENT, BG, BORDER, CARD, DANGER, FAINT, MUTED, OK, PANEL, TEXT, ScrollFrame,
                    Tooltip,
                    apply_theme, dark_titlebar, draw_logo, font, mono, rounded_rect)
from .timeline import HEAD_W, THUMB_H, THUMB_W, WAVE_PER_SEC, TimelineView
from .timeutil import fmt_time

APP_NAME = "Tiny Video Editor"
INSPECTOR_W = 372
def _settings_path() -> str:
    if IS_WIN:
        base = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "TinyVideoEditor")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/TinyVideoEditor")
    else:
        base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "tiny_video_editor")
    return os.path.join(base, "settings.json")


SETTINGS_PATH = _settings_path()
OLD_SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".tiny_video_editor.json")
FFMPEG_MISSING = ("FFmpeg is missing from the program folder (ffmpeg\\bin).\n"
                  "Please copy Tiny Video Editor again with its ffmpeg folder.")
DEFAULT_NAME = "new_video"
UNDO_LIMIT = 100

VIDEO_EXT = "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.wmv *.flv *.mpg *.mpeg *.ts *.mts *.3gp"
AUDIO_EXT = "*.mp3 *.wav *.m4a *.aac *.ogg *.oga *.opus *.flac *.wma"
VIDEO_TYPES = [("Video files", VIDEO_EXT), ("All files", "*.*")]
AUDIO_TYPES = [("Audio files", AUDIO_EXT), ("Video files (use their sound)", VIDEO_EXT),
               ("All files", "*.*")]
FORMATS = [("MP4  ·  H.264 (plays everywhere)", "mp4"),
           ("MP4  ·  H.265 (smaller file)", "mp4_hevc"),
           ("MP3  ·  audio only", "mp3"),
           ("GIF  ·  animated picture", "gif")]
QUALITIES = [("Original quality (recommended)", "original"), ("High quality", "high"),
             ("Balanced", "balanced"), ("Small file", "small"), ("Very small file", "tiny")]
ENC_SPEEDS = [("Fastest (weak computer)", "fastest"), ("Fast (recommended)", "fast"),
              ("Slow (slightly smaller file)", "slow")]
FADES = [("No fade", 0.0), ("Short  ·  0.5 s", 0.5), ("Normal  ·  1 s", 1.0),
         ("Long  ·  2 s", 2.0)]
STANDARD_SHORTS = [2160, 1440, 1080, 720, 540, 480, 360, 240]
KNOWN_RATIOS = [(16, 9), (9, 16), (4, 3), (3, 4), (1, 1), (4, 5), (21, 9), (5, 4), (2, 1)]
BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

SPLIT_HELP = ("Split  ·  S key\n\n"
              "• Put the white playhead where you want to cut and press S. The clip under it "
              "is split in two.\n"
              "• Or drag on the range bar above the tracks to select a range [ ], then press S. "
              "That part becomes its own clip and gets selected.\n"
              "• Then press Delete to remove it, or use the Clip panel on the right to add an "
              "effect, text, speed or volume only to that piece.\n"
              "• If clips are selected, only those clips (and their linked sound) are split.")


# ------------------------------------------------------------------ helpers
def load_settings() -> dict:
    path = SETTINGS_PATH if os.path.exists(SETTINGS_PATH) or \
        not os.path.exists(OLD_SETTINGS_PATH) else OLD_SETTINGS_PATH
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def open_path(path: str) -> None:
    try:
        if IS_WIN:
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def reveal_in_folder(path: str) -> None:
    try:
        if IS_WIN:
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        else:
            open_path(os.path.dirname(path))
    except Exception:
        pass


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def ratio_name(w: int, h: int) -> str:
    for a, b in KNOWN_RATIOS:
        if abs(w / h - a / b) < 0.02:
            return f"{a}:{b}"
    g = math.gcd(w, h)
    if w // g <= 32 and h // g <= 32:
        return f"{w // g}:{h // g}"
    return f"{w / h:.2f}:1"


def write_files(folder: str, files: dict) -> None:
    for name, content in files.items():
        with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
            fh.write(content)


# ============================================================ export dialog
class ExportDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.withdraw()
        self.title("Export video")
        self.configure(bg=PANEL)
        self.transient(app)
        self.resizable(False, False)
        b = ttk.Frame(self, padding=(24, 20, 24, 20))
        b.pack(fill="both", expand=True)
        b.columnconfigure(1, weight=1)
        ttk.Label(b, text="Export", style="H.TLabel").grid(row=0, column=0, columnspan=3,
                                                          sticky="w", pady=(0, 14))
        r = 1

        def row(label, widget, extra=None, pady=5):
            nonlocal r
            ttk.Label(b, text=label, style="Field.TLabel").grid(row=r, column=0, sticky="w",
                                                               padx=(0, 16), pady=pady)
            widget.grid(row=r, column=1, sticky="we", pady=pady)
            if extra is not None:
                extra.grid(row=r, column=2, sticky="w", padx=(6, 0))
            r += 1

        self.fmt_cb = ttk.Combobox(b, textvariable=app.fmt_var, state="readonly", width=36,
                                   values=list(app.maps["fmt"]))
        row("Format", self.fmt_cb)
        self.q_cb = ttk.Combobox(b, textvariable=app.q_var, state="readonly",
                                 values=list(app.maps["q"]))
        row("Quality", self.q_cb)
        sz = ttk.Frame(b)
        ttk.Checkbutton(sz, text="Limit file size to", variable=app.target_on).pack(side="left")
        ttk.Entry(sz, textvariable=app.target_var, width=7).pack(side="left", padx=6)
        ttk.Label(sz, text="MB").pack(side="left")
        row("File size", sz)
        ttk.Label(b, textvariable=app.size_hint_var, style="Muted.TLabel").grid(
            row=r, column=1, sticky="w")
        r += 1
        self.enc_cb = ttk.Combobox(b, textvariable=app.enc_var, state="readonly",
                                   values=list(app.maps["enc"]))
        row("Encoding speed", self.enc_cb)
        opts = ttk.Frame(b)
        ttk.Checkbutton(opts, text="Fast cut (no re-encoding, takes seconds)",
                        variable=app.copy_var).pack(anchor="w")
        self.hw_ck = ttk.Checkbutton(opts, textvariable=app.hw_text, variable=app.hw_var)
        self.hw_ck.pack(anchor="w")
        ttk.Checkbutton(opts, text="Keep the computer responsive (low priority)",
                        variable=app.low_var).pack(anchor="w")
        row("Options", opts, pady=(8, 8))
        fr = ttk.Frame(b)
        ttk.Entry(fr, textvariable=app.dir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(fr, text="…", style="Tool.TButton", width=3,
                   command=app.choose_dir).pack(side="left", padx=(6, 0))
        row("Save to folder", fr)
        fr = ttk.Frame(b)
        ttk.Entry(fr, textvariable=app.name_var).pack(side="left", fill="x", expand=True)
        ttk.Label(fr, textvariable=app.ext_var, style="Muted.TLabel", width=5).pack(
            side="left", padx=(6, 0))
        row("File name", fr)
        self.summary = ttk.Label(b, textvariable=app.summary_var, style="Muted.TLabel",
                                 wraplength=440, justify="left")
        self.summary.grid(row=r, column=0, columnspan=3, sticky="we", pady=(14, 6))
        r += 1
        self.progress = ttk.Progressbar(b, maximum=100)
        self.progress.grid(row=r, column=0, columnspan=3, sticky="we", pady=(4, 2))
        r += 1
        ttk.Label(b, textvariable=app.job_status, style="Muted.TLabel").grid(
            row=r, column=0, columnspan=3, sticky="w")
        r += 1
        btns = ttk.Frame(b)
        btns.grid(row=r, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="Close", command=self.close).pack(side="right")
        self.go = ttk.Button(btns, text="Export", style="Accent.TButton", command=app.export)
        self.go.pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.refresh()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + max(20, (app.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        dark_titlebar(self)

    def refresh(self):
        app = self.app
        busy = app.job is not None
        copy = app.copy_var.get()
        for cb in (self.fmt_cb, self.q_cb, self.enc_cb):
            cb.configure(state="disabled" if copy or busy else "readonly")
        self.hw_ck.configure(state="normal" if app.hw_found and not copy else "disabled")
        if busy:
            self.go.configure(text="Cancel", style="Danger.TButton", command=app.cancel_job)
        else:
            self.go.configure(text="Export", style="Accent.TButton", command=app.export)
        self.summary.configure(style="Err.TLabel" if app.summary_error else "Muted.TLabel")
        self.progress["value"] = app.job_progress
        if busy or app.job_progress:
            self.progress.grid()
        else:
            self.progress.grid_remove()

    def close(self):
        self.app.export_dlg = None
        self.destroy()


# =================================================================== app
class App(tk.Tk):
    def __init__(self, initial_file=None):
        super().__init__()
        self.title(APP_NAME)
        apply_theme(self)
        self._set_icon()
        self.settings = load_settings()
        self.ff = None
        self.project = None
        self.selected = set()
        self.range = None
        self.cur_time = 0.0
        self.undo_stack, self.redo_stack = [], []
        self.thumbs, self.waves, self._cache_started = {}, {}, set()
        self.hw_found = []
        self.job = None
        self.job_ctx = None
        self.job_progress = 0
        self.export_dlg = None
        self.summary_error = False
        self.events = queue.Queue()
        self.maps = {}
        self.player = None
        self.playing = False
        self._play_photo = None
        self._photo = None
        self._frame_after = None
        self._frame_busy = False
        self._frame_pending = False
        self._summary_after = None
        self._guard = False
        self._loading = False
        self._initial_file = initial_file
        self.editor = None
        self.work_dir = tempfile.mkdtemp(prefix="tve_work_")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        pw = max(480, min(860, int(sw * 0.40)))
        ph = pw * 9 // 16
        if ph > sh * 0.40:
            ph = int(sh * 0.40)
            pw = ph * 16 // 9
        self.PW, self.PH = pw // 2 * 2, ph // 2 * 2
        self._init_vars()

        self.welcome = self._build_welcome()
        self.welcome.pack(fill="both", expand=True)
        self._place_window(1200, 760)
        dark_titlebar(self)
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll)
        self.after(120, self._init_ffmpeg)
        self.after(300, lambda: hook_file_drop(
            self, lambda files, x, y: self.events.put(("drop", files, x, y))))

    def _set_icon(self):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        imgs = []
        for size in (256, 64, 32):
            path = os.path.join(base, "assets", f"icon-{size}.png")
            if os.path.isfile(path):
                try:
                    imgs.append(tk.PhotoImage(file=path))
                except tk.TclError:
                    pass
        if imgs:
            self._icons = imgs
            self.iconphoto(True, *imgs)

    def _init_vars(self):
        self.fmt_var, self.q_var, self.enc_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.target_on = tk.BooleanVar(value=False)
        self.target_var = tk.StringVar()
        self.size_hint_var = tk.StringVar()
        self.copy_var = tk.BooleanVar(value=False)
        self.hw_var = tk.BooleanVar(value=bool(self.settings.get("hw", True)))
        self.hw_text = tk.StringVar(value="Use the graphics card (checking…)")
        self.low_var = tk.BooleanVar(value=bool(self.settings.get("low_priority", True)))
        self.dir_var, self.name_var = tk.StringVar(), tk.StringVar()
        self.ext_var = tk.StringVar(value=".mp4")
        self.summary_var = tk.StringVar()
        self.job_status = tk.StringVar()
        self.status_var = tk.StringVar()
        for v in (self.fmt_var, self.q_var, self.enc_var, self.target_on, self.target_var,
                  self.copy_var, self.hw_var):
            v.trace_add("write", lambda *_: self._schedule_summary())

    def _place_window(self, w, h):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(w, sw - 20), min(h, sh - 70)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")

    # ============================================================ welcome
    def _build_welcome(self):
        f = tk.Frame(self, bg=BG)
        wrap = tk.Frame(f, bg=BG)
        wrap.place(relx=0.5, rely=0.46, anchor="center")
        logo = tk.Canvas(wrap, width=76, height=76, bg=BG, highlightthickness=0)
        draw_logo(logo, 38, 38, 32)
        logo.pack()
        tk.Label(wrap, text=APP_NAME, bg=BG, fg=TEXT, font=font(24, "bold")).pack(pady=(16, 2))
        tk.Label(wrap, text="Cut, combine and shrink your videos — fast and light.",
                 bg=BG, fg=MUTED, font=font(11)).pack()
        self._drop = tk.Canvas(wrap, width=480, height=200, bg=BG, highlightthickness=0,
                               cursor="hand2")
        self._drop.pack(pady=(30, 0))
        self._draw_drop(False)
        btn = ttk.Button(self._drop, text="Choose a video", style="Hero.TButton",
                         command=self.open_video)
        self._drop.create_window(240, 128, window=btn)
        self._drop.bind("<Button-1>", lambda e: self.open_video())
        self._drop.bind("<Enter>", lambda e: self._draw_drop(True))
        self._drop.bind("<Leave>", lambda e: self._draw_drop(False))
        tk.Label(wrap, text="MP4 · MKV · MOV · AVI · WEBM · WMV · FLV · MPG · 3GP",
                 bg=BG, fg=FAINT, font=font(9)).pack(pady=(14, 0))
        self.w_status = tk.Label(wrap, text="Looking for FFmpeg…", bg=BG, fg=MUTED, font=font(9))
        self.w_status.pack(pady=(26, 0))
        self.w_ff_row = tk.Frame(wrap, bg=BG)
        ttk.Button(self.w_ff_row, text="Locate ffmpeg.exe…", style="Tool.TButton",
                   command=self._choose_ffmpeg).pack(side="left", padx=4)
        return f

    def _draw_drop(self, hover):
        c = self._drop
        c.delete("deco")
        rounded_rect(c, 3, 3, 477, 197, 22, fill="#181b22" if hover else BG,
                     outline=ACCENT if hover else BORDER, width=2, dash=(8, 6), tags="deco")
        c.create_text(240, 52, text="Choose the video you want to edit", fill=TEXT,
                      font=font(12), tags="deco")
        if IS_WIN:
            c.create_text(240, 76, text="or drop it here from a folder", fill=MUTED,
                          font=font(9), tags="deco")
        c.tag_lower("deco")

    # ============================================================= editor
    def _build_editor(self):
        ed = tk.Frame(self, bg=BG)
        hd = tk.Frame(ed, bg=PANEL, height=56)
        hd.pack(fill="x")
        hd.pack_propagate(False)
        lg = tk.Canvas(hd, width=30, height=30, bg=PANEL, highlightthickness=0)
        draw_logo(lg, 15, 15, 13)
        lg.pack(side="left", padx=(18, 10))
        tk.Label(hd, text=APP_NAME, bg=PANEL, fg=TEXT, font=font(12, "bold")).pack(side="left")
        tk.Frame(hd, bg=BORDER, width=1, height=24).pack(side="left", padx=18)
        self.h_file = tk.Label(hd, text="", bg=PANEL, fg=TEXT, font=font(10, "bold"))
        self.h_file.pack(side="left")
        self.h_meta = tk.Label(hd, text="", bg=PANEL, fg=MUTED, font=font(9))
        self.h_meta.pack(side="left", padx=(12, 0))
        self.gear_btn = ttk.Button(hd, text="⚙", style="Head.TButton", width=3,
                                   command=self._show_menu)
        self.gear_btn.pack(side="right", padx=(6, 16))
        ttk.Button(hd, text="Export video…", style="Play.TButton",
                   command=self.open_export).pack(side="right", padx=(6, 0))
        ttk.Button(hd, text="Open another video", style="Head.TButton",
                   command=self.open_video).pack(side="right")
        tk.Frame(ed, bg=BORDER, height=1).pack(fill="x")

        top = tk.Frame(ed, bg=BG)
        top.pack(fill="x", padx=16, pady=(14, 10))
        insp = tk.Frame(top, bg=PANEL, width=INSPECTOR_W, height=max(self.PH + 2, 340),
                        highlightthickness=1, highlightbackground=BORDER)
        insp.pack(side="right", fill="y")
        insp.pack_propagate(False)
        pvw = tk.Frame(top, bg=BG)
        pvw.pack(side="left", fill="both", expand=True, padx=(0, 16))
        pv = tk.Frame(pvw, bg="#000000", highlightthickness=1, highlightbackground=BORDER)
        pv.pack(expand=True)
        self.preview = tk.Canvas(pv, width=self.PW, height=self.PH, bg="#000000",
                                 highlightthickness=0)
        self.preview.pack()
        self.preview.bind("<Button-1>", lambda e: self.toggle_play())
        self._build_inspector(insp)

        tp = tk.Frame(ed, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        tp.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self._build_toolbar(tp)
        self.timeline = TimelineView(tp, self)
        self.timeline.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(tp, orient="horizontal", command=self.timeline.xview_cmd)
        sb.pack(fill="x")
        self.timeline.scrollbar = sb

        st = tk.Frame(ed, bg=BG)
        st.pack(fill="x", padx=18, pady=(0, 8))
        tk.Label(st, textvariable=self.status_var, bg=BG, fg=MUTED, font=font(9),
                 anchor="w").pack(side="left", fill="x", expand=True)
        tk.Label(st, text="Space play  ·  S split  ·  Del delete  ·  Ctrl+Z undo",
                 bg=BG, fg=FAINT, font=font(8)).pack(side="right")
        return ed

    def _build_toolbar(self, p):
        tb = tk.Frame(p, bg=PANEL)
        tb.pack(fill="x", padx=10, pady=8)
        self.play_btn = ttk.Button(tb, text="▶  Play", style="Play.TButton", width=9,
                                   command=self.toggle_play)
        self.play_btn.pack(side="left")
        Tooltip(self.play_btn, "Play the edited video from the playhead (Space)")
        self.time_lbl = tk.Label(tb, text="00:00.00", bg=PANEL, fg=TEXT, font=mono(12, "bold"))
        self.time_lbl.pack(side="left", padx=(12, 0))
        self.dur_lbl = tk.Label(tb, text="", bg=PANEL, fg=MUTED, font=mono(10))
        self.dur_lbl.pack(side="left")
        nav = tk.Frame(tb, bg=PANEL)
        nav.pack(side="left", padx=10)
        for txt, d, tip in (("|‹", "home", "Go to start (Home)"),
                            ("‹", "-f", "1 frame back (,)   ← = 1 second"),
                            ("›", "+f", "1 frame forward (.)   → = 1 second"),
                            ("›|", "end", "Go to end (End)")):
            bt = ttk.Button(nav, text=txt, style="Head.TButton", width=2,
                            command=lambda d=d: self.step(d))
            bt.pack(side="left", padx=1)
            Tooltip(bt, tip)
        tk.Frame(tb, bg=BORDER, width=1, height=24).pack(side="left", padx=8)
        sp = ttk.Button(tb, text="✂  Split", style="Tool.TButton", command=self.split)
        sp.pack(side="left")
        Tooltip(sp, "Split clips at the playhead or at the selected range (S)")
        hlp = tk.Canvas(tb, width=22, height=22, bg=PANEL, highlightthickness=0, cursor="hand2")
        hlp.create_oval(2, 2, 20, 20, outline=MUTED, width=1.5)
        hlp.create_text(11, 11, text="?", fill=TEXT, font=font(9, "bold"))
        hlp.pack(side="left", padx=(5, 8))
        Tooltip(hlp, SPLIT_HELP, delay=150, wrap=340)
        hlp.bind("<Button-1>", lambda e: messagebox.showinfo("How to split clips", SPLIT_HELP))
        dl = ttk.Button(tb, text="Delete", style="Tool.TButton", command=lambda: self.delete(True))
        dl.pack(side="left")
        Tooltip(dl, "Delete the selected clips and close the gap (Del).\n"
                    "Shift+Del leaves an empty gap instead.")
        self.undo_btn = ttk.Button(tb, text="↶", style="Head.TButton", width=2, command=self.undo)
        self.undo_btn.pack(side="left", padx=(8, 1))
        Tooltip(self.undo_btn, "Undo (Ctrl+Z)")
        self.redo_btn = ttk.Button(tb, text="↷", style="Head.TButton", width=2, command=self.redo)
        self.redo_btn.pack(side="left")
        Tooltip(self.redo_btn, "Redo (Ctrl+Y)")

        for txt, cmd, tip in (("+", lambda: self.timeline.zoom(1.5), "Zoom in (Ctrl+wheel)"),
                              ("Fit", lambda: self.timeline.fit(), "Show the whole timeline"),
                              ("−", lambda: self.timeline.zoom(1 / 1.5), "Zoom out")):
            bt = ttk.Button(tb, text=txt, style="Head.TButton", width=3 if txt == "Fit" else 2,
                            command=cmd)
            bt.pack(side="right", padx=1)
            Tooltip(bt, tip)
        tk.Frame(tb, bg=BORDER, width=1, height=24).pack(side="right", padx=8)
        self.add_btn = ttk.Button(tb, text="+  Add media  ▾", style="Tool.TButton",
                                  command=self._add_menu)
        self.add_btn.pack(side="right")
        Tooltip(self.add_btn, "Add another video on a new layer, join a video to the end, "
                              "or add music / sound on a new audio track")

    # ------------------------------------------------------------ inspector
    def _row_combo(self, parent, row, label, key, command):
        ttk.Label(parent, text=label, style="Field.TLabel").grid(row=row, column=0, sticky="w",
                                                                padx=(0, 10), pady=3)
        var = tk.StringVar()
        cb = ttk.Combobox(parent, textvariable=var, state="readonly", height=15)
        cb.grid(row=row, column=1, sticky="we", pady=3)
        cb.var = var
        cb.key = key
        var.trace_add("write", lambda *_: (None if self._guard else command()))
        return cb

    def _build_inspector(self, s):
        nb = ttk.Notebook(s)
        nb.pack(fill="both", expand=True)
        self.nb = nb
        s1, s2 = ScrollFrame(nb), ScrollFrame(nb)
        nb.add(s1, text="Clip")
        nb.add(s2, text="Project")
        t1, t2 = s1.inner, s2.inner
        self.clip_scroll = s1

        # -- clip: nothing selected
        self.clip_empty = ttk.Frame(t1)
        ttk.Label(self.clip_empty, text="No clip selected", style="H.TLabel").pack(anchor="w")
        ttk.Label(self.clip_empty, text="Click a clip on the timeline to change it.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 14))
        tip = tk.Frame(self.clip_empty, bg=CARD)
        tip.pack(fill="x")
        tk.Label(tip, text="How to edit only one part", bg=CARD, fg=TEXT, font=font(9, "bold"),
                 anchor="w").pack(fill="x", padx=12, pady=(10, 2))
        tk.Label(tip, text="1.  Drag on the range bar to select it  [ ]\n"
                           "2.  Press  S  to cut it out as its own clip\n"
                           "3.  Change effect, text, speed… here, or press Delete",
                 bg=CARD, fg=MUTED, font=font(9), justify="left", anchor="w",
                 wraplength=INSPECTOR_W - 60).pack(
            fill="x", padx=12, pady=(0, 10))

        # -- clip: something selected
        f = self.clip_form = ttk.Frame(t1)
        self.clip_title = ttk.Label(f, text="", style="H.TLabel")
        self.clip_title.pack(anchor="w")
        self.clip_sub = ttk.Label(f, text="", style="Muted.TLabel")
        self.clip_sub.pack(anchor="w", pady=(2, 8))
        g = ttk.Frame(f)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.speed_cb = self._row_combo(g, 0, "Speed", "speed", self._set_speed)
        self.vsec = ttk.Frame(f)
        self.vsec.columnconfigure(1, weight=1)
        self.fx_cb = self._row_combo(self.vsec, 0, "Effect", "fx", lambda: self._set_prop("fx"))
        ttk.Label(self.vsec, text="Strength", style="Field.TLabel").grid(row=1, column=0,
                                                                         sticky="w", pady=3)
        sr = ttk.Frame(self.vsec)
        sr.grid(row=1, column=1, sticky="we", pady=3)
        self.strength_var = tk.DoubleVar(value=5)
        self.strength_lbl = ttk.Label(sr, text="5", style="Field.TLabel", width=3, anchor="e")
        self.strength_lbl.pack(side="right")
        self.strength = ttk.Scale(sr, from_=1, to=10, variable=self.strength_var,
                                  command=lambda v: self.strength_lbl.configure(
                                      text=str(int(round(float(v))))))
        self.strength.pack(side="left", fill="x", expand=True)
        self.strength.bind("<ButtonRelease-1>", lambda e: self._set_prop("strength"))
        self.rot_cb = self._row_combo(self.vsec, 2, "Rotate", "rot", lambda: self._set_prop("rot"))
        self.lay_cb = self._row_combo(self.vsec, 3, "Layout", "lay", lambda: self._set_prop("lay"))
        ttk.Label(self.vsec, text="Text", style="Field.TLabel").grid(row=4, column=0, sticky="w",
                                                                     pady=3)
        tr = ttk.Frame(self.vsec)
        tr.grid(row=4, column=1, sticky="we", pady=3)
        self.text_btn = ttk.Button(tr, text="Add text…", style="Tool.TButton",
                                   command=self._edit_text)
        self.text_btn.pack(side="left", fill="x", expand=True)
        self.text_rm = ttk.Button(tr, text="Remove", style="Tool.TButton",
                                  command=self._remove_text)
        self.text_rm.pack(side="left", padx=(6, 0))
        self.asec = ttk.Frame(f)
        self.asec.columnconfigure(1, weight=1)
        self.vol_cb = self._row_combo(self.asec, 0, "Volume", "vol", lambda: self._set_prop("vol"))
        self.mute_var = tk.BooleanVar(value=False)
        self.mute_ck = ttk.Checkbutton(self.asec, text="Mute this clip", variable=self.mute_var,
                                       command=lambda: self._set_prop("mute"))
        self.mute_ck.grid(row=1, column=1, sticky="w", pady=3)
        bot = ttk.Frame(f)
        bot.pack(side="bottom", fill="x", pady=(10, 0))
        self.all_btn = ttk.Button(bot, text="Apply this look to all video clips",
                                  style="Tool.TButton", command=self._apply_look_all)
        self.all_btn.pack(fill="x")
        self.custom_btn = ttk.Button(bot, text="⚙  Custom FFmpeg filter…", style="Tool.TButton",
                                     command=self._edit_custom)
        self.custom_btn.pack(fill="x", pady=(6, 0))
        Tooltip(self.all_btn, "Copy this clip's effect, rotation and layout to every video clip")

        # -- project tab
        g = ttk.Frame(t2)
        g.pack(fill="x")
        g.columnconfigure(1, weight=1)
        self.res_cb = self._row_combo(g, 0, "Resolution", "res", self._on_project_setting)
        self.aspect_cb = self._row_combo(g, 1, "Aspect ratio", "aspect", self._on_project_setting)
        self.fps_cb = self._row_combo(g, 2, "Frame rate", "fps", self._on_project_setting)
        self.fade_cb = self._row_combo(g, 3, "Fade in / out", "fade", self._on_project_setting)
        self.norm_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="Even out loud and quiet sound", variable=self.norm_var,
                        command=self._on_project_setting).grid(row=4, column=1, sticky="w", pady=4)
        self.proj_info = ttk.Label(t2, text="", style="Muted.TLabel", wraplength=INSPECTOR_W - 60,
                                   justify="left")
        self.proj_info.pack(anchor="w", pady=(12, 0))
        ttk.Label(t2, text="Defaults come from your video, so you only change what you need. "
                           "Aspect ratio changes crop the picture from the center.",
                  style="Muted.TLabel", wraplength=INSPECTOR_W - 60, justify="left").pack(
            anchor="w", pady=(8, 0))

    # ================================================================ keys
    def _bind_keys(self):
        def guard(fn):
            def h(event):
                if self.editor is None or not self.project:
                    return None
                if isinstance(self.focus_get(), (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox)):
                    return None
                if self.export_dlg is not None and self.focus_get() is not None and \
                        str(self.focus_get()).startswith(str(self.export_dlg)):
                    return None
                fn(event)
                return "break"
            return h
        self.bind("<space>", guard(lambda e: self.toggle_play()))
        self.bind("<KeyPress-s>", guard(lambda e: self.split()))
        self.bind("<KeyPress-S>", guard(lambda e: self.split()))
        self.bind("<Delete>", guard(lambda e: self.delete(not (e.state & 1))))
        self.bind("<BackSpace>", guard(lambda e: self.delete(True)))
        self.bind("<Left>", guard(lambda e: self.step(-5 if e.state & 1 else -1)))
        self.bind("<Right>", guard(lambda e: self.step(5 if e.state & 1 else 1)))
        self.bind("<comma>", guard(lambda e: self.step("-f")))
        self.bind("<period>", guard(lambda e: self.step("+f")))
        self.bind("<Home>", guard(lambda e: self.step("home")))
        self.bind("<End>", guard(lambda e: self.step("end")))
        self.bind("<bracketleft>", guard(lambda e: self._range_edge("a")))
        self.bind("<bracketright>", guard(lambda e: self._range_edge("b")))
        self.bind("<Escape>", guard(lambda e: (self.clear_range(), self.select([]))))
        self.bind("<Control-z>", guard(lambda e: self.undo()))
        self.bind("<Control-y>", guard(lambda e: self.redo()))
        self.bind("<Control-Z>", guard(lambda e: self.redo()))
        self.bind("<plus>", guard(lambda e: self.timeline.zoom(1.5)))
        self.bind("<equal>", guard(lambda e: self.timeline.zoom(1.5)))
        self.bind("<minus>", guard(lambda e: self.timeline.zoom(1 / 1.5)))
        self.bind("<Control-o>", lambda e: self.open_video())
        self.bind("<Control-e>", lambda e: self.open_export())
        # the mouse wheel must never change a setting: it only scrolls the panel
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.unbind_class("TCombobox", seq)
            self.unbind_class("TScale", seq)
            self.unbind_class("Scale", seq)

    def _show_menu(self):
        m = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT,
                    activeforeground="#ffffff", bd=0, relief="flat", font=font(10))
        m.add_command(label="Keyboard shortcuts", command=self._show_shortcuts)
        m.add_command(label="Ask before adding dropped files",
                      command=lambda: (self.settings.update(ask_drop=True),
                                       save_settings(self.settings),
                                       self.status_var.set("You will be asked again before dropped "
                                                           "files are added.")))
        m.add_command(label="About", command=lambda: messagebox.showinfo(
            "About", f"{APP_NAME} {__version__}\nA light video editor powered by FFmpeg "
                     f"{self.ff.version_major if self.ff else ''} (included)."))
        b = self.gear_btn
        m.tk_popup(b.winfo_rootx() - 180, b.winfo_rooty() + b.winfo_height())

    def _show_shortcuts(self):
        messagebox.showinfo("Keyboard shortcuts",
                            "Space   play / stop\n"
                            "S   split at the playhead or at the range\n"
                            "Delete   delete selected clips (Shift+Delete leaves a gap)\n"
                            "Ctrl+Z / Ctrl+Y   undo / redo\n"
                            "← / →   1 second (Shift: 5 seconds)\n"
                            ",  /  .   one frame\n"
                            "[  /  ]   start / end of the range at the playhead\n"
                            "Esc   clear range and selection\n"
                            "Ctrl+wheel, + / −   zoom the timeline\n"
                            "Click   select only that clip (its video or its sound)\n"
                            "Alt+drag   move a clip without its linked video/sound\n"
                            "Drag a clip up/down   move it to another track\n"
                            "Drag a track name (⋮⋮ V1) up/down   change the track order\n"
                            "Ctrl+click   select several clips\n"
                            "Ctrl+E   export")

    # ========================================================= ffmpeg init
    def _init_ffmpeg(self):
        path = find_ffmpeg(self.settings.get("ffmpeg_path"))
        if path:
            self._set_ffmpeg(path)
        else:
            self.w_status.configure(text=FFMPEG_MISSING, fg=DANGER)
            self.w_ff_row.pack(pady=(10, 0))

    def _choose_ffmpeg(self):
        types = [("ffmpeg", "ffmpeg.exe"), ("All files", "*.*")] if IS_WIN else [("All files", "*")]
        path = filedialog.askopenfilename(title="Select ffmpeg.exe", filetypes=types)
        if not path:
            return
        if find_ffmpeg(path) != path:
            messagebox.showerror("Error", "The selected file is not a working FFmpeg.")
            return
        self.settings["ffmpeg_path"] = path
        save_settings(self.settings)
        self._set_ffmpeg(path)

    def _set_ffmpeg(self, path):
        self.ff = FFmpeg(path)
        if self.ff.version_major:
            _builder.FFMPEG_MAJOR = self.ff.version_major
        self.player = Player(self.ff.ffmpeg, self.ff.ffplay)
        self.w_status.configure(text="✓  FFmpeg is ready", fg=OK)
        self.w_ff_row.pack_forget()
        ff = self.ff
        threading.Thread(target=lambda: self.events.put(("hw", ff.detect_hw())),
                         daemon=True).start()
        if self._initial_file:
            f, self._initial_file = self._initial_file, None
            self._load_video(f)

    # ============================================================ loading
    def open_video(self):
        if self.job or self._loading:
            return
        if not self.ff:
            if messagebox.askyesno("FFmpeg missing", FFMPEG_MISSING +
                                   "\n\nDo you want to locate ffmpeg.exe yourself?"):
                self._choose_ffmpeg()
            if not self.ff:
                return
        path = filedialog.askopenfilename(
            title="Choose a video", filetypes=VIDEO_TYPES,
            initialdir=self.settings.get("last_open_dir") or os.path.expanduser("~"))
        if path:
            self._load_video(path)

    def _load_video(self, path):
        if self.project and self.undo_stack and not messagebox.askyesno(
                "Open another video", "Your changes to the current video will be lost. Continue?"):
            return
        self.stop_play()
        self._loading = True
        if self.editor is None:
            self.w_status.configure(text="Reading the video…", fg=MUTED)
        else:
            self.status_var.set("Reading the video…")
        self.config(cursor="watch")
        ff = self.ff

        def work():
            try:
                self.events.put(("probe", path, ff.probe(path), None))
            except ProbeError as e:
                self.events.put(("probe", path, None, str(e)))
            except Exception as e:  # noqa: BLE001
                self.events.put(("probe", path, None, f"Could not open the file: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def _on_probe(self, path, info, err):
        self._loading = False
        self.config(cursor="")
        if err:
            if self.editor is None:
                self.w_status.configure(text="✓  FFmpeg is ready", fg=OK)
            messagebox.showerror("This file can't be opened", err)
            return
        first = self.editor is None
        if first:
            self.editor = self._build_editor()
        p = Project()
        p.media[path] = info
        p.main_path = path
        v1, a1 = p.add_track("video"), p.add_track("audio")
        p.add_media_clips(path, v1, a1, 0.0)
        self.project = p
        self.selected, self.range = set(), None
        self.undo_stack, self.redo_stack = [], []
        self.cur_time = 0.0
        self.settings["last_open_dir"] = os.path.dirname(path)
        self.h_file.configure(text=os.path.basename(path))
        mb = info.size_bytes / (1024 * 1024)
        meta = [fmt_time(info.duration, 1), f"{info.width}×{info.height}", f"{info.fps:.3g} fps",
                f"{mb:.1f} MB", info.vcodec.upper()] + ([] if info.has_audio else ["no audio"])
        self.h_meta.configure(text="  ·  ".join(meta))
        self._populate(info)
        if first:
            self.welcome.pack_forget()
            self.editor.pack(fill="both", expand=True)
        self._fit_window()
        self._ensure_cache(path)
        self.after(50, self.timeline.fit)
        self.after(120, self._fit_window)
        self.status_var.set("Ready. Press Space to play. Select a part with [ ] on the range bar "
                            "and press S to cut it out.")
        self.changed()
        self.seek(0)

    def _fit_window(self):
        self.timeline.configure(height=self.timeline.needed_height())
        self.update_idletasks()
        rw, rh = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        cw, ch = self.winfo_width(), self.winfo_height()
        if rh > sh - 70 or rw > sw - 20:
            try:
                self.state("zoomed")
                return
            except tk.TclError:
                pass
        if ch < rh or cw < rw or cw < 1100:
            self._place_window(max(rw + 20, 1240), max(rh, ch))
        self.minsize(min(rw, 1000), min(rh, 640))

    def _populate(self, info):
        self._guard = True
        w, h = info.width, info.height
        short = min(w, h)
        res = [(f"Original  ·  {w}×{h}", 1.0)]
        res += [(f"{s}p", s / short) for s in STANDARD_SHORTS if s < short]
        self._set_choices(self.res_cb, res)
        asp = [(f"Original  ·  {ratio_name(w, h)}", None)]
        asp += [(l, r) for l, r in ASPECT_CHOICES if abs(w / h - r[0] / r[1]) > 0.02]
        self._set_choices(self.aspect_cb, asp)
        fps = [(f"Original  ·  {info.fps:.3g} fps", None)]
        fps += [(l, v) for l, v in FPS_CHOICES if v < info.fps - 0.5]
        self._set_choices(self.fps_cb, fps)
        self._set_choices(self.fade_cb, FADES)
        self.norm_var.set(False)
        slow = [c for c in SPEED_CHOICES if c[1] < 1]
        fast = [c for c in SPEED_CHOICES if c[1] > 1]
        self._set_choices(self.speed_cb, slow + [("1x  ·  normal", 1.0)] + fast, len(slow))
        self._set_choices(self.fx_cb, [("None", None)] +
                          [(label, k) for k, label, _ in FILTER_PRESETS])
        self._set_choices(self.rot_cb, [("None", None)] + ROTATE_CHOICES)
        self._set_choices(self.lay_cb, LAYOUT_CHOICES)
        lo = [c for c in VOLUME_CHOICES if c[1] < 1]
        hi = [c for c in VOLUME_CHOICES if c[1] > 1]
        self._set_choices(self.vol_cb, lo + [("100%  ·  original", 1.0)] + hi, len(lo))

        fmts = [f for f in FORMATS]
        self.maps["fmt"] = dict(fmts)
        self.fmt_var.set(fmts[1 if info.vcodec.lower() in ("hevc", "h265") else 0][0])
        self.maps["q"] = dict(QUALITIES)
        self.q_var.set(QUALITIES[0][0])
        self.maps["enc"] = dict(ENC_SPEEDS)
        key = self.settings.get("enc_speed", "fast")
        self.enc_var.set(next((l for l, k in ENC_SPEEDS if k == key), ENC_SPEEDS[1][0]))
        self.target_on.set(False)
        self.target_var.set(f"{max(1, round(info.size_bytes / 1048576 / 2)):g}")
        self.size_hint_var.set(f"The original file is {info.size_bytes / 1048576:.1f} MB")
        self.copy_var.set(False)
        folder = os.path.dirname(info.path)
        self.dir_var.set(folder)
        self.ext_var.set(output_extension(self.output_settings(silent=True), info.path))
        self.name_var.set(self._unique_name(folder, self.ext_var.get()))
        self._guard = False
        self._update_project_info()

    def _set_choices(self, cb, pairs, default=0):
        self.maps[cb.key] = dict(pairs)
        cb["values"] = [l for l, _ in pairs]
        cb.var.set(pairs[default][0])

    def _val(self, cb):
        return self.maps.get(cb.key, {}).get(cb.var.get())

    def _label_for(self, cb, value):
        for l, v in self.maps.get(cb.key, {}).items():
            if v == value or (isinstance(v, float) and isinstance(value, (int, float))
                              and abs(v - value) < 1e-6):
                return l
        return None

    # ============================================================ caches
    def _ensure_cache(self, path):
        if path in self._cache_started or not self.ff:
            return
        self._cache_started.add(path)
        info, ff = self.project.media[path], self.ff

        def thumbs():
            n = max(8, min(60, int(info.duration / 2) + 1))
            self.events.put(("thumbinit", path, n))
            for i in range(n):
                t = (i + 0.5) * info.duration / n
                try:
                    data = ff.grab_frame(path, t, THUMB_W, THUMB_H, fill=True, fast=True)
                except Exception:  # noqa: BLE001
                    data = None
                self.events.put(("thumb", path, i, data))

        def waves():
            self.events.put(("wave", path, ff.waveform(path, WAVE_PER_SEC)))
        if getattr(info, "has_video", True):
            threading.Thread(target=thumbs, daemon=True).start()
        if info.has_audio:
            threading.Thread(target=waves, daemon=True).start()

    # ============================================================== state
    def project_settings(self) -> ProjectSettings:
        info = self.project.media[self.project.main_path]
        w, h = float(info.width), float(info.height)
        ar = self._val(self.aspect_cb)
        if ar:
            r = ar[0] / ar[1]
            if w / h > r:
                w = h * r
            else:
                h = w / r
        f = self._val(self.res_cb) or 1.0
        fade = self._val(self.fade_cb) or 0.0
        return ProjectSettings(_even(w * f), _even(h * f), self._val(self.fps_cb) or info.fps,
                               fade, fade, self.norm_var.get())

    def _update_project_info(self):
        if not self.project:
            return
        ps = self.project_settings()
        self.proj_info.configure(text=f"Output frame: {ps.width}×{ps.height}  ·  {ps.fps:.3g} fps")

    def _on_project_setting(self):
        if self._guard or not self.project:
            return
        self._update_project_info()
        self.stop_play()
        self.request_frame()
        self._schedule_summary()

    def select(self, clips):
        self.selected = {c.id for c in clips if c is not None}
        if self.editor:
            self.timeline.redraw()
            self._refresh_inspector()

    def selected_clips(self):
        return [c for c in (self.project.clip(i) for i in self.selected) if c] if self.project else []

    def push_undo(self, snap=None):
        self.undo_stack.append(snap if snap is not None else self.project.snapshot())
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()

    def commit_edit(self, snap, msg=None):
        self.push_undo(snap)
        if msg:
            self.status_var.set(msg)
        self.changed()

    def changed(self):
        if not self.project:
            return
        self.project.prune_empty_tracks()
        self.selected = {i for i in self.selected if self.project.clip(i)}
        nh = self.timeline.needed_height()
        if int(self.timeline.cget("height")) != nh:
            self.timeline.configure(height=nh)
            self.after(10, self._fit_window)
        self.timeline.redraw()
        self._refresh_inspector()
        self.undo_btn.state(["!disabled"] if self.undo_stack else ["disabled"])
        self.redo_btn.state(["!disabled"] if self.redo_stack else ["disabled"])
        self.dur_lbl.configure(text=f"  /  {fmt_time(self.project.end, 2)}")
        self.request_frame()
        self._schedule_summary()

    def on_live_edit(self):
        self.dur_lbl.configure(text=f"  /  {fmt_time(self.project.end, 2)}")

    def undo(self):
        if not self.undo_stack:
            self.status_var.set("Nothing to undo.")
            return
        self.stop_play()
        self.redo_stack.append(self.project.snapshot())
        self.project.restore(self.undo_stack.pop())
        self.status_var.set("Undone.")
        self.changed()

    def redo(self):
        if not self.redo_stack:
            return
        self.stop_play()
        self.undo_stack.append(self.project.snapshot())
        self.project.restore(self.redo_stack.pop())
        self.status_var.set("Redone.")
        self.changed()

    # ========================================================= navigation
    def seek(self, t, live=False):
        if not self.project:
            return
        if self.playing:
            self.stop_play()
        self.cur_time = max(0.0, min(float(t), max(self.project.end, 0.0)))
        self.time_lbl.configure(text=fmt_time(self.cur_time, 2))
        self.timeline.set_playhead()
        self.request_frame()

    def step(self, d):
        if not self.project:
            return
        fps = self.project_settings().fps
        if d == "home":
            t = 0.0
        elif d == "end":
            t = self.project.end
        elif d == "-f":
            t = self.cur_time - 1.0 / fps
        elif d == "+f":
            t = self.cur_time + 1.0 / fps
        else:
            t = self.cur_time + d
        self.seek(t)
        self.timeline.ensure_visible(self.cur_time)

    # ============================================================ range
    def set_range(self, a, b):
        self.range = (max(0.0, a), max(a + MIN_CLIP, b))
        self.timeline.redraw()

    def clear_range(self):
        if self.range:
            self.range = None
            self.timeline.redraw()

    def on_range_done(self):
        if self.range:
            a, b = self.range
            self.status_var.set(f"Range {fmt_time(a, 2)} → {fmt_time(b, 2)} "
                                f"({fmt_time(b - a, 1)}).  Press S to cut it out as its own clip.")

    def _range_edge(self, which):
        t = self.cur_time
        if self.range:
            a, b = self.range
        else:
            a, b = (t, min(self.project.end, t + 2.0)) if which == "a" else (max(0.0, t - 2.0), t)
        if which == "a":
            a = min(t, b - MIN_CLIP)
        else:
            b = max(t, a + MIN_CLIP)
        self.set_range(a, b)
        self.on_range_done()

    # ============================================================ editing
    def split(self):
        if not self.project:
            return
        self.stop_play()
        p = self.project
        sel = self.selected_clips()
        if sel:
            targets = list({c.id: c for c in sel + [p.linked(c) for c in sel if p.linked(c)]}.values())
        else:
            targets = p.all_clips()
        points = list(self.range) if self.range else [self.cur_time]
        snap = p.snapshot()
        cur = list(targets)
        made = 0
        for t in points:
            rights = p.split([c for c in cur if c.start < t < c.end], t)
            made += len(rights)
            cur += rights
        if not made:
            self.status_var.set("Nothing to split here. Put the playhead (or the range) over a clip.")
            return
        if self.range:
            a, b = self.range
            inside = [c for c in cur if c.start >= a - 1e-3 and c.end <= b + 1e-3]
            self.range = None
            self.push_undo(snap)
            self.selected = {c.id for c in inside}
            self.status_var.set("The range is now its own clip and is selected. Press Delete to "
                                "remove it, or change it in the Clip panel.")
        else:
            self.push_undo(snap)
            self.status_var.set("Clip split at the playhead.")
        self.changed()

    def delete(self, ripple=True):
        if not self.project:
            return
        self.stop_play()
        p = self.project
        sel = self.selected_clips()
        snap = p.snapshot()
        if not sel and self.range:
            a, b = self.range
            cur = p.all_clips()
            for t in (a, b):
                cur += p.split([c for c in cur if c.start < t < c.end], t)
            sel = [c for c in p.all_clips() if c.start >= a - 1e-3 and c.end <= b + 1e-3]
            self.range = None
        if not sel:
            self.status_var.set("Select a clip (or a range) first, then press Delete.")
            return
        p.delete(sel, ripple)
        self.selected = set()
        self.push_undo(snap)
        self.status_var.set("Deleted." + ("" if ripple else " The gap was kept.") +
                            "  Ctrl+Z to undo.")
        self.cur_time = min(self.cur_time, p.end)
        self.changed()

    def unlink(self, clip):
        snap = self.project.snapshot()
        self.project.unlink(clip)
        self.commit_edit(snap, "Video and sound are now separate clips.")

    def toggle_track(self, tr):
        snap = self.project.snapshot()
        tr.hidden = not tr.hidden
        name = self.project.track_name(tr)
        self.commit_edit(snap, f"{name} {'hidden' if tr.hidden and tr.kind == 'video' else 'muted' if tr.hidden else 'on'}.")

    def swap_tracks(self, a, b):
        snap = self.project.snapshot()
        self.project.swap_tracks(a, b)
        self.commit_edit(snap, f"Track moved — it is now {self.project.track_name(a)}.")

    def remove_track(self, tr):
        name = self.project.track_name(tr)
        if tr.clips and not messagebox.askyesno("Remove track",
                                                f"Remove track {name} and its clips?"):
            return
        snap = self.project.snapshot()
        self.project.remove_track(tr)
        self.commit_edit(snap, f"Track {name} removed.")

    # ---------------------------------------------------------- add media
    def _add_menu(self):
        if not self.project:
            return
        m = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=ACCENT,
                    activeforeground="#ffffff", bd=0, relief="flat", font=font(10))
        m.add_command(label="Video on a new layer (at the playhead)…",
                      command=lambda: self._import("overlay"))
        m.add_command(label="Video joined to the end…", command=lambda: self._import("append"))
        m.add_separator()
        m.add_command(label="Music / sound on a new audio track (at the playhead)…",
                      command=lambda: self._import("audio"))
        b = self.add_btn
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _free_track(self, kind, start, dur, prefer=None):
        p = self.project
        if prefer is not None and prefer.kind == kind and p.is_free(prefer, start, start + dur):
            return prefer
        lst = p.video_tracks() if kind == "video" else p.audio_tracks()
        for tr in lst[1:]:
            if p.is_free(tr, start, start + dur):
                return tr
        return p.add_track(kind)

    def _paired_audio(self, vtrack):
        p = self.project
        i = p.video_tracks().index(vtrack)
        auds = p.audio_tracks()
        return auds[i] if i < len(auds) else None

    # ------------------------------------------------------- drag & drop
    def _on_drop(self, files, sx, sy):
        files = [f for f in files if os.path.isfile(f)]
        if not files or self._loading:
            return
        if self.job:
            self.status_var.set("Please wait until the export is finished.")
            return
        if not self.ff:
            messagebox.showerror("FFmpeg missing", FFMPEG_MISSING)
            return
        if self.project is None or self.editor is None:
            self._load_video(files[0])
            return
        self.stop_play()
        track, t = None, self.cur_time
        try:
            w = self.winfo_containing(sx, sy)
        except (KeyError, tk.TclError):
            w = None
        if w is self.timeline:
            x, y = sx - self.timeline.winfo_rootx(), sy - self.timeline.winfo_rooty()
            if x > HEAD_W:
                t = self.timeline.t_of(x)
            r = self.timeline.row_at(y)
            track = r[0] if r else None
        items, bad = [], []
        self.config(cursor="watch")
        self.update_idletasks()
        for f in files:
            try:
                items.append((f, self.ff.probe(f, require_video=False)))
            except ProbeError:
                bad.append(os.path.basename(f))
        self.config(cursor="")
        if not items:
            messagebox.showerror("Can't add", "These files are not video or audio files:\n" +
                                 "\n".join(bad))
            return
        names = ", ".join(f"“{os.path.basename(p)}”" for p, _ in items[:3])
        if len(items) > 3:
            names += f" and {len(items) - 3} more"
        where = self.project.track_name(track) if track else "a new track"
        text = f"Add {names} to {where} at {fmt_time(t, 1)}?"
        if track is not None and track.kind == "audio" and any(i.has_video for _, i in items):
            text += "\n\nOn an audio track only the sound of a video is used."
        if bad:
            text += "\n\nSkipped (not video or audio): " + ", ".join(bad)
        if self.settings.get("ask_drop", True):
            res = ask_form(self, "Add to the timeline", [
                {"key": "never", "label": "Don't ask me again", "type": "bool", "default": False}],
                text, ok_text="Add")
            if res is None:
                self.status_var.set("Nothing was added.")
                return
            if res["never"]:
                self.settings["ask_drop"] = False
                save_settings(self.settings)
        self._place_files(items, track, t)

    def _place_files(self, items, track, t):
        p = self.project
        snap = p.snapshot()
        cur, new, notes = t, [], []
        ps = self.project_settings()
        for path, info in items:
            p.media[path] = info
            dur = info.duration
            v = a = None
            if getattr(info, "has_video", True) and not (track is not None and track.kind == "audio"):
                vt = self._free_track("video", cur, dur,
                                      prefer=track if track is not None else None)
                if vt is None:
                    notes.append("You can use up to 3 video tracks.")
                    break
                at = None
                if info.has_audio:
                    at = self._free_track("audio", cur, dur, prefer=self._paired_audio(vt))
                v, a = p.add_media_clips(path, vt, at, cur)
                if v and vt is p.video_tracks()[0] and \
                        abs(info.width / info.height - ps.width / ps.height) > 0.02:
                    v.layout = "fit"
            else:
                if not info.has_audio:
                    notes.append(f"{os.path.basename(path)} has no sound.")
                    continue
                at = self._free_track("audio", cur, dur, prefer=track)
                if at is None:
                    notes.append("You can use up to 3 audio tracks.")
                    break
                v, a = p.add_media_clips(path, None, at, cur)
            new += [c for c in (v, a) if c]
            self._ensure_cache(path)
            cur += dur
        if not new:
            p.restore(snap)
            messagebox.showinfo("Nothing added", "\n".join(notes) or "Nothing could be added.")
            return
        names = sorted({p.track_name(p.track_of(c)) for c in new})
        self.selected = {c.id for c in new}
        self.commit_edit(snap, f"Added to {', '.join(names)}.  " + " ".join(notes))
        self.after(30, lambda: self.timeline.ensure_visible(t))

    def _import(self, how):
        self.stop_play()
        audio = how == "audio"
        path = filedialog.askopenfilename(
            title="Choose music or a sound file" if audio else "Choose a video",
            filetypes=AUDIO_TYPES if audio else VIDEO_TYPES,
            initialdir=self.settings.get("last_open_dir") or os.path.expanduser("~"))
        if not path:
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            info = self.ff.probe(path, require_video=not audio)
        except ProbeError as e:
            self.config(cursor="")
            messagebox.showerror("This file can't be added", str(e))
            return
        finally:
            self.config(cursor="")
        if audio and not info.has_audio:
            messagebox.showerror("No sound", "This file has no sound.")
            return
        p = self.project
        snap = p.snapshot()
        p.media[path] = info
        t = self.cur_time
        if how == "overlay":
            vt = self._free_track("video", t, info.duration)
            at = self._free_track("audio", t, info.duration) if info.has_audio else None
            if vt is None or (info.has_audio and at is None):
                p.restore(snap)
                messagebox.showinfo("Too many tracks", "You can use up to 3 video and 3 audio "
                                                       "tracks. Remove a track first.")
                return
            v, a = p.add_media_clips(path, vt, at, t)
            msg = f"Added on {p.track_name(vt)}. Use the Clip panel → Layout to make it small."
        elif how == "append":
            v1, a1 = p.video_tracks()[0], p.audio_tracks()[0]
            start = max(p.track_end(v1), p.track_end(a1))
            v, a = p.add_media_clips(path, v1, a1, start)
            if v and abs(info.width / info.height - self.project_settings().width /
                         self.project_settings().height) > 0.02:
                v.layout = "fit"
            msg = "Video joined to the end."
        else:
            at = self._free_track("audio", t, info.duration)
            if at is None:
                p.restore(snap)
                messagebox.showinfo("Too many tracks", "You can use up to 3 audio tracks. "
                                                       "Remove a track first.")
                return
            v, a = p.add_media_clips(path, None, at, t)
            msg = f"Sound added on {p.track_name(at)}. Select it to change its volume."
        self._ensure_cache(path)
        self.selected = {c.id for c in (v, a) if c}
        self.commit_edit(snap, msg)
        self.after(30, lambda: self.timeline.ensure_visible(t))

    # ------------------------------------------------------------- inspector
    def _refresh_inspector(self):
        if not self.editor:
            return
        sel = self.selected_clips()
        if not sel:
            self.clip_form.pack_forget()
            self.clip_empty.pack(fill="both", expand=True)
            return
        self.clip_empty.pack_forget()
        self.clip_form.pack(fill="both", expand=True)
        vids = [c for c in sel if c.kind == "video"]
        auds = [c for c in sel if c.kind == "audio"]
        first = sel[0]
        if len({c.link for c in sel} | {c.id for c in sel}) <= 2 and len(sel) <= 2:
            kind = " + ".join(k for k, have in (("Video", vids), ("Sound", auds)) if have)
            self.clip_title.configure(text=f"{kind} clip")
            self.clip_sub.configure(text=f"{fmt_time(first.start, 2)} → {fmt_time(first.end, 2)}"
                                         f"   ({fmt_time(first.duration, 1)})")
        else:
            self.clip_title.configure(text=f"{len(sel)} clips selected")
            self.clip_sub.configure(text="Changes apply to all of them.")
        self._guard = True
        self.speed_cb.var.set(self._label_for(self.speed_cb, first.speed) or "1x  ·  normal")
        if vids:
            v = vids[0]
            self.vsec.pack(fill="x", pady=(4, 0))
            self.fx_cb.var.set(self._label_for(self.fx_cb, v.effect) or "None")
            self.strength_var.set(v.strength)
            self.strength_lbl.configure(text=str(v.strength))
            self.strength.state(["!disabled"] if v.effect and FILTER_HAS_STRENGTH.get(v.effect)
                                else ["disabled"])
            self.rot_cb.var.set(self._label_for(self.rot_cb, v.rotate) or "None")
            self.lay_cb.var.set(self._label_for(self.lay_cb, v.layout) or LAYOUT_CHOICES[0][0])
            self.text_btn.configure(text="Edit text…" if v.text else "Add text…")
            self.text_rm.state(["!disabled"] if any(c.text for c in vids) else ["disabled"])
        else:
            self.vsec.pack_forget()
        if auds:
            a = auds[0]
            self.asec.pack(fill="x", pady=(4, 0))
            self.vol_cb.var.set(self._label_for(self.vol_cb, a.volume) or "100%  ·  original")
            self.mute_var.set(a.muted)
        else:
            self.asec.pack_forget()
        self.all_btn.state(["!disabled"] if vids else ["disabled"])
        self._guard = False

    def _set_prop(self, which):
        if self._guard or not self.project:
            return
        sel = self.selected_clips()
        vids = [c for c in sel if c.kind == "video"]
        auds = [c for c in sel if c.kind == "audio"]
        snap = self.project.snapshot()
        if which == "fx":
            for c in vids:
                c.effect = self._val(self.fx_cb)
        elif which == "strength":
            for c in vids:
                c.strength = int(round(self.strength_var.get()))
        elif which == "rot":
            for c in vids:
                c.rotate = self._val(self.rot_cb)
        elif which == "lay":
            for c in vids:
                c.layout = self._val(self.lay_cb) or "fill"
        elif which == "vol":
            for c in auds:
                c.volume = self._val(self.vol_cb) or 1.0
        elif which == "mute":
            for c in auds:
                c.muted = self.mute_var.get()
        self.stop_play()
        self.commit_edit(snap)

    def _set_speed(self):
        if self._guard or not self.project:
            return
        p = self.project
        sp = self._val(self.speed_cb) or 1.0
        targets = self.selected_clips()
        snap = p.snapshot()
        for c in sorted(targets, key=lambda c: c.start, reverse=True):
            tr = p.track_of(c)
            old_end = c.end
            c.speed = sp
            delta = c.end - old_end
            for o in tr.clips:      # keep the clips after it attached (ripple)
                if o is not c and o.start >= old_end - EPS:
                    o.start += delta
        self.stop_play()
        self.commit_edit(snap, f"Speed set to {sp:g}x.")

    def _edit_text(self):
        vids = [c for c in self.selected_clips() if c.kind == "video"]
        if not vids:
            return
        cur = vids[0].text or {}
        res = ask_form(self, "Text on the clip", [
            {"key": "text", "label": "Text", "type": "multiline", "default": cur.get("text", "")},
            {"key": "position", "label": "Position", "type": "choice",
             "choices": list(TEXT_POSITIONS), "default": cur.get("position", "Bottom center")},
            {"key": "size", "label": "Size", "type": "choice", "choices": list(TEXT_SIZES),
             "default": cur.get("size", "Medium")},
            {"key": "color", "label": "Color", "type": "choice", "choices": list(TEXT_COLORS),
             "default": cur.get("color", "White")},
            {"key": "box", "label": "Dark box behind the text (easier to read)", "type": "bool",
             "default": cur.get("box", True)},
        ], "The text is shown while this clip plays.",
            lambda r: None if r["text"].strip() else "The text can't be empty.")
        if res:
            res["text"] = res["text"].strip()
            snap = self.project.snapshot()
            for c in vids:
                c.text = dict(res)
            self.commit_edit(snap, "Text added.")

    def _remove_text(self):
        snap = self.project.snapshot()
        for c in self.selected_clips():
            if c.kind == "video":
                c.text = None
        self.commit_edit(snap, "Text removed.")

    def _edit_custom(self):
        sel = self.selected_clips()
        if not sel:
            return
        intro = ("For people who know FFmpeg filters. One filter or a comma-separated chain, "
                 "applied to the selected clips.\n"
                 "Video examples:  hue=h=90   edgedetect   colorbalance=rs=0.3\n"
                 "Sound examples:  highpass=f=200   aecho=0.8:0.9:500:0.3   bass=g=8\n"
                 "Leave empty to remove.")
        vids = [c for c in sel if c.kind == "video"]
        auds = [c for c in sel if c.kind == "audio"]
        choices = (["Video"] if vids else []) + (["Sound"] if auds else [])

        def validate(r):
            expr = r["expr"].strip()
            if not expr:
                return None
            if any(ch in expr for ch in "[];"):
                return "Labels ([..]) and ';' are not supported here."
            self.config(cursor="watch")
            self.update_idletasks()
            err = self.ff.test_filter(expr, audio=r["target"] == "Sound")
            self.config(cursor="")
            return ("This filter does not work:\n\n" + err) if err else None

        cur = (vids or auds)[0].custom
        res = ask_form(self, "Custom FFmpeg filter", [
            {"key": "expr", "label": "Filter", "type": "text", "default": cur},
            {"key": "target", "label": "Apply to", "type": "choice", "choices": choices},
        ], intro, validate)
        if res is None:
            return
        snap = self.project.snapshot()
        for c in (vids if res["target"] == "Video" else auds):
            c.custom = res["expr"].strip()
        self.commit_edit(snap, "Custom filter updated.")

    def _apply_look_all(self):
        vids = [c for c in self.selected_clips() if c.kind == "video"]
        if not vids:
            return
        src = vids[0]
        snap = self.project.snapshot()
        for c in self.project.all_clips():
            if c.kind == "video":
                c.effect, c.strength, c.rotate, c.layout = src.effect, src.strength, src.rotate, src.layout
        self.commit_edit(snap, "Look applied to every video clip.")

    # ============================================================ preview
    def request_frame(self):
        if not self.project or self.editor is None or self.playing:
            return
        if self._frame_after:
            self.after_cancel(self._frame_after)
        self._frame_after = self.after(50, self._start_frame)

    def _start_frame(self):
        self._frame_after = None
        if not self.project or not self.ff or self.playing:
            return
        if self._frame_busy:
            self._frame_pending = True
            return
        built = build_frame(self.project, self.project_settings(), self.cur_time, self.PW, self.PH)
        if not built:
            self._show_empty()
            return
        args, files = built
        try:
            write_files(self.work_dir, files)
        except OSError:
            pass
        self._frame_busy = True
        ff, cwd = self.ff, self.work_dir

        def work():
            try:
                r = subprocess.run([ff.ffmpeg] + args, stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=30, cwd=cwd,
                                   **popen_kwargs())
                data = r.stdout if r.returncode == 0 else None
            except Exception:  # noqa: BLE001
                data = None
            self.events.put(("frame", data))
        threading.Thread(target=work, daemon=True).start()

    def _on_frame(self, data):
        self._frame_busy = False
        if data and not self.playing:
            try:
                self._photo = tk.PhotoImage(data=base64.b64encode(data))
                self._draw_preview(self._photo)
            except tk.TclError:
                pass
        if self._frame_pending:
            self._frame_pending = False
            self._start_frame()

    def _draw_preview(self, photo):
        c = self.preview
        c.delete("all")
        c.create_image(self.PW // 2, self.PH // 2, image=photo)
        if not self.playing and self.project and self.project.end > 0:
            c.create_text(self.PW - 12, self.PH - 10, anchor="se", text="▶  Space",
                          fill="#ffffff", font=font(8))

    def _show_empty(self):
        c = self.preview
        c.delete("all")
        c.create_text(self.PW // 2, self.PH // 2, fill=FAINT, font=font(11),
                      text="The timeline is empty — press Ctrl+Z to undo, or add media.")

    # ========================================================== playback
    def toggle_play(self):
        if self.playing:
            self.stop_play()
            return
        if not self.project or not self.player or self.project.end < 0.1:
            return
        t0 = self.cur_time if self.cur_time < self.project.end - 0.05 else 0.0
        built = build_play(self.project, self.project_settings(), t0, self.PW, self.PH)
        if not built:
            return
        vargs, aargs, w, h, fps, _dur, files = built
        try:
            write_files(self.work_dir, files)
        except OSError:
            pass
        self.player.start(vargs, aargs, w, h, fps, t0, self.work_dir)
        self.playing = True
        self._last_frame = -1
        self.play_btn.configure(text="■  Stop")
        note = "" if self.ff.ffplay or not aargs else "  (no sound: ffplay.exe was not found " \
                                                      "next to ffmpeg.exe)"
        self.status_var.set("Playing the edited video…  Space to stop." + note)
        self.after(15, self._play_tick)

    def _play_tick(self):
        if not self.playing:
            return
        p = self.player
        with p.lock:
            no, data, t = p.frame_no, p.frame, p.time
        if no != self._last_frame and data:
            self._last_frame = no
            try:
                if self._play_photo is None or self._play_photo.width() != p.w or \
                        self._play_photo.height() != p.h:
                    self._play_photo = tk.PhotoImage(width=p.w, height=p.h)
                self._play_photo.configure(data=data)
                self._draw_preview(self._play_photo)
            except tk.TclError:
                pass
            self.cur_time = t
            self.time_lbl.configure(text=fmt_time(t, 2))
            self.timeline.ensure_visible(t)
            self.timeline.set_playhead()
        if p.finished:
            err = p.error
            self.stop_play()
            if err:
                self.status_var.set("Playback error: " + err.splitlines()[-1][:160])
            else:
                self.status_var.set("End of the video.")
            return
        self.after(15, self._play_tick)

    def stop_play(self):
        if not self.playing:
            return
        self.playing = False
        if self.player:
            self.player.stop()
        if self.editor:
            self.play_btn.configure(text="▶  Play")
            self.status_var.set("Stopped.")
        self.request_frame()

    # ============================================================== export
    def output_settings(self, silent=False) -> OutputSettings:
        target = None
        if self.target_on.get():
            try:
                target = float(self.target_var.get().replace(",", ".").strip())
                if target <= 0:
                    raise ValueError
            except ValueError:
                if not silent:
                    raise ValueError("Please enter a valid number of MB for the file size limit.")
                target = None
        hw = self.hw_found[0] if (self.hw_var.get() and self.hw_found) else None
        info = self.project.media[self.project.main_path] if self.project else None
        return OutputSettings(fmt=self.maps.get("fmt", {}).get(self.fmt_var.get(), "mp4"),
                              quality=self.maps.get("q", {}).get(self.q_var.get(), "original"),
                              target_mb=target,
                              speed=self.maps.get("enc", {}).get(self.enc_var.get(), "fast"),
                              hwaccel=hw, fast_copy=self.copy_var.get(),
                              low_priority=self.low_var.get(),
                              src_vkbps=info.vbitrate_kbps if info else None)

    def open_export(self):
        if not self.project:
            return
        self.stop_play()
        if self.export_dlg is not None:
            self.export_dlg.lift()
            return
        self._update_summary()
        self.export_dlg = ExportDialog(self)

    def choose_dir(self):
        d = filedialog.askdirectory(title="Choose a folder",
                                    initialdir=self.dir_var.get() or os.path.expanduser("~"),
                                    parent=self.export_dlg or self)
        if d:
            self.dir_var.set(d)

    def _schedule_summary(self):
        if self._guard:
            return
        if self._summary_after:
            self.after_cancel(self._summary_after)
        self._summary_after = self.after(120, self._update_summary)

    def _update_summary(self):
        self._summary_after = None
        if not self.project:
            return
        err, text = False, ""
        try:
            st = self.output_settings()
            new_ext = output_extension(st, self.project.main_path)
            if self.ext_var.get() != new_ext:
                self.ext_var.set(new_ext)
                if re.fullmatch(re.escape(DEFAULT_NAME) + r"(_\d+)?", self.name_var.get() or ""):
                    self.name_var.set(self._unique_name(self.dir_var.get(), new_ext))
            res = build_export(self.project, self.project_settings(), st, "out" + new_ext)
            parts = [f"Length {fmt_time(res.duration, 1)}"]
            if res.size and st.fmt != "mp3":
                parts.append(f"{res.size[0]}×{res.size[1]}")
            if st.target_mb and not st.fast_copy:
                parts.append(f"at most {st.target_mb:g} MB")
            if st.fast_copy:
                parts.append("no re-encoding")
            elif res.uses_hw:
                parts.append(HW_NAMES.get(st.hwaccel, st.hwaccel))
            text = "  ·  ".join(parts)
            if res.notes:
                text += "\n" + "\n".join("ℹ " + n for n in res.notes)
        except (ValueError, BuildError) as e:
            err, text = True, "⚠  " + str(e).replace("\n", " ")
        self.summary_var.set(text)
        self.summary_error = err
        if self.export_dlg is not None:
            self.export_dlg.refresh()

    @staticmethod
    def _unique_name(folder, ext):
        name, i = DEFAULT_NAME, 2
        while folder and os.path.exists(os.path.join(folder, name + ext)):
            name = f"{DEFAULT_NAME}_{i}"
            i += 1
        return name

    def export(self):
        if self.job or not self.project:
            return
        parent = self.export_dlg or self
        try:
            st = self.output_settings()
        except ValueError as e:
            messagebox.showerror("Please check", str(e), parent=parent)
            return
        ext = output_extension(st, self.project.main_path)
        folder = self.dir_var.get().strip()
        name = BAD_NAME_CHARS.sub("_", self.name_var.get().strip()).strip(" .")
        if not folder:
            messagebox.showerror("Folder", "Please choose a folder.", parent=parent)
            return
        if not name:
            messagebox.showerror("File name", "Please type a file name.", parent=parent)
            return
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Folder", f"Could not create the folder:\n{e}", parent=parent)
            return
        out = os.path.abspath(os.path.join(folder, name + ext))
        if any(os.path.normcase(out) == os.path.normcase(os.path.abspath(m))
               for m in self.project.media):
            messagebox.showerror("File name", "This would overwrite one of your source files. "
                                              "Please use another name.", parent=parent)
            return
        if os.path.exists(out) and not messagebox.askyesno(
                "File exists", f"{os.path.basename(out)} already exists. Replace it?",
                parent=parent):
            return
        self.settings.update(hw=self.hw_var.get(), low_priority=st.low_priority,
                             enc_speed=st.speed)
        save_settings(self.settings)
        self._start_job(st, out)

    def _start_job(self, st, out, retried=False):
        try:
            res = build_export(self.project, self.project_settings(), st, out)
        except BuildError as e:
            messagebox.showerror("Can't export", str(e), parent=self.export_dlg or self)
            return
        work = tempfile.mkdtemp(prefix="tve_")
        try:
            write_files(work, res.files)
        except OSError as e:
            shutil.rmtree(work, ignore_errors=True)
            messagebox.showerror("Error", f"Could not write a temporary file:\n{e}")
            return
        self.job = FFJob(self.ff.ffmpeg, res.args, res.duration, cwd=work,
                         low_priority=st.low_priority)
        self.job_ctx = {"out": out, "work": work, "st": st, "start": time.time(),
                        "retried": retried}
        self.job_progress = 0
        self.job_status.set("Exporting…")
        self.status_var.set("Exporting…")
        self.job.start()
        if self.export_dlg:
            self.export_dlg.refresh()

    def cancel_job(self):
        if self.job:
            self.job_status.set("Cancelling…")
            self.job.cancel()

    def _on_progress(self, frac):
        self.job_progress = frac * 100
        el = time.time() - self.job_ctx["start"]
        txt = f"Exporting…  {int(frac * 100)}%"
        if frac > 0.03 and el > 2:
            txt += f"   ·   about {fmt_time(el / frac - el, 0)} left"
        self.job_status.set(txt)
        self.status_var.set(txt)
        if self.export_dlg:
            self.export_dlg.progress["value"] = self.job_progress

    def _on_done(self, rc, err):
        ctx, self.job, self.job_ctx = self.job_ctx, None, None
        shutil.rmtree(ctx["work"], ignore_errors=True)
        out = ctx["out"]
        parent = self.export_dlg or self
        if rc == 0:
            self.job_progress = 100
            el = time.time() - ctx["start"]
            try:
                mb = os.path.getsize(out) / (1024 * 1024)
            except OSError:
                mb = 0
            msg = f"✓  Saved  ·  {mb:.1f} MB  ·  took {fmt_time(el, 0)}"
            self.job_status.set(msg)
            self.status_var.set(msg)
            folder = os.path.dirname(out)
            self.name_var.set(self._unique_name(folder, os.path.splitext(out)[1]))
            if self.export_dlg:
                self.export_dlg.refresh()
            if messagebox.askyesno("Done", f"Your video was saved:\n{out}\n\nSize: {mb:.1f} MB\n\n"
                                           "Show it in its folder?", parent=parent):
                reveal_in_folder(out)
            return
        self._remove_quiet(out)
        self.job_progress = 0
        if rc == -2:
            self.job_status.set("Cancelled.")
            self.status_var.set("Export cancelled.")
            if self.export_dlg:
                self.export_dlg.refresh()
            return
        st = ctx["st"]
        if st.hwaccel and not ctx["retried"]:
            st.hwaccel = None
            self.hw_found = []
            self.hw_var.set(False)
            self.hw_text.set("Use the graphics card (did not work on this computer)")
            self.job_status.set("The graphics card failed, trying with the processor…")
            self._start_job(st, out, retried=True)
            return
        self.job_status.set("Export failed.")
        self.status_var.set("Export failed.")
        if self.export_dlg:
            self.export_dlg.refresh()
        messagebox.showerror("Export failed", "FFmpeg reported an error:\n\n" +
                             ((err or "").strip()[-1200:] or "Unknown error"), parent=parent)

    @staticmethod
    def _remove_quiet(path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    # ============================================================== events
    def _poll(self):
        redraw = False
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "frame":
                    self._on_frame(ev[1])
                elif kind == "thumbinit":
                    self.thumbs[ev[1]] = [None] * ev[2]
                elif kind == "thumb":
                    _, path, i, data = ev
                    if data and path in self.thumbs:
                        try:
                            self.thumbs[path][i] = tk.PhotoImage(data=base64.b64encode(data))
                            redraw = True
                        except tk.TclError:
                            pass
                elif kind == "wave":
                    if ev[2]:
                        self.waves[ev[1]] = ev[2]
                        redraw = True
                elif kind == "probe":
                    self._on_probe(ev[1], ev[2], ev[3])
                elif kind == "drop":
                    self._on_drop(ev[1], ev[2], ev[3])
                elif kind == "hw":
                    self.hw_found = ev[1]
                    if self.hw_found:
                        self.hw_text.set("Use the graphics card (" +
                                         HW_NAMES.get(self.hw_found[0], self.hw_found[0]) + ")")
                    else:
                        self.hw_text.set("Use the graphics card (not supported here)")
                        self.hw_var.set(False)
                    self._schedule_summary()
        except queue.Empty:
            pass
        if redraw and self.editor is not None and self.timeline.mode is None:
            self.timeline.schedule_redraw()
        if self.job:
            try:
                while True:
                    ev = self.job.events.get_nowait()
                    if ev[0] == "progress":
                        self._on_progress(ev[1])
                    else:
                        self._on_done(ev[1], ev[2])
                        break
            except queue.Empty:
                pass
        self.after(80, self._poll)

    def _on_close(self):
        if self.job:
            if not messagebox.askyesno("Quit", "A video is still being exported. Cancel it and quit?"):
                return
            self.job.cancel()
            out = self.job_ctx["out"] if self.job_ctx else None
            for _ in range(30):
                if self.job.proc is None or self.job.proc.poll() is not None:
                    break
                time.sleep(0.1)
            if out:
                self._remove_quiet(out)
        if self.player:
            self.player.stop()
        save_settings(self.settings)
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.destroy()


def main(argv=None):
    argv = argv or []
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    initial = argv[0] if argv and os.path.isfile(argv[0]) else None
    App(initial).mainloop()
