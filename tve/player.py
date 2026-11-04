"""Live preview playback.

Video: ffmpeg renders the composed timeline at preview size as raw RGB frames into a pipe;
a reader thread paces them to real time (dropping frames if the computer is slow).
Audio: a second ffmpeg renders the mixed audio and pipes it into `ffplay -nodisp`.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Optional

from .ffmpeg_utils import popen_kwargs

AUDIO_LATENCY = 0.25  # seconds ffplay usually needs before sound starts


class Player:
    def __init__(self, ffmpeg: str, ffplay: Optional[str]):
        self.ffmpeg = ffmpeg
        self.ffplay = ffplay
        self.lock = threading.Lock()
        self.frame: Optional[bytes] = None
        self.frame_no = -1
        self.time = 0.0
        self.running = False
        self.finished = False
        self.error = ""
        self._procs = []

    def start(self, vargs, aargs, w: int, h: int, fps: float, t0: float, cwd: str):
        self.stop()
        self.frame, self.frame_no, self.time = None, -1, t0
        self.running, self.finished, self.error = True, False, ""
        self.w, self.h, self.fps, self.t0, self.cwd = w, h, fps, t0, cwd
        self.aargs = aargs if self.ffplay else None
        kw = popen_kwargs(False)
        self.vp = subprocess.Popen([self.ffmpeg] + vargs, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=cwd, bufsize=0, **kw)
        self._procs = [self.vp]
        threading.Thread(target=self._read_video, daemon=True).start()

    def _start_audio(self):
        if not self.aargs or not self.running:
            return False
        kw = popen_kwargs(False)
        try:
            ap = subprocess.Popen([self.ffmpeg] + self.aargs, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  cwd=self.cwd, **kw)
            fp = subprocess.Popen([self.ffplay, "-hide_banner", "-loglevel", "quiet", "-nodisp",
                                   "-autoexit", "-i", "pipe:0"], stdin=ap.stdout,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
            ap.stdout.close()
            self._procs += [ap, fp]
            return True
        except Exception:
            return False

    def _read_exact(self, n):
        buf = bytearray()
        out = self.vp.stdout
        while len(buf) < n:
            chunk = out.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def _read_video(self):
        size = self.w * self.h * 3
        header = f"P6 {self.w} {self.h} 255\n".encode()
        start = None
        i = 0
        while self.running:
            data = self._read_exact(size)
            if data is None:
                break
            if start is None:
                with_audio = self._start_audio()
                start = time.time() + (AUDIO_LATENCY if with_audio else 0.0)
            target = start + i / self.fps
            now = time.time()
            if now < target:
                time.sleep(target - now)
            elif now - target > 1.5 / self.fps:
                i += 1          # too slow: skip showing this frame
                with self.lock:
                    self.time = self.t0 + i / self.fps
                continue
            with self.lock:
                self.frame = header + data
                self.frame_no = i
                self.time = self.t0 + i / self.fps
            i += 1
        if self.running:
            try:
                err = self.vp.stderr.read().decode("utf-8", "replace").strip()
            except Exception:
                err = ""
            if self.vp.wait() not in (0, None) and err:
                self.error = err[-800:]
        self.finished = True

    def stop(self):
        self.running = False
        for p in self._procs:
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        self._procs = []
