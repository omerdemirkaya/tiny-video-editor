# Tiny Video Editor

A small multi-track video editor written in Python + Tkinter. All heavy work is done by
**FFmpeg**, so it stays light enough for weak computers.

## Setup

1. **Python 3.8+** (the python.org installer for Windows includes Tkinter). No pip packages needed.
2. **FFmpeg** comes with the program in the `ffmpeg` folder (FFmpeg 8.1 from
   https://github.com/BtbN/FFmpeg-Builds — license in `ffmpeg/LICENSE.txt`). The program always
   uses it, whatever is installed on the computer. `build.py` downloads it if the folder is missing.

## Run

Double-click `baslat.bat`, or:

```
python main.py
python main.py "C:\Videos\clip.mp4"
```

## How it works

1. The app opens with a single **Choose a video** screen. After choosing, the editor opens:
   the picture is on track **V1**, its sound on track **A1** (separate, linked clips).
2. **Play** (Space) plays the *edited* result from the playhead, with effects, layers and sound.
3. **Range [ ]** – drag on the range bar above the tracks to select a part. Drag the `[` / `]`
   handles to resize it, drag the middle to move it, double-click to clear.
4. **S** splits clips: at the playhead, or at both ends of the range (that part becomes its own,
   selected clip). The **?** next to *Split* explains this.
5. Select a clip and use the **Clip** panel to change only that piece: effect + strength,
   rotation, layout (fill / fit / small picture-in-picture), speed, text, volume, mute, or a
   custom FFmpeg filter.
6. On the timeline: drag clips to move them (they snap to edges and the playhead), drag clip edges
   to trim, **Delete** removes and closes the gap (Shift+Delete keeps the gap), right-click for more.
   Alt+click selects only the video or only the sound; Ctrl+click selects several.
7. **Drag & drop**: drop video or audio files from a folder onto a track (or anywhere in the
   window). The app asks before adding them; tick *Don't ask me again* to skip the question
   (⚙ → *Ask before adding dropped files* turns it back on). Dropping a video on the start screen
   opens it.
8. **+ Add media**: another video on a new layer (V2, V3), a video joined to the end, or music /
   sound on a new audio track (A2, A3). Track headers: ◉ hide a video track, ♪ mute an audio
   track, × (or right-click) remove a track. Tracks that become empty are removed automatically.
   **Reorder tracks** by dragging a track name (⋮⋮ V2) up or down — e.g. drag V2 onto V1 to make
   it the bottom layer — or right-click it → *Move track up / down*. Clips can also be dragged up
   or down onto another track of the same kind (where it has room).
9. **Project** panel: resolution, aspect ratio (crops from the center), frame rate, fade in/out,
   sound leveling — all pre-filled with your video's own values.
10. **Export video…**: MP4 (H.264 / H.265), MP3 or GIF, quality, optional size limit in MB,
   *Fast cut* (no re-encoding, when you only cut the original video), graphics card
   acceleration, low priority. The suggested file name is `new_video` (`new_video_2`, … if taken).

Undo / redo: Ctrl+Z / Ctrl+Y.

## Why it is light

- **One pass:** the whole timeline is turned into a single FFmpeg command. No temporary videos;
  every source is decoded once and the result encoded once.
- Each clip only reads the part it uses (`-ss` / `-t` per clip); upper layers are only blended
  while they actually have a clip.
- Preview frames and live playback are rendered at preview size only; frames are dropped instead
  of lagging when the computer is slow.
- Timeline thumbnails decode keyframes only; waveforms are computed at a tiny sample rate.
- Export runs at low priority, uses the graphics card if it works (falls back to the processor
  automatically), and *Fast cut* finishes in seconds.

## Building, releases and the Microsoft Store

`build.py` builds for the system it runs on and downloads the matching FFmpeg build into `ffmpeg/`
if it is missing (FFmpeg is not kept in git because its libraries are larger than GitHub allows):

```
pip install pyinstaller
python build.py
```

| System | Output (`dist/release/`) | What it is |
|---|---|---|
| Windows | `TinyVideoEditor-windows-x64.zip` | Unzip, run `TinyVideoEditor.exe` |
| Windows | `TinyVideoEditor-microsoft-store-upload.msix` | **Only** for Partner Center (unsigned; the Store signs it) |
| Linux | `TinyVideoEditor-linux-x64.tar.gz` | Unpack, run `./install.sh` (adds a menu entry) or `./TinyVideoEditor` |

GitHub Actions (`.github/workflows/build.yml`) builds Windows and Linux together:

- **Publishing a new version:** bump `__version__` in `tve/__init__.py` (the Store requires a
  higher version every time), then on GitHub go to **Releases → Draft a new release**, type a tag
  such as `v1.0.1`, choose *Create new tag*, give it a title and press **Publish release**.
  About 10 minutes later the Windows `.zip` and the Linux `.tar.gz` are attached to that release.
- **Store package only / test build:** **Actions → Build Tiny Video Editor → Run workflow**.
  All files, including the `.msix`, go to a private draft release named `store-upload`.

### Download links for the website

```
Windows (Microsoft Store): https://apps.microsoft.com/detail/9PDK46Z2RHDS
Linux (always the newest release):
https://github.com/omerdemirkaya/tiny-video-editor/releases/latest/download/TinyVideoEditor-linux-x64.tar.gz
Windows without the Store (optional):
https://github.com/omerdemirkaya/tiny-video-editor/releases/latest/download/TinyVideoEditor-windows-x64.zip
```

### Installing on Linux

```
tar -xzf TinyVideoEditor-linux-x64.tar.gz
cd TinyVideoEditor-linux-x64
./install.sh
```

Then open **Tiny Video Editor** from the applications menu. Works on 64-bit Linux with glibc 2.35 or
newer (Ubuntu 22.04+, Debian 12+, Fedora 36+, Linux Mint 21+). Drag & drop from the file manager is
only available on Windows; on Linux use *Open another video* / *Add media*.

Listing texts and screenshots are in `store/`; the package identity is in the `STORE` block of
`build.py`.

## Files

| File | Purpose |
|---|---|
| `main.py` | Starter |
| `tve/ui.py` | Welcome screen, editor window, inspector, export dialog, playback control |
| `tve/timeline.py` | Multi-track timeline widget (range, clips, drag / trim, zoom) |
| `tve/project.py` | Tracks, clips, split / delete / undo snapshots |
| `tve/builder.py` | Turns the timeline into one FFmpeg command (export, preview frame, playback) |
| `tve/player.py` | Live preview playback (video frames + sound via ffplay) |
| `tve/ffmpeg_utils.py` | Finding FFmpeg, probing, thumbnails, waveforms, GPU detection, background jobs |
| `tve/operations.py` | Effect presets and choice lists |
| `tve/dnd.py` | Drag & drop from Windows Explorer (no extra packages) |
| `tve/dialogs.py`, `tve/theme.py` | Small forms; dark theme, tooltips, scrolling panels, logo |
| `ffmpeg/` | FFmpeg 8.1 (ffmpeg, ffprobe, ffplay + libraries), downloaded by `build.py` |
| `tve/timeutil.py` | Time formatting |


## Privacy policy

Tiny Video Editor does not collect, store or transmit any personal data. It never connects to the
internet: your videos are only read from and written to folders on your own computer, settings are
kept in a local file, and no information is sent to the developer or to any third party.

Tiny Video Editor hiçbir kişisel veri toplamaz, saklamaz veya iletmez. İnternete bağlanmaz;
videolarınız yalnızca kendi bilgisayarınızdaki klasörlerden okunur ve oraya kaydedilir, ayarlar
yerel bir dosyada tutulur ve geliştiriciye ya da üçüncü taraflara hiçbir bilgi gönderilmez.
