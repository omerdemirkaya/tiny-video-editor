"""Dark, clean theme (on top of ttk "clam")."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

IS_WIN = os.name == "nt"

BG = "#14161b"
PANEL = "#1c1f26"
CARD = "#262b35"
CARD_HI = "#313745"
BORDER = "#323846"
TEXT = "#e8eaf0"
MUTED = "#8d95a5"
FAINT = "#5d6575"
ACCENT = "#5b8cff"
ACCENT_HI = "#7ba3ff"
ACCENT_DIS = "#2c3a5c"
DANGER = "#ff6b6b"
DANGER_BG = "#3a2329"
DANGER_HI = "#4a2a31"
OK = "#4cd4a0"
WARN = "#ffb84d"

FONT_FAMILY = "Segoe UI" if IS_WIN else "DejaVu Sans"
MONO_FAMILY = "Consolas" if IS_WIN else "DejaVu Sans Mono"


def font(size=10, weight="normal"):
    return (FONT_FAMILY, size, weight)


def mono(size=11, weight="normal"):
    return (MONO_FAMILY, size, weight)


def _button(st, name, bg, hover, fg, press=None, dis_bg=None, dis_fg=FAINT, **extra):
    press = press or hover
    dis_bg = dis_bg or bg
    st.configure(name, background=bg, foreground=fg, bordercolor=bg, lightcolor=bg,
                 darkcolor=bg, relief="flat", focuscolor=bg, borderwidth=0, **extra)
    st.map(name,
           background=[("disabled", dis_bg), ("pressed", press), ("active", hover)],
           bordercolor=[("disabled", dis_bg), ("pressed", press), ("active", hover)],
           lightcolor=[("disabled", dis_bg), ("pressed", press), ("active", hover)],
           darkcolor=[("disabled", dis_bg), ("pressed", press), ("active", hover)],
           focuscolor=[("active", hover)],
           foreground=[("disabled", dis_fg)])


def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=BG)
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=PANEL, foreground=TEXT, fieldbackground=CARD,
                 bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL, troughcolor=CARD,
                 focuscolor=PANEL, selectbackground=ACCENT, selectforeground="#ffffff",
                 insertcolor=TEXT, font=font(10))
    st.configure("TFrame", background=PANEL)
    st.configure("Bg.TFrame", background=BG)
    st.configure("TLabel", background=PANEL, foreground=TEXT)
    st.configure("Muted.TLabel", foreground=MUTED, font=font(9))
    st.configure("Field.TLabel", foreground=MUTED, font=font(9))
    st.configure("H.TLabel", font=font(11, "bold"))
    st.configure("Err.TLabel", foreground=DANGER, font=font(9))
    st.configure("Ok.TLabel", foreground=OK, font=font(9))

    _button(st, "TButton", CARD, "#313745", TEXT, "#3a4152", PANEL, padding=(12, 7), font=font(10))
    _button(st, "Tool.TButton", CARD, "#313745", TEXT, "#3a4152", PANEL, padding=(10, 6), font=font(9))
    _button(st, "Icon.TButton", BG, CARD, TEXT, "#313745", BG, padding=(6, 4), font=font(11))
    _button(st, "Ghost.TButton", BG, CARD, MUTED, "#313745", BG, padding=(10, 6), font=font(9))
    _button(st, "Head.TButton", PANEL, CARD, TEXT, "#313745", PANEL, padding=(12, 6), font=font(9))
    _button(st, "Accent.TButton", ACCENT, ACCENT_HI, "#ffffff", "#4a78e6", ACCENT_DIS, "#8a97b5",
            padding=(16, 10), font=font(11, "bold"))
    _button(st, "Hero.TButton", ACCENT, ACCENT_HI, "#ffffff", "#4a78e6", ACCENT_DIS, "#8a97b5",
            padding=(34, 12), font=font(12, "bold"))
    _button(st, "Play.TButton", ACCENT, ACCENT_HI, "#ffffff", "#4a78e6", ACCENT_DIS, "#8a97b5",
            padding=(14, 6), font=font(10, "bold"))
    _button(st, "Danger.TButton", DANGER_BG, DANGER_HI, DANGER, "#5a3038", PANEL,
            padding=(10, 6), font=font(9, "bold"))

    st.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=TEXT,
                 arrowcolor=MUTED, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD,
                 padding=(8, 5), arrowsize=13)
    st.map("TCombobox",
           fieldbackground=[("disabled", PANEL), ("readonly", CARD)],
           background=[("disabled", PANEL), ("active", "#313745")],
           foreground=[("disabled", FAINT), ("readonly", TEXT)],
           selectbackground=[("readonly", CARD)],
           selectforeground=[("readonly", TEXT)],
           bordercolor=[("focus", ACCENT), ("hover", "#3a4152")],
           lightcolor=[("focus", CARD)], darkcolor=[("focus", CARD)],
           arrowcolor=[("disabled", FAINT), ("hover", TEXT)])
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", font(10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*TCombobox*Listbox.relief", "flat")

    st.configure("TEntry", fieldbackground=CARD, foreground=TEXT, bordercolor=CARD,
                 lightcolor=CARD, darkcolor=CARD, insertcolor=TEXT, padding=(8, 5))
    st.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", CARD)],
           fieldbackground=[("disabled", PANEL)], foreground=[("disabled", FAINT)])

    st.configure("TCheckbutton", background=PANEL, foreground=TEXT, indicatorbackground=CARD,
                 indicatorforeground="#ffffff", upperbordercolor=BORDER, lowerbordercolor=BORDER,
                 focuscolor=PANEL, padding=(0, 4), indicatormargin=(0, 0, 8, 0))
    st.map("TCheckbutton",
           indicatorbackground=[("disabled", PANEL), ("selected", ACCENT), ("active", "#313745")],
           upperbordercolor=[("selected", ACCENT)], lowerbordercolor=[("selected", ACCENT)],
           foreground=[("disabled", FAINT)], background=[("active", PANEL)])

    st.configure("TNotebook", background=PANEL, borderwidth=0, tabmargins=(12, 12, 12, 0),
                 lightcolor=PANEL, darkcolor=PANEL, bordercolor=PANEL)
    st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(14, 7),
                 borderwidth=0, lightcolor=PANEL, darkcolor=PANEL, bordercolor=PANEL,
                 focuscolor=PANEL, font=font(10, "bold"))
    st.map("TNotebook.Tab",
           background=[("selected", CARD), ("active", PANEL)],
           foreground=[("selected", TEXT), ("active", TEXT)],
           lightcolor=[("selected", CARD)], darkcolor=[("selected", CARD)],
           bordercolor=[("selected", CARD)])

    st.configure("Horizontal.TScrollbar", troughcolor=PANEL, background=CARD, bordercolor=PANEL,
                 lightcolor=CARD, darkcolor=CARD, arrowcolor=MUTED, gripcount=0, arrowsize=12)
    st.map("Horizontal.TScrollbar", background=[("active", "#3a4152"), ("disabled", PANEL)])
    st.configure("Vertical.TScrollbar", troughcolor=PANEL, background=CARD, bordercolor=PANEL,
                 lightcolor=CARD, darkcolor=CARD, arrowcolor=MUTED, gripcount=0, arrowsize=12)
    st.map("Vertical.TScrollbar", background=[("active", "#3a4152"), ("disabled", PANEL)])
    st.configure("Horizontal.TProgressbar", troughcolor=CARD, background=ACCENT,
                 bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)
    st.configure("Horizontal.TScale", background=ACCENT, troughcolor=CARD, bordercolor=PANEL,
                 lightcolor=ACCENT, darkcolor=ACCENT, gripcount=0)
    st.map("Horizontal.TScale", background=[("disabled", FAINT), ("active", ACCENT_HI)])


def dark_titlebar(win) -> None:
    """Dark title bar on Windows 10/11."""
    if not IS_WIN:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


def rounded_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def draw_logo(c: tk.Canvas, cx, cy, r, bg=None):
    """Simple logo: rounded blue square, play triangle and two cut lines."""
    rounded_rect(c, cx - r, cy - r, cx + r, cy + r, r * 0.45, fill=ACCENT, outline="")
    s = r * 0.42
    c.create_polygon(cx - s * 0.7, cy - s, cx - s * 0.7, cy + s, cx + s, cy,
                     fill="#ffffff", outline="")
    c.create_line(cx - r * 0.62, cy - r * 0.62, cx - r * 0.62, cy + r * 0.62,
                  fill="#ffffff", width=max(1, r // 9))
    c.create_line(cx + r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62,
                  fill="#ffffff", width=max(1, r // 9))


class Tooltip:
    """Small dark tooltip shown when the mouse rests on a widget."""

    def __init__(self, widget, text, delay=450, wrap=300):
        self.widget, self.text, self.delay, self.wrap = widget, text, delay, wrap
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _e=None):
        self.hide()
        self._after = self.widget.after(self.delay, self.show)

    def show(self):
        if self._tip or not self.text:
            return
        w = self.widget
        tip = tk.Toplevel(w)
        tip.wm_overrideredirect(True)
        tip.configure(bg=BORDER)
        tk.Label(tip, text=self.text, bg=CARD, fg=TEXT, font=font(9), justify="left",
                 wraplength=self.wrap, padx=10, pady=7).pack(padx=1, pady=1)
        tip.update_idletasks()
        x = w.winfo_rootx() + min(10, w.winfo_width() // 2)
        y = w.winfo_rooty() + w.winfo_height() + 6
        sw = w.winfo_screenwidth()
        x = max(0, min(x, sw - tip.winfo_reqwidth() - 8))
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def hide(self, _e=None):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tip:
            self._tip.destroy()
            self._tip = None


class ScrollFrame(ttk.Frame):
    """A frame whose content (`.inner`) scrolls vertically with the mouse wheel when it is
    taller than the visible area. The scrollbar only appears when it is needed."""

    def __init__(self, master, padding=(16, 14, 16, 12)):
        super().__init__(master)
        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0, bd=0,
                                yscrollincrement=20)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=padding)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda e: self._update())
        self.canvas.bind("<Configure>", lambda e: self._update())
        self.bind_all("<MouseWheel>", self._wheel, add="+")
        self.bind_all("<Button-4>", lambda e: self._wheel(e, 120), add="+")
        self.bind_all("<Button-5>", lambda e: self._wheel(e, -120), add="+")
        self._busy = False

    def _update(self):
        if self._busy:
            return
        self._busy = True
        try:
            cw = self.canvas.winfo_width()
            self.canvas.itemconfigure(self._win, width=cw)
            ih = self.inner.winfo_reqheight()
            self.canvas.configure(scrollregion=(0, 0, cw, ih))
            need = ih > self.canvas.winfo_height() + 2
            if need and not self.vsb.winfo_ismapped():
                self.vsb.pack(side="right", fill="y", before=self.canvas)
            elif not need and self.vsb.winfo_ismapped():
                self.vsb.pack_forget()
                self.canvas.yview_moveto(0)
        finally:
            self._busy = False

    def _wheel(self, e, delta=None):
        try:
            w = e.widget
            if not isinstance(w, tk.Misc) or not str(w).startswith(str(self)):
                return
        except Exception:
            return
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height() + 2:
            return
        d = delta if delta is not None else e.delta
        self.canvas.yview_scroll(-3 if d > 0 else 3, "units")
        return "break"
