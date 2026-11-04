"""Small reusable form dialog."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .theme import CARD, PANEL, TEXT, ACCENT, BORDER, MUTED, dark_titlebar, font
from .timeutil import parse_time


class FormDialog(tk.Toplevel):
    """Opens a small form. The result is `self.result` (dict) or None.

    Field types: choice, text, multiline, float, time, level (1-10), bool
    """

    def __init__(self, parent, title, fields, intro=None, validate=None, ok_text="OK"):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(bg=PANEL)
        self.result = None
        self._fields = []
        self._validate = validate

        body = ttk.Frame(self, padding=20)
        body.pack(fill="both", expand=True)
        row = 0
        if intro:
            ttk.Label(body, text=intro, wraplength=420, justify="left",
                      style="Muted.TLabel").grid(row=row, column=0, columnspan=2,
                                              sticky="w", pady=(0, 10))
            row += 1
        has_multiline = False
        first = None
        for f in fields:
            t = f.get("type", "text")
            default = f.get("default", "")
            if t == "bool":
                var = tk.BooleanVar(value=bool(default))
                w = ttk.Checkbutton(body, text=f["label"], variable=var)
                w.grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
            else:
                ttk.Label(body, text=f["label"], style="Field.TLabel").grid(
                    row=row, column=0, sticky="nw", padx=(0, 14), pady=8)
                if t == "choice":
                    var = tk.StringVar(value=default or f["choices"][0])
                    w = ttk.Combobox(body, textvariable=var, values=f["choices"],
                                     state="readonly", width=38, height=16)
                elif t == "multiline":
                    has_multiline = True
                    w = tk.Text(body, width=40, height=4, wrap="word", font=font(10), bg=CARD,
                                fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                                highlightthickness=1, highlightbackground=CARD,
                                highlightcolor=ACCENT, padx=8, pady=6)
                    w.insert("1.0", default)
                    var = w
                elif t == "level":
                    var = tk.IntVar(value=int(default or 5))
                    w = tk.Scale(body, from_=1, to=10, orient="horizontal", variable=var,
                                 length=260, showvalue=True, bg=PANEL, fg=TEXT,
                                 troughcolor=CARD, highlightthickness=0, bd=0,
                                 activebackground=ACCENT, sliderrelief="flat",
                                 font=font(9))
                else:
                    var = tk.StringVar(value=str(default))
                    w = ttk.Entry(body, textvariable=var, width=40)
                w.grid(row=row, column=1, sticky="we", pady=5)
                if f.get("hint"):
                    row += 1
                    ttk.Label(body, text=f["hint"], style="Muted.TLabel", wraplength=320,
                              justify="left").grid(row=row, column=1, sticky="w")
            if first is None:
                first = w
            self._fields.append((f, var))
            row += 1

        btns = ttk.Frame(body)
        btns.grid(row=row, column=0, columnspan=2, sticky="e", pady=(20, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text=ok_text, style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())
        if not has_multiline:
            self.bind("<Return>", lambda e: self._ok())

        self.update_idletasks()
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
            py = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
            self.geometry(f"+{max(0, px)}+{max(0, py)}")
        except tk.TclError:
            pass
        self.deiconify()
        dark_titlebar(self)
        self.grab_set()
        if first is not None:
            first.focus_set()
        self.wait_window(self)

    def _ok(self):
        res = {}
        for f, var in self._fields:
            t = f.get("type", "text")
            try:
                if t == "multiline":
                    v = var.get("1.0", "end").rstrip("\n")
                else:
                    v = var.get()
                if t == "float":
                    v = float(str(v).replace(",", ".").strip())
                    lo, hi = f.get("min"), f.get("max")
                    if (lo is not None and v < lo) or (hi is not None and v > hi):
                        raise ValueError
                elif t == "time":
                    v = parse_time(v)
            except (ValueError, tk.TclError):
                rng = ""
                if f.get("min") is not None and f.get("max") is not None:
                    rng = f" (between {f['min']:g} and {f['max']:g})"
                messagebox.showerror("Invalid value",
                                     f"Please enter a valid value for “{f['label']}”{rng}.",
                                     parent=self)
                return
            res[f["key"]] = v
        if self._validate:
            err = self._validate(res)
            if err:
                messagebox.showerror("Please check", err, parent=self)
                return
        self.result = res
        self.destroy()


def ask_form(parent, title, fields, intro=None, validate=None, ok_text="OK"):
    return FormDialog(parent, title, fields, intro, validate, ok_text).result
