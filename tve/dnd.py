"""Drag & drop of files from Windows Explorer, without extra packages.

Uses the classic WM_DROPFILES message: the Tk window is marked as accepting files and its
window procedure is subclassed to catch the drop. Drops on any child widget reach it.
On other systems `hook_file_drop` simply returns False.
"""
from __future__ import annotations

import os


def hook_file_drop(widget, callback) -> bool:
    """callback(files: list[str], screen_x: int, screen_y: int) is called on every drop."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM)
        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        set_long.restype = ctypes.c_void_p
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.CallWindowProcW.restype = LRESULT
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM]
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p,
                                           wintypes.UINT]
        shell32.DragQueryPoint.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        WM_DROPFILES, GWLP_WNDPROC = 0x0233, -4

        hwnd = widget.winfo_id()
        state = {}

        def proc(h, msg, wp, lp):
            if msg == WM_DROPFILES:
                try:
                    hdrop = ctypes.c_void_p(wp)
                    n = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                    files = []
                    for i in range(n):
                        ln = shell32.DragQueryFileW(hdrop, i, None, 0)
                        buf = ctypes.create_unicode_buffer(ln + 1)
                        shell32.DragQueryFileW(hdrop, i, buf, ln + 1)
                        files.append(buf.value)
                    pt = wintypes.POINT()
                    shell32.DragQueryPoint(hdrop, ctypes.byref(pt))
                    user32.ClientToScreen(h, ctypes.byref(pt))
                    shell32.DragFinish(hdrop)
                    callback(files, pt.x, pt.y)
                except Exception:
                    pass
                return 0
            return user32.CallWindowProcW(state["old"], h, msg, wp, lp)

        cb = WNDPROC(proc)
        # allow drops even if the program runs as administrator
        try:
            for m in (WM_DROPFILES, 0x004A, 0x0049):
                user32.ChangeWindowMessageFilterEx(hwnd, m, 1, None)
        except Exception:
            pass
        shell32.DragAcceptFiles(hwnd, True)
        state["cb"] = cb
        state["old"] = set_long(hwnd, GWLP_WNDPROC, ctypes.cast(cb, ctypes.c_void_p))
        widget._tve_drop_state = state      # keep the callback alive
        return True
    except Exception:
        return False
