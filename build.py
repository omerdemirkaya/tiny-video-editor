"""Builds Tiny Video Editor for Windows (+ Microsoft Store) and Linux.

Usage (run on the system you build for):
    pip install pyinstaller
    python build.py                 # output: dist/release/

Outputs:
    Windows: TinyVideoEditor-windows-x64.zip              (unzip and run TinyVideoEditor.exe)
             TinyVideoEditor-microsoft-store-upload.msix  (only for uploading to Partner Center)
    Linux:   TinyVideoEditor-linux-x64.tar.gz             (unpack, run ./install.sh or ./TinyVideoEditor)

FFmpeg is not stored in git (its DLLs are bigger than GitHub allows); if ffmpeg/bin is missing
it is downloaded from the pinned FFmpeg build below. The MSIX step needs the Windows SDK
(it is preinstalled on GitHub's Windows machines). The package is unsigned: the Microsoft
Store signs it after certification.
"""
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

ROOT = Path(__file__).resolve().parent
NAME = "TinyVideoEditor"
DIST = ROOT / "dist"
RELEASE = DIST / "release"
FFMPEG_DIR = ROOT / "ffmpeg"
IS_WIN = sys.platform == "win32"
EXE = ".exe" if IS_WIN else ""
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/" +
              ("ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip" if IS_WIN
               else "ffmpeg-n8.1-latest-linux64-gpl-shared-8.1.tar.xz"))

# Microsoft Store (Partner Center -> Product management -> Product identity)
STORE = {
    "identity_name": "Startiux.TinyVideoEditor",
    "publisher": "CN=50FDEF67-1E39-4BBB-BE9A-4A4EB54AAE09",
    "publisher_display_name": "Startiux",
    "display_name": "Tiny Video Editor",
    "description": "A light, multi-track video editor: cut, combine, add effects and shrink videos.",
    "languages": ["en-us", "tr-tr"],
}
MSIX_FILE = f"{NAME}-microsoft-store-upload.msix"
ZIP_FILE = f"{NAME}-windows-x64.zip"
LINUX_FILE = f"{NAME}-linux-x64.tar.gz"


