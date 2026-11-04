"""Low level ffmpeg / ffprobe helpers.

- locating ffmpeg / ffplay
- probing media (duration, size, audio...)
- thumbnails and audio waveforms
- hardware encoder (NVIDIA / Intel / AMD) detection
- running a job in the background at low priority and reading its progress
"""
from __future__ import annotations

import json
import os
import queue
from array import array
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from typing import List, Optional

IS_WIN = os.name == "nt"

CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

HW_NAMES = {"nvenc": "NVIDIA (NVENC)", "qsv": "Intel Quick Sync", "amf": "AMD (AMF)"}
_HW_TEST = [("nvenc", "h264_nvenc", "yuv420p"),
            ("qsv", "h264_qsv", "nv12"),
            ("amf", "h264_amf", "nv12")]


class ProbeError(Exception):
    pass


def app_dir() -> str:
    """Folder of the program (also when packaged with PyInstaller)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def popen_kwargs(low_priority: bool = False) -> dict:
    """No console window on Windows, optionally at low priority."""
    kw: dict = {}
    if IS_WIN:
        flags = CREATE_NO_WINDOW
        if low_priority:
            flags |= BELOW_NORMAL_PRIORITY_CLASS
        kw["creationflags"] = flags
    elif low_priority:
        kw["preexec_fn"] = lambda: os.nice(10)
    return kw


def _exe(name: str) -> str:
    return name + (".exe" if IS_WIN else "")


def _works(path: str) -> bool:
    try:
        r = subprocess.run([path, "-version"], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=15, **popen_kwargs())
        return r.returncode == 0
    except Exception:
        return False


def bundled_ffmpeg() -> str:
    """The FFmpeg that ships inside the program folder (ffmpeg/bin)."""
    return os.path.join(app_dir(), "ffmpeg", "bin", _exe("ffmpeg"))


def find_ffmpeg(preferred: Optional[str] = None) -> Optional[str]:
    """The bundled FFmpeg always wins; others are only a fallback."""
    base = app_dir()
    candidates = [bundled_ffmpeg()]
    for sub in ("", "bin", "ffmpeg"):
        candidates.append(os.path.join(base, sub, _exe("ffmpeg")))
    if preferred:
        candidates.append(preferred)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.isfile(c) and _works(c):
            return c
    return None


def find_ffprobe(ffmpeg_path: str) -> Optional[str]:
    c = os.path.join(os.path.dirname(ffmpeg_path), _exe("ffprobe"))
    if os.path.isfile(c):
        return c
    return shutil.which("ffprobe")


def find_ffplay(ffmpeg_path: str) -> Optional[str]:
    c = os.path.join(os.path.dirname(ffmpeg_path), _exe("ffplay"))
    if os.path.isfile(c):
        return c
    return shutil.which("ffplay")


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_rate(r) -> float:
    try:
        r = str(r)
        if "/" in r:
            a, b = r.split("/", 1)
            return float(a) / float(b) if float(b) else 0.0
        return float(r)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    vcodec: str
    has_audio: bool
    acodec: str
    size_bytes: int
    bitrate_kbps: Optional[int] = None
    vbitrate_kbps: Optional[int] = None
    has_video: bool = True

    def summary(self) -> str:
        from .timeutil import fmt_time
        mb = self.size_bytes / (1024 * 1024)
        parts = [os.path.basename(self.path), fmt_time(self.duration, 1),
                 f"{self.width}x{self.height}", f"{self.fps:.3g} fps",
                 f"{mb:.1f} MB", self.vcodec.upper()]
        if not self.has_audio:
            parts.append("no audio")
        return "  •  ".join(parts)


class FFmpeg:
    def __init__(self, ffmpeg_path: str):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = find_ffprobe(ffmpeg_path)
        self.ffplay = find_ffplay(ffmpeg_path)
        self.version_major = 0
        try:
            r = self._run([ffmpeg_path, "-version"], timeout=15)
            m = re.search(r"ffmpeg version n?(\d+)", r.stdout.decode("utf-8", "replace"))
            if m:
                self.version_major = int(m.group(1))
        except Exception:
            pass

    def _run(self, args: List[str], timeout: float = 30) -> subprocess.CompletedProcess:
        return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, **popen_kwargs())

    # ---------------------------------------------------------------- probe
    def probe(self, path: str, require_video: bool = True) -> VideoInfo:
        if not os.path.isfile(path):
            raise ProbeError("File not found.")
        if self.ffprobe:
            try:
                r = self._run([self.ffprobe, "-v", "error", "-print_format", "json",
                               "-show_format", "-show_streams", path], timeout=40)
            except subprocess.TimeoutExpired:
                raise ProbeError("Reading the file took too long.")
            if r.returncode == 0:
                try:
                    data = json.loads(r.stdout.decode("utf-8", "replace"))
                except ValueError:
                    data = None
                if data is not None:
                    return self._from_json(path, data, require_video)
        return self._probe_with_ffmpeg(path, require_video)

    def _from_json(self, path: str, data: dict, require_video: bool = True) -> VideoInfo:
        streams = data.get("streams") or []
        fmt = data.get("format") or {}
        v = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if v is None:
            if require_video or a is None:
                raise ProbeError("This file has no video picture.\nPlease choose a video file."
                                 if require_video else "This file has no audio or video.")
            dur = _f(fmt.get("duration")) or _f(a.get("duration"))
            if dur <= 0:
                raise ProbeError("Could not read the length of this file.")
            return VideoInfo(path=path, duration=dur, width=0, height=0, fps=0.0, vcodec="",
                             has_audio=True, acodec=str(a.get("codec_name") or ""),
                             size_bytes=os.path.getsize(path), has_video=False)
        dur = _f(fmt.get("duration")) or _f(v.get("duration"))
        if dur <= 0:
            raise ProbeError("Could not read the length of the video. The file may be broken "
                             "or unsupported.")
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        if not w or not h:
            raise ProbeError("Could not read the video size.")
        rot = 0
        tags = v.get("tags") or {}
        if "rotate" in tags:
            rot = int(_f(tags["rotate"]))
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(_f(sd["rotation"]))
        if abs(rot) % 180 == 90:
            w, h = h, w
        fps = _parse_rate(v.get("avg_frame_rate")) or _parse_rate(v.get("r_frame_rate"))
        if not (0.5 <= fps <= 240):
            fps = 25.0
        br = _f(fmt.get("bit_rate"))
        vb = _f(v.get("bit_rate"))
        if not vb and br:
            ab = _f((a or {}).get("bit_rate")) or (128000 if a else 0)
            vb = br - ab
        return VideoInfo(path=path, duration=dur, width=w, height=h, fps=fps,
                         vcodec=str(v.get("codec_name") or "?"), has_audio=a is not None,
                         acodec=str((a or {}).get("codec_name") or ""),
                         size_bytes=os.path.getsize(path),
                         bitrate_kbps=int(br / 1000) if br else None,
                         vbitrate_kbps=int(vb / 1000) if vb > 0 else None)

    def _probe_with_ffmpeg(self, path: str, require_video: bool = True) -> VideoInfo:
        """Fallback when ffprobe is missing: parse ffmpeg's output."""
        try:
            r = self._run([self.ffmpeg, "-hide_banner", "-i", path], timeout=40)
        except subprocess.TimeoutExpired:
            raise ProbeError("Reading the file took too long.")
        txt = r.stderr.decode("utf-8", "replace")
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", txt)
        if not m:
            raise ProbeError("Could not read this file. Please choose a supported file.")
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        vm = re.search(r"Stream #.*?Video:\s*([^\s,]+).*?(\d{2,5})x(\d{2,5})", txt)
        am0 = re.search(r"Stream #.*?Audio:\s*([^\s,]+)", txt)
        if not vm:
            if require_video or not am0:
                raise ProbeError("This file has no video picture.")
            return VideoInfo(path=path, duration=dur, width=0, height=0, fps=0.0, vcodec="",
                             has_audio=True, acodec=am0.group(1),
                             size_bytes=os.path.getsize(path), has_video=False)
        w, h = int(vm.group(2)), int(vm.group(3))
        rm = re.search(r"rotate\s*:\s*(-?\d+)", txt) or re.search(r"rotation of (-?[\d.]+)", txt)
        if rm and abs(int(float(rm.group(1)))) % 180 == 90:
            w, h = h, w
        fm = re.search(r"([\d.]+)\s*fps", txt)
        fps = float(fm.group(1)) if fm else 25.0
        am = re.search(r"Stream #.*?Audio:\s*([^\s,]+)", txt)
        return VideoInfo(path=path, duration=dur, width=w, height=h, fps=fps or 25.0,
                         vcodec=vm.group(1), has_audio=am is not None,
                         acodec=am.group(1) if am else "", size_bytes=os.path.getsize(path))

    # ------------------------------------------------------------ preview
    def grab_frame(self, path: str, t: float, max_w: int, max_h: int,
                   fill: bool = False, fast: bool = False) -> Optional[bytes]:
        """Small PNG of the source frame at time t.

        fill=True: fill exactly w x h (cropping) – for thumbnails.
        fast=True: decode keyframes only – very fast, for the timeline.
        """
        if fill:
            vf = (f"scale={max_w}:{max_h}:force_original_aspect_ratio=increase"
                  f":flags=fast_bilinear,crop={max_w}:{max_h}")
        else:
            vf = (f"scale=w={max_w}:h={max_h}:force_original_aspect_ratio=decrease"
                  f":flags=fast_bilinear")
        pre = ["-skip_frame", "nokey"] if fast else []
        for tt in (t, t - 0.5, t - 2.0):
            if tt < 0 and tt != t:
                continue
            args = [self.ffmpeg, "-hide_banner", "-loglevel", "error",
                    "-ss", f"{max(0.0, tt):.3f}"] + pre + ["-i", path, "-frames:v", "1",
                    "-an", "-sn", "-vf", vf, "-f", "image2pipe", "-c:v", "png", "-"]
            try:
                r = self._run(args, timeout=20)
            except subprocess.TimeoutExpired:
                return None
            if r.returncode == 0 and r.stdout:
                return r.stdout
        return None

    def waveform(self, path: str, per_second: int = 50) -> Optional[List[float]]:
        """Peak levels (0..1), `per_second` values per second of audio."""
        rate = 2000
        n = max(1, rate // per_second)
        try:
            r = self._run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path,
                           "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"], timeout=300)
        except Exception:
            return None
        if r.returncode != 0 and not r.stdout:
            return None
        data = r.stdout[: len(r.stdout) // 2 * 2]
        arr = array("h")
        arr.frombytes(data)
        if sys.byteorder == "big":
            arr.byteswap()
        peaks = []
        for i in range(0, len(arr), n):
            chunk = arr[i:i + n]
            peaks.append(max(max(chunk), -min(chunk)) / 32768.0)
        return peaks

    # -------------------------------------------------------- hw detection
    def detect_hw(self) -> List[str]:
        """Hardware encoders that really work on this computer."""
        try:
            r = self._run([self.ffmpeg, "-hide_banner", "-encoders"], timeout=15)
            enc_list = r.stdout.decode("utf-8", "replace")
        except Exception:
            return []
        found = []
        for fam, enc, pix in _HW_TEST:
            if f" {enc} " not in enc_list:
                continue
            try:
                r = self._run([self.ffmpeg, "-hide_banner", "-loglevel", "error",
                               "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=0.5",
                               "-vf", f"format={pix}", "-c:v", enc, "-f", "null", "-"],
                              timeout=25)
            except Exception:
                continue
            if r.returncode == 0:
                found.append(fam)
        return found

    def test_filter(self, expr: str, audio: bool = False) -> Optional[str]:
        """Try a custom filter on tiny fake input. Returns the error text or None."""
        if audio:
            args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=stereo", "-t", "0.3", "-af", expr,
                    "-f", "null", "-"]
        else:
            args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=gray:s=320x240:r=10:d=0.3", "-vf", expr,
                    "-f", "null", "-"]
        try:
            r = self._run(args, timeout=20)
        except subprocess.TimeoutExpired:
            return "Testing the filter took too long."
        if r.returncode == 0:
            return None
        return r.stderr.decode("utf-8", "replace").strip()[-600:] or "Unknown error"


