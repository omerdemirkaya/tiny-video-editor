"""Turns the timeline (tracks + clips) into ONE ffmpeg command.

Why it is fast and light:
  * No intermediate files: everything is decoded once and encoded once.
  * Every clip is its own input with -ss/-t, so ffmpeg only reads the parts that are used.
  * Each track is concatenated into one stream; upper video tracks are blended on top only
    while they actually have a clip (overlay with enable=...).
  * The same compositor renders the export, the scrub preview frame and the live playback,
    the last two at preview resolution only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .operations import TEXT_COLORS, TEXT_POSITIONS, TEXT_SIZES
from .project import EPS, Project

IS_WIN = os.name == "nt"

X264_CRF = {"original": 20, "high": 20, "balanced": 23, "small": 28, "tiny": 33}
X265_CRF = {"original": 24, "high": 24, "balanced": 28, "small": 31, "tiny": 35}
HW_Q = {"original": 21, "high": 21, "balanced": 25, "small": 30, "tiny": 35}
AUDIO_KBPS = {"original": 192, "high": 192, "balanced": 160, "small": 112, "tiny": 80}
MP3_Q = {"original": 0, "high": 0, "balanced": 2, "small": 5, "tiny": 7}
X26X_PRESET = {"fastest": "ultrafast", "fast": "veryfast", "slow": "medium"}
HW_ENCODERS = {"nvenc": ("h264_nvenc", "hevc_nvenc"),
               "qsv": ("h264_qsv", "hevc_qsv"),
               "amf": ("h264_amf", "hevc_amf")}
COPY_CONTAINERS = (".mp4", ".mov", ".m4v")
SR = 48000


class BuildError(Exception):
    pass


@dataclass
class ProjectSettings:
    width: int
    height: int
    fps: float
    fade_in: float = 0.0
    fade_out: float = 0.0
    normalize: bool = False


@dataclass
class OutputSettings:
    fmt: str = "mp4"                 # mp4 | mp4_hevc | mp3 | gif
    quality: str = "original"        # original | high | balanced | small | tiny
    target_mb: Optional[float] = None
    speed: str = "fast"              # fastest | fast | slow
    hwaccel: Optional[str] = None    # nvenc | qsv | amf | None
    fast_copy: bool = False
    low_priority: bool = True
    src_vkbps: Optional[int] = None


@dataclass
class BuildResult:
    args: List[str]
    duration: float
    size: Optional[Tuple[int, int]]
    notes: List[str] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)
    uses_hw: bool = False


# ------------------------------------------------------------------ helpers
def _num(x: float) -> str:
    return f"{float(x):.4f}".rstrip("0").rstrip(".") or "0"


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def atempo_chain(f: float) -> List[str]:
    parts = []
    while f > 2.0 + 1e-9:
        parts.append(2.0)
        f /= 2.0
    while f < 0.5 - 1e-9:
        parts.append(0.5)
        f /= 0.5
    parts.append(f)
    return [f"atempo={_num(x)}" for x in parts if abs(x - 1.0) > 1e-6]


def find_font() -> Optional[str]:
    if IS_WIN:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        cands = [os.path.join(windir, "Fonts", n) for n in
                 ("arial.ttf", "segoeui.ttf", "tahoma.ttf", "verdana.ttf")]
    else:
        cands = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/TTF/DejaVuSans.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/Library/Fonts/Arial.ttf"]
    return next((c for c in cands if os.path.isfile(c)), None)


def _filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def preset_filter(key: str, s: int = 5) -> str:
    s = int(s or 5)
    table = {
        "bw": "hue=s=0",
        "sepia": ("colorchannelmixer=rr=.393:rg=.769:rb=.189:gr=.349:gg=.686:gb=.168"
                  ":br=.272:bg=.534:bb=.131"),
        "vintage": "curves=preset=vintage",
        "brighter": f"eq=brightness={0.03 * s:.2f}",
        "darker": f"eq=brightness={-0.03 * s:.2f}",
        "contrast": f"eq=contrast={1 + 0.08 * s:.2f}",
        "vivid": f"eq=saturation={1 + 0.15 * s:.2f}",
        "faded": f"eq=saturation={max(0.0, 1 - 0.09 * s):.2f}",
        "blur": f"boxblur=luma_radius={max(1, s * 2)}:luma_power=1",
        "sharpen": f"unsharp=5:5:{0.4 + s * 0.16:.2f}",
        "denoise": f"hqdn3d={1 + s * 0.8:.1f}",
        "vignette": f"vignette=a={0.25 + s * 0.06:.2f}",
        "negative": "negate",
        "mirror": "hflip",
    }
    if key not in table:
        raise BuildError(f"Unknown effect: {key}")
    return table[key]


def drawtext_filter(p: dict, textfile: str) -> str:
    x, y = TEXT_POSITIONS.get(p.get("position"), TEXT_POSITIONS["Bottom center"])
    size = TEXT_SIZES.get(p.get("size"), TEXT_SIZES["Medium"])
    color = TEXT_COLORS.get(p.get("color"), "white")
    parts = [f"drawtext=textfile='{textfile}'", "expansion=none"]
    font = find_font()
    if font:
        parts.append(f"fontfile='{_filter_path(font)}'")
    parts += [f"fontsize={size}", f"fontcolor={color}", f"x={x}", f"y={y}",
              "borderw=2", "bordercolor=black@0.6"]
    if p.get("box"):
        parts += ["box=1", "boxcolor=black@0.5", "boxborderw=12"]
    return ":".join(parts)


def output_extension(st: OutputSettings, src_path: Optional[str]) -> str:
    if st.fast_copy:
        return os.path.splitext(src_path or "")[1].lower() or ".mp4"
    return {"mp4": ".mp4", "mp4_hevc": ".mp4", "mp3": ".mp3", "gif": ".gif"}.get(st.fmt, ".mp4")


def preview_size(ps: ProjectSettings, box_w: int, box_h: int) -> Tuple[int, int]:
    s = min(box_w / ps.width, box_h / ps.height, 1.0)
    return _even(ps.width * s), _even(ps.height * s)


# ---------------------------------------------------------------- compose
class _Graph:
    def __init__(self):
        self.inputs: List[List[str]] = []
        self.chains: List[str] = []
        self.files: Dict[str, str] = {}
        self._n = 0

    def label(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def add_input(self, path: str, ss: float, dur: float) -> int:
        args = []
        if ss > 0.001:
            args += ["-ss", _num(ss)]
        args += ["-t", _num(dur), "-i", path]
        self.inputs.append(args)
        return len(self.inputs) - 1

    def input_args(self) -> List[str]:
        return [a for arg in self.inputs for a in arg]


def _layout_filters(layout: str, W: int, H: int, alpha: bool) -> List[str]:
    pad_color = "black@0" if alpha else "black"
    if layout == "fit":
        return [f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={pad_color}"]
    if layout.startswith("pip_"):
        bw, bh = _even(W * 0.36), _even(H * 0.36)
        m = max(2, int(min(W, H) * 0.035))
        corner = layout[4:]
        x = f"{m}" if corner.endswith("l") else f"ow-iw-{m}"
        y = f"{m}" if corner.startswith("t") else f"oh-ih-{m}"
        return [f"scale={bw}:{bh}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                f"pad={W}:{H}:{x}:{y}:color={pad_color}"]
    return [f"scale={W}:{H}:force_original_aspect_ratio=increase:force_divisible_by=2",
            f"crop={W}:{H}"]


_ROTATE = {"cw": ["transpose=1"], "ccw": ["transpose=2"], "180": ["hflip", "vflip"],
           "hflip": ["hflip"], "vflip": ["vflip"]}


def _video_track(g: _Graph, project: Project, track, w0, w1, W, H, F, alpha) -> Optional[Tuple[str, list]]:
    pix = "yuva420p" if alpha else "yuv420p"
    segs, active, pos = [], [], w0
    for c in track.sorted():
        cs, ce = max(c.start, w0), min(c.end, w1)
        if ce - cs < 1e-3:
            continue
        if cs - pos > 1e-3:
            segs.append(_video_gap(g, cs - pos, W, H, F, alpha))
        d = ce - cs
        i = g.add_input(c.path, c.src_at(cs), d * c.speed + 0.2)
        f = ["setpts=PTS-STARTPTS"]
        if abs(c.speed - 1) > EPS:
            f.append(f"setpts=PTS/{_num(c.speed)}")
        f.append(f"fps={_num(F)}")
        f += _ROTATE.get(c.rotate or "", [])
        if alpha:
            f.append("format=yuva420p")
        f += _layout_filters(c.layout, W, H, alpha)
        if c.effect:
            f.append(preset_filter(c.effect, c.strength))
        if c.custom.strip():
            f.append(c.custom.strip())
        if c.text and c.text.get("text"):
            name = f"tve_text_{c.id}.txt"
            g.files[name] = c.text["text"]
            f.append(drawtext_filter(c.text, name))
        f += ["setsar=1", f"format={pix}", f"tpad=stop_mode=clone:stop_duration={_num(d)}",
              f"trim=duration={_num(d)}", "setpts=PTS-STARTPTS"]
        lab = g.label("vc")
        g.chains.append(f"[{i}:v:0]" + ",".join(f) + f"[{lab}]")
        segs.append(lab)
        active.append((cs - w0, ce - w0))
        pos = ce
    if not active:
        return None
    if w1 - pos > 1e-3:
        segs.append(_video_gap(g, w1 - pos, W, H, F, alpha))
    return _concat(g, segs, "v"), active


def _video_gap(g: _Graph, d, W, H, F, alpha) -> str:
    lab = g.label("vg")
    col = "black@0" if alpha else "black"
    g.chains.append(f"color=c={col}:s={W}x{H}:r={_num(F)}:d={_num(d)},"
                    f"format={'yuva420p' if alpha else 'yuv420p'},setsar=1[{lab}]")
    return lab


def _audio_track(g: _Graph, project: Project, track, w0, w1) -> Optional[str]:
    segs, pos, any_clip = [], w0, False
    for c in track.sorted():
        cs, ce = max(c.start, w0), min(c.end, w1)
        if ce - cs < 1e-3 or c.muted:
            continue
        if cs - pos > 1e-3:
            segs.append(_audio_gap(g, cs - pos))
        d = ce - cs
        i = g.add_input(c.path, c.src_at(cs), d * c.speed + 0.2)
        f = ["asetpts=PTS-STARTPTS"] + atempo_chain(c.speed)
        if abs(c.volume - 1) > EPS:
            f.append(f"volume={_num(c.volume)}")
        if c.custom.strip():
            f.append(c.custom.strip())
        f += [f"aresample={SR}", "aformat=sample_fmts=fltp:channel_layouts=stereo",
              "apad", f"atrim=duration={_num(d)}", "asetpts=PTS-STARTPTS"]
        lab = g.label("ac")
        g.chains.append(f"[{i}:a:0]" + ",".join(f) + f"[{lab}]")
        segs.append(lab)
        any_clip = True
        pos = ce
    if not any_clip:
        return None
    if w1 - pos > 1e-3:
        segs.append(_audio_gap(g, w1 - pos))
    return _concat(g, segs, "a")


def _audio_gap(g: _Graph, d) -> str:
    lab = g.label("ag")
    g.chains.append(f"anullsrc=r={SR}:cl=stereo,atrim=duration={_num(d)},"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[{lab}]")
    return lab


def _concat(g: _Graph, segs: List[str], kind: str) -> str:
    lab = g.label("t" + kind)
    if len(segs) == 1:
        g.chains.append(f"[{segs[0]}]{'null' if kind == 'v' else 'anull'}[{lab}]")
    else:
        g.chains.append("".join(f"[{s}]" for s in segs) +
                        f"concat=n={len(segs)}:v={1 if kind == 'v' else 0}:"
                        f"a={1 if kind == 'a' else 0}[{lab}]")
    return lab


def _enable(intervals) -> str:
    return "+".join(f"between(t,{_num(a)},{_num(b)})" for a, b in intervals)


def compose(project: Project, ps: ProjectSettings, w0: float, w1: float, W: int, H: int,
            F: float, want_video: bool, want_audio: bool):
    """Build the filter graph for timeline window [w0, w1].

    Returns (graph, video_label or None, audio_label or None).
    """
    g = _Graph()
    T = project.end
    dur = w1 - w0
    vlab = alab = None
    if want_video:
        base = None
        for idx, tr in enumerate(project.video_tracks()):
            if tr.hidden:
                continue
            res = _video_track(g, project, tr, w0, w1, W, H, F, alpha=base is not None)
            if not res:
                continue
            lab, active = res
            if base is None:
                base = lab
            else:
                out = g.label("ov")
                en = _enable(active)
                g.chains.append(f"[{base}][{lab}]overlay=0:0:eof_action=pass:enable='{en}'[{out}]")
                base = out
        if base is None:
            base = _video_gap(g, dur, W, H, F, False)
        f = []
        if ps.fade_in > 0 and w0 < ps.fade_in:
            f.append(f"fade=t=in:st=0:d={_num(ps.fade_in - w0)}")
        if ps.fade_out > 0 and T - ps.fade_out < w1:
            st_ = max(0.0, T - ps.fade_out - w0)
            f.append(f"fade=t=out:st={_num(st_)}:d={_num(min(ps.fade_out, w1 - w0 - st_))}")
        vlab = base
        if f:
            vlab = g.label("vf")
            g.chains.append(f"[{base}]" + ",".join(f) + f"[{vlab}]")
    if want_audio:
        labs = [lab for tr in project.audio_tracks() if not tr.hidden
                for lab in [_audio_track(g, project, tr, w0, w1)] if lab]
        if labs:
            if len(labs) == 1:
                mix = labs[0]
            else:
                mix = g.label("mix")
                g.chains.append("".join(f"[{x}]" for x in labs) +
                                f"amix=inputs={len(labs)}:duration=longest:normalize=0[{mix}]")
            f = []
            if ps.normalize:
                f.append("dynaudnorm")
            if ps.fade_in > 0 and w0 < ps.fade_in:
                f.append(f"afade=t=in:st=0:d={_num(ps.fade_in - w0)}")
            if ps.fade_out > 0 and T - ps.fade_out < w1:
                st_ = max(0.0, T - ps.fade_out - w0)
                f.append(f"afade=t=out:st={_num(st_)}:d={_num(min(ps.fade_out, w1 - w0 - st_))}")
            f.append(f"atrim=duration={_num(dur)}")
            alab = g.label("af")
            g.chains.append(f"[{mix}]" + ",".join(f) + f"[{alab}]")
    return g, vlab, alab


FFMPEG_MAJOR = 8   # set by the app from the FFmpeg it uses


def _graph_args(g: _Graph, extra_chain: str = "") -> List[str]:
    graph = ";".join(g.chains + ([extra_chain] if extra_chain else []))
    if len(graph) > 8000:      # keep the Windows command line short: read graph from a file
        g.files["tve_graph.txt"] = graph
        if FFMPEG_MAJOR >= 7:
            return ["-/filter_complex", "tve_graph.txt"]
        return ["-filter_complex_script", "tve_graph.txt"]
    return ["-filter_complex", graph]


def _base_args(progress: bool = True) -> List[str]:
    a = ["-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if progress:
        a += ["-nostats", "-progress", "pipe:1"]
    return a


def _check(project: Project):
    if project.end < 0.05:
        raise BuildError("The timeline is empty. Add or restore at least one clip.")


# ------------------------------------------------------------ preview
def build_frame(project: Project, ps: ProjectSettings, t: float, box_w: int, box_h: int):
    """Command that writes one composed PNG frame (preview resolution) to stdout."""
    if project.end < 0.05:
        return None
    W, H = preview_size(ps, box_w, box_h)
    t = max(0.0, min(t, max(0.0, project.end - 0.5 / max(ps.fps, 1))))
    g, v, _ = compose(project, ps, t, t + 2.0 / max(ps.fps, 1), W, H, ps.fps, True, False)
    args = _base_args(False) + g.input_args() + _graph_args(g, f"[{v}]format=rgb24[pout]")
    args += ["-map", "[pout]", "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"]
    return args, g.files


def build_play(project: Project, ps: ProjectSettings, t0: float, box_w: int, box_h: int,
               fps_cap: float = 30.0):
    """Commands for live playback from t0: raw RGB video frames + audio (nut/pcm)."""
    T = project.end
    if T - t0 < 0.05:
        return None
    W, H = preview_size(ps, box_w, box_h)
    F = min(ps.fps, fps_cap)
    gv, v, _ = compose(project, ps, t0, T, W, H, F, True, False)
    vargs = _base_args(False) + gv.input_args() + _graph_args(gv, f"[{v}]format=rgb24[pout]")
    vargs += ["-map", "[pout]", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    ga, _, a = compose(project, ps, t0, T, W, H, F, False, True)
    aargs = None
    if a:
        aargs = _base_args(False) + ga.input_args() + _graph_args(ga)
        aargs += ["-map", f"[{a}]", "-c:a", "pcm_s16le", "-f", "nut", "pipe:1"]
    files = dict(gv.files)
    files.update(ga.files)
    return vargs, aargs, W, H, F, T - t0, files


# ------------------------------------------------------------- export
def build_export(project: Project, ps: ProjectSettings, st: OutputSettings, out_path: str,
                 preview_seconds: Optional[float] = None) -> BuildResult:
    _check(project)
    if st.fast_copy:
        return _build_copy(project, ps, out_path, preview_seconds)
    T = project.end
    w1 = min(T, preview_seconds) if preview_seconds else T
    notes: List[str] = []
    W, H, F = ps.width, ps.height, ps.fps
    has_audio = any(c.kind == "audio" and not c.muted
                    for tr in project.audio_tracks() if not tr.hidden for c in tr.clips)
    if not any(c.kind == "video" for c in project.all_clips()) and st.fmt not in ("mp3",):
        notes.append("There is no video clip on the timeline; the video will be black.")

    args = _base_args()
    size = None
    uses_hw = False
    dur = w1

    if st.fmt == "mp3":
        if not has_audio:
            raise BuildError("There is no audio on the timeline, so an MP3 cannot be made.")
        g, _, a = compose(project, ps, 0.0, w1, W, H, F, False, True)
        args += g.input_args() + _graph_args(g) + ["-map", f"[{a}]", "-c:a", "libmp3lame"]
        if st.target_mb:
            kbps = int(st.target_mb * 8192 / max(dur, 0.1))
            if kbps < 32:
                raise BuildError(f"The size limit is too small. Use at least "
                                 f"{32 * dur / 8192:.1f} MB.")
            args += ["-b:a", f"{min(320, kbps)}k"]
        else:
            args += ["-q:a", str(MP3_Q[st.quality])]

    elif st.fmt == "gif":
        gw, gh = (W, H)
        if max(W, H) > 480:
            s = 480 / max(W, H)
            gw, gh = _even(W * s), _even(H * s)
        gf = min(F, 12)
        g, v, _ = compose(project, ps, 0.0, w1, gw, gh, gf, True, False)
        pal = (f"[{v}]split[pa][pb];[pa]palettegen=stats_mode=single:max_colors=256[pp];"
               f"[pb][pp]paletteuse=new=1[gout]")
        args += g.input_args() + _graph_args(g, pal) + ["-map", "[gout]", "-loop", "0"]
        size = (gw, gh)
        if dur > 30:
            notes.append("Long GIFs get very large. Keep it under 30 seconds if you can.")
        if st.target_mb:
            notes.append("A size limit cannot be applied to GIF; shorten it or lower the "
                         "resolution instead.")
    else:
        g, v, a = compose(project, ps, 0.0, w1, W, H, F, True, has_audio)
        hevc = st.fmt == "mp4_hevc"
        hw = st.hwaccel if st.hwaccel in HW_ENCODERS else None
        pix = "nv12" if hw in ("qsv", "amf") else "yuv420p"
        args += g.input_args() + _graph_args(g, f"[{v}]format={pix}[vout]") + ["-map", "[vout]"]
        if a:
            args += ["-map", f"[{a}]"]
        audio_k = AUDIO_KBPS[st.quality]
        vkbps = None
        if st.target_mb:
            total_k = st.target_mb * 8192 / max(dur, 0.1) * 0.96
            if a:
                audio_k = 128 if total_k > 900 else (96 if total_k > 400 else 64)
            vkbps = int(total_k - (audio_k if a else 0))
            if vkbps < 80:
                need = (80 + (audio_k if a else 0)) * dur / 8192 / 0.96
                raise BuildError(f"The size limit is too small for this length.\n"
                                 f"Use at least about {need:.1f} MB or make the video shorter.")
        if hw:
            uses_hw = True
            args += ["-c:v", HW_ENCODERS[hw][1 if hevc else 0]]
            if vkbps:
                args += ["-b:v", f"{vkbps}k", "-maxrate", f"{int(vkbps * 1.5)}k",
                         "-bufsize", f"{vkbps * 2}k"]
                if hw == "amf":
                    args += ["-rc", "vbr_peak"]
            else:
                q = HW_Q[st.quality]
                if hw == "nvenc":
                    args += ["-rc", "vbr", "-cq", str(q), "-b:v", "0"]
                elif hw == "qsv":
                    args += ["-global_quality", str(q)]
                else:
                    args += ["-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2)]
            if hw == "nvenc":
                args += ["-preset", "fast" if st.speed != "slow" else "slow"]
        else:
            if hevc:
                args += ["-c:v", "libx265", "-preset", X26X_PRESET[st.speed],
                         "-x265-params", "log-level=error"]
            else:
                args += ["-c:v", "libx264", "-preset", X26X_PRESET[st.speed]]
            if vkbps:
                args += ["-b:v", f"{vkbps}k", "-maxrate", f"{int(vkbps * 1.5)}k",
                         "-bufsize", f"{vkbps * 2}k"]
            else:
                args += ["-crf", str((X265_CRF if hevc else X264_CRF)[st.quality])]
                if st.quality == "original" and st.src_vkbps:
                    cap = max(300, int(st.src_vkbps * 1.05))
                    args += ["-maxrate", f"{cap}k", "-bufsize", f"{cap * 2}k"]
        if hevc:
            args += ["-tag:v", "hvc1"]
            if not hw and st.speed != "fastest":
                notes.append("H.265 makes smaller files but is slow on weak computers.")
        if a:
            args += ["-c:a", "aac", "-b:a", f"{audio_k}k"]
        args += ["-movflags", "+faststart"]
        size = (W, H)
    args.append(out_path)
    return BuildResult(args=args, duration=dur, size=size, notes=notes, files=g.files,
                       uses_hw=uses_hw)


def copy_segments(project: Project, ps: ProjectSettings):
    """If the timeline is only cuts of the main video, return its (in, out) list, else None."""
    main = project.main_path
    info = project.media.get(main)
    if not info:
        return None
    vt, at = project.video_tracks(), project.audio_tracks()
    if any(t.clips for t in vt[1:] + at[1:]) or not vt or not vt[0].clips:
        return None
    if (ps.width, ps.height) != (info.width, info.height) or abs(ps.fps - info.fps) > 0.01 \
            or ps.fade_in or ps.fade_out or ps.normalize or vt[0].hidden:
        return None
    vclips = vt[0].sorted()
    aclips = at[0].sorted() if at else []
    pos = 0.0
    for c in vclips:
        if c.path != main or c.has_edits() or abs(c.start - pos) > 0.02:
            return None
        pos = c.end
    if info.has_audio:
        if len(aclips) != len(vclips) or (at and at[0].hidden):
            return None
        for v, a in zip(vclips, aclips):
            if a.path != main or a.has_edits() or abs(a.start - v.start) > 0.02 \
                    or abs(a.src_in - v.src_in) > 0.02 or abs(a.src_out - v.src_out) > 0.02:
                return None
    elif aclips:
        return None
    return [(c.src_in, c.src_out) for c in vclips]


def _build_copy(project, ps, out_path, preview_seconds) -> BuildResult:
    segs = copy_segments(project, ps)
    if segs is None:
        raise BuildError("“Fast cut” can only cut and join pieces of the original video. "
                         "Turn it off to use effects, text, speed, extra tracks or project "
                         "settings.")
    args = _base_args()
    files: Dict[str, str] = {}
    main = project.main_path
    if len(segs) == 1:
        s, e = segs[0]
        if s > 0.001:
            args += ["-ss", _num(s)]
        args += ["-t", _num(e - s), "-i", main]
    else:
        path = os.path.abspath(main).replace("\\", "/").replace("'", "'\\''")
        lines = ["ffconcat version 1.0"]
        for s, e in segs:
            lines += [f"file '{path}'", f"inpoint {_num(s)}", f"outpoint {_num(e)}"]
        files["tve_concat.txt"] = "\n".join(lines) + "\n"
        args += ["-f", "concat", "-safe", "0", "-i", "tve_concat.txt"]
    args += ["-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    if os.path.splitext(out_path)[1].lower() in COPY_CONTAINERS:
        args += ["-movflags", "+faststart"]
    if preview_seconds:
        args += ["-t", _num(preview_seconds)]
    args.append(out_path)
    dur = sum(e - s for s, e in segs)
    if preview_seconds:
        dur = min(dur, preview_seconds)
    info = project.media[main]
    return BuildResult(args=args, duration=dur, size=(info.width, info.height),
                       notes=["Fast cut does not re-encode, so cut points may shift by a "
                              "second or two to the nearest keyframe."], files=files)
