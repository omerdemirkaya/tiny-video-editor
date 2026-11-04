"""Builds Tiny Video Editor for Windows and packs it for the Microsoft Store.

Usage (on Windows):
    pip install pyinstaller
    python build.py                 # output: dist/release/

Outputs:
    TinyVideoEditor-windows-x64.zip              (unzip and run TinyVideoEditor.exe)
    TinyVideoEditor-microsoft-store-upload.msix  (only for uploading to Partner Center)

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
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
              "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip")

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


def version():
    src = (ROOT / "tve" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', src).group(1)


def run(cmd):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=ROOT)


# ------------------------------------------------------------------ FFmpeg
def ensure_ffmpeg():
    exe = FFMPEG_DIR / "bin" / "ffmpeg.exe"
    if exe.is_file() and (FFMPEG_DIR / "bin" / "ffplay.exe").is_file():
        print("FFmpeg found:", exe)
        return
    print("Downloading FFmpeg:", FFMPEG_URL, flush=True)
    DIST.mkdir(parents=True, exist_ok=True)
    zpath = DIST / "ffmpeg.zip"
    with urllib.request.urlopen(FFMPEG_URL) as r, open(zpath, "wb") as fh:
        shutil.copyfileobj(r, fh)
    tmp = DIST / "ffmpeg-unpacked"
    shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(tmp)
    top = next(p for p in tmp.iterdir() if p.is_dir())
    shutil.rmtree(FFMPEG_DIR, ignore_errors=True)
    (FFMPEG_DIR / "bin").mkdir(parents=True)
    for f in (top / "bin").iterdir():
        shutil.copy2(f, FFMPEG_DIR / "bin" / f.name)
    shutil.copy2(top / "LICENSE.txt", FFMPEG_DIR / "LICENSE.txt")
    (FFMPEG_DIR / "README.txt").write_text(
        "FFmpeg 8.1 (Windows 64-bit, GPL, shared build) from https://github.com/BtbN/FFmpeg-Builds\n"
        "Used by Tiny Video Editor for all video and audio processing.\n"
        "License: see LICENSE.txt (GPL). Source code: https://ffmpeg.org/download.html\n",
        encoding="utf-8")
    for f in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):
        if not (FFMPEG_DIR / "bin" / f).is_file():
            raise SystemExit(f"{f} is missing from the FFmpeg download")


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
           "--specpath", ROOT / "build", "--icon", ROOT / "assets" / "app.ico",
           "--version-file", windows_version_file(ver),
           "--exclude-module", "PIL", "--exclude-module", "numpy"]
    for size in (32, 64, 256):
        cmd += ["--add-data", f"{ROOT / 'assets' / f'icon-{size}.png'}{sep}assets"]
    cmd.append(ROOT / "main.py")
    run(cmd)
    app = DIST / "app" / NAME
    shutil.copytree(FFMPEG_DIR, app / "ffmpeg")      # next to the exe: the app finds it there
    return app


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
    exe = app / "ffmpeg" / "bin" / "ffmpeg.exe"
    subprocess.run([str(exe), "-hide_banner", "-version"], check=True, capture_output=True)
    print("FFmpeg inside the build works.")


def build():
    if sys.platform != "win32":
        raise SystemExit("Please run build.py on Windows (GitHub Actions does this for you).")
    ver = version()
    shutil.rmtree(DIST / "app", ignore_errors=True)
    shutil.rmtree(DIST / "msix", ignore_errors=True)
    shutil.rmtree(RELEASE, ignore_errors=True)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    RELEASE.mkdir(parents=True, exist_ok=True)
    ensure_ffmpeg()
    app = pyinstaller(ver)
    smoke_test(app)
    outputs = [package_zip(app)]
    msix = package_msix(ver, app)
    if msix:
        outputs.append(msix)
    print()
    for out in outputs:
        print(f"Ready: {out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build()