class FFJob:
    """Runs ffmpeg in the background and posts progress to `events`.

    Events: ("progress", fraction 0..1, seconds_done) and ("done", return_code, error_text).
    The return code is -2 when cancelled.
    """

    def __init__(self, ffmpeg: str, args: List[str], total_seconds: float,
                 cwd: Optional[str] = None, low_priority: bool = True):
        self.ffmpeg = ffmpeg
        self.args = args
        self.total = max(0.001, float(total_seconds))
        self.cwd = cwd
        self.low_priority = low_priority
        self.events: "queue.Queue" = queue.Queue()
        self.proc: Optional[subprocess.Popen] = None
        self.cancelled = False

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self.cancelled = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass

    def _run(self) -> None:
        try:
            self.proc = subprocess.Popen([self.ffmpeg] + self.args, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         cwd=self.cwd, **popen_kwargs(self.low_priority))
        except Exception as e:  # noqa: BLE001
            self.events.put(("done", -1, f"Could not start ffmpeg: {e}"))
            return
        if self.cancelled:
            self.cancel()
        err_lines: deque = deque(maxlen=40)

        def read_err():
            for raw in iter(self.proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    err_lines.append(line)

        t = threading.Thread(target=read_err, daemon=True)
        t.start()
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            sec = None
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                val = line.split("=", 1)[1]
                if val.lstrip("-").isdigit():
                    sec = int(val) / 1_000_000
            elif line.startswith("out_time="):
                m = re.match(r"out_time=(-?\d+):(\d+):([\d.]+)", line)
                if m:
                    sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            if sec is not None and sec >= 0:
                self.events.put(("progress", min(1.0, sec / self.total), sec))
        rc = self.proc.wait()
        t.join(timeout=3)
        if self.cancelled:
            rc = -2
        self.events.put(("done", rc, "\n".join(err_lines)))