def version():
    src = (ROOT / "tve" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', src).group(1)


def run(cmd):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


# ------------------------------------------------------------------ FFmpeg
def ensure_ffmpeg():
    exe = FFMPEG_DIR / "bin" / f"ffmpeg{EXE}"
    if exe.is_file() and (FFMPEG_DIR / "bin" / f"ffplay{EXE}").is_file():
        print("FFmpeg found:", exe)
        return
    print("Downloading FFmpeg:", FFMPEG_URL, flush=True)
    DIST.mkdir(parents=True, exist_ok=True)
    zpath = DIST / ("ffmpeg.zip" if IS_WIN else "ffmpeg.tar.xz")
    with urllib.request.urlopen(FFMPEG_URL) as r, open(zpath, "wb") as fh:
        shutil.copyfileobj(r, fh)
    tmp = DIST / "ffmpeg-unpacked"
    shutil.rmtree(tmp, ignore_errors=True)
    if IS_WIN:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(zpath) as t:
            t.extractall(tmp, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    top = next(p for p in tmp.iterdir() if p.is_dir())
    shutil.rmtree(FFMPEG_DIR, ignore_errors=True)
    (FFMPEG_DIR / "bin").mkdir(parents=True)
    for f in (top / "bin").iterdir():
        shutil.copy2(f, FFMPEG_DIR / "bin" / f.name)
    if not IS_WIN:
        # shared build: binaries find their libraries through RPATH=$ORIGIN/../lib
        (FFMPEG_DIR / "lib").mkdir()
        for f in (top / "lib").iterdir():
            if f.name.endswith(".so") or f.is_dir():
                continue                                   # dev symlinks / pkgconfig not needed
            if f.is_symlink():
                os.symlink(os.readlink(f), FFMPEG_DIR / "lib" / f.name)
            else:
                shutil.copy2(f, FFMPEG_DIR / "lib" / f.name)
    shutil.copy2(top / "LICENSE.txt", FFMPEG_DIR / "LICENSE.txt")
    (FFMPEG_DIR / "README.txt").write_text(
        f"FFmpeg 8.1 ({'Windows' if IS_WIN else 'Linux'} 64-bit, GPL, shared build) from https://github.com/BtbN/FFmpeg-Builds\n"
        "Used by Tiny Video Editor for all video and audio processing.\n"
        "License: see LICENSE.txt (GPL). Source code: https://ffmpeg.org/download.html\n",
        encoding="utf-8")
    for f in ("ffmpeg", "ffprobe", "ffplay"):
        path = FFMPEG_DIR / "bin" / f"{f}{EXE}"
        if not path.is_file():
            raise SystemExit(f"{path.name} is missing from the FFmpeg download")
        if not IS_WIN:
            path.chmod(0o755)


# --------------------------------------------------------------- PyInstaller
def windows_version_file(ver):
    nums = (ver.split(".") + ["0", "0", "0"])[:4]
    tup = ", ".join(n if n.isdigit() else "0" for n in nums)
    path = DIST / "version_info.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup})),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{STORE["publisher_display_name"]}'),
      StringStruct('ProductName', '{STORE["display_name"]}'),
      StringStruct('FileDescription', '{STORE["display_name"]}'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('ProductVersion', '{ver}'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('LegalCopyright', '{STORE["publisher_display_name"]}')])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
""", encoding="utf-8")
    return path


def pyinstaller(ver):
    sep = os.pathsep
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--onedir", "--name", NAME, "--distpath", DIST / "app", "--workpath", ROOT / "build",
           "--specpath", ROOT / "build",
           "--exclude-module", "PIL", "--exclude-module", "numpy"]
    if IS_WIN:
        cmd += ["--icon", ROOT / "assets" / "app.ico", "--version-file", windows_version_file(ver)]
    for size in (32, 64, 256):
        cmd += ["--add-data", f"{ROOT / 'assets' / f'icon-{size}.png'}{sep}assets"]
    cmd.append(ROOT / "main.py")
    run(cmd)
    app = DIST / "app" / NAME
    shutil.copytree(FFMPEG_DIR, app / "ffmpeg", symlinks=True)   # next to the exe: the app finds it there
    return app


def package_linux(app):
    """tar.gz with the app folder, an icon and an install.sh that adds a menu entry."""
    folder = f"{NAME}-linux-x64"
    stage = DIST / folder
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(app, stage, symlinks=True)
    shutil.copy2(ROOT / "assets" / "icon-256.png", stage / "tiny-video-editor.png")
    install = stage / "install.sh"
    install.write_text("""#!/bin/sh
# Installs Tiny Video Editor for the current user (no admin rights needed) and adds it to the menu.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/tiny-video-editor"
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons"
rm -rf "$DEST"
cp -r "$DIR" "$DEST"
ln -sf "$DEST/TinyVideoEditor" "$HOME/.local/bin/tiny-video-editor"
cp "$DIR/tiny-video-editor.png" "$HOME/.local/share/icons/tiny-video-editor.png"
cat > "$HOME/.local/share/applications/tiny-video-editor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tiny Video Editor
Comment=Cut, combine and shrink videos
Exec="$DEST/TinyVideoEditor" %f
Icon=$HOME/.local/share/icons/tiny-video-editor.png
Categories=AudioVideo;Video;AudioVideoEditing;
MimeType=video/mp4;video/x-matroska;video/quicktime;video/webm;video/x-msvideo;
Terminal=false
EOF
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
echo "Tiny Video Editor is installed. Open it from your applications menu."
""", encoding="utf-8")
    install.chmod(0o755)
    (stage / NAME).chmod(0o755)
    out = RELEASE / LINUX_FILE
    with tarfile.open(out, "w:gz") as tar:
        tar.add(stage, arcname=folder)
    return out


def package_zip(app):
    out = RELEASE / ZIP_FILE
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in app.rglob("*"):
            if f.is_file():
                z.write(f, Path(NAME) / f.relative_to(app))
    return out


# ------------------------------------------------------------- Microsoft Store
def msix_version(ver):
    """MSIX versions have four parts; the Store requires the last one to be 0."""
    nums = [int(n) if n.isdigit() else 0 for n in re.split(r"[.\-+]", ver)[:3]]
    nums += [0] * (3 - len(nums))
    return ".".join(map(str, nums)) + ".0"


def appx_manifest(ver):
    s = {k: escape(v) if isinstance(v, str) else v for k, v in STORE.items()}
    langs = "\n".join(f'    <Resource Language="{lang}" />' for lang in STORE["languages"])
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap rescap">
  <Identity Name="{s['identity_name']}" Publisher="{s['publisher']}"
            Version="{msix_version(ver)}" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>{s['display_name']}</DisplayName>
    <PublisherDisplayName>{s['publisher_display_name']}</PublisherDisplayName>
    <Description>{s['description']}</Description>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Resources>
{langs}
  </Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
  <Applications>
    <Application Id="TinyVideoEditor" Executable="{NAME}.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="{s['display_name']}" Description="{s['description']}"
        BackgroundColor="transparent"
        Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png" />
    </Application>
  </Applications>
</Package>
"""


def sdk_tool(name):
    base = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    found = []
    for p in base.glob(f"10.*/x64/{name}"):
        try:
            found.append((tuple(int(x) for x in p.parent.parent.name.split(".")), p))
        except ValueError:
            continue
    if found:
        return max(found)[1]
    w = shutil.which(name)
    return Path(w) if w else None


def build_pri(makepri, stage):
    work = DIST / "pri"
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(stage / "Assets", work / "Assets")
    shutil.copy2(stage / "AppxManifest.xml", work / "AppxManifest.xml")
    cfg = DIST / "priconfig.xml"
    run([makepri, "createconfig", "/cf", cfg, "/dq", "en-US", "/pv", "10.0.0", "/o"])
    run([makepri, "new", "/pr", work, "/cf", cfg, "/mn", work / "AppxManifest.xml",
         "/of", stage / "resources.pri", "/o"])


def package_msix(ver, app):
    makeappx = sdk_tool("makeappx.exe")
    if not makeappx:
        msg = "makeappx.exe not found (Windows SDK missing); skipped the MSIX package."
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise SystemExit(msg)
        print("WARNING:", msg)
        return None
    stage = DIST / "msix"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(app, stage)
    shutil.copytree(ROOT / "assets" / "msix", stage / "Assets")
    (stage / "AppxManifest.xml").write_text(appx_manifest(ver), encoding="utf-8")
    makepri = sdk_tool("makepri.exe")
    if makepri:
        try:
            build_pri(makepri, stage)
        except subprocess.CalledProcessError as e:
            print("WARNING: resources.pri could not be made, using the default images:", e)
            (stage / "resources.pri").unlink(missing_ok=True)
    out = RELEASE / MSIX_FILE
    run([makeappx, "pack", "/d", stage, "/p", out, "/o"])
    return out


def smoke_test(app):
    """Make sure the packed app can find and run its FFmpeg."""
    exe = app / "ffmpeg" / "bin" / f"ffmpeg{EXE}"
    subprocess.run([str(exe), "-hide_banner", "-version"], check=True, capture_output=True)
    print("FFmpeg inside the build works.")


def build():
    if sys.platform not in ("win32", "linux"):
        raise SystemExit("Please run build.py on Windows or Linux (GitHub Actions does this for you).")
    ver = version()
    shutil.rmtree(DIST / "app", ignore_errors=True)
    shutil.rmtree(DIST / "msix", ignore_errors=True)
    shutil.rmtree(RELEASE, ignore_errors=True)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    RELEASE.mkdir(parents=True, exist_ok=True)
    ensure_ffmpeg()
    app = pyinstaller(ver)
    smoke_test(app)
    if IS_WIN:
        outputs = [package_zip(app)]
        msix = package_msix(ver, app)
        if msix:
            outputs.append(msix)
    else:
        outputs = [package_linux(app)]
    print()
    for out in outputs:
        print(f"Ready: {out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build()
