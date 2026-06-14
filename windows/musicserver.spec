# PyInstaller spec for aMusicServer (Windows).
#
# Run from the windows/ subdirectory:
#     pyinstaller --clean musicserver.spec
#
# Output: ..\dist\aMusicServer\aMusicServer.exe (+ supporting files).
# That folder is then bundled by installer.iss into Setup_aMusicServer_vX.Y.Z.exe.

# ruff: noqa
# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

HERE = Path(SPECPATH).resolve()      # windows/
ROOT = HERE.parent                   # repo root

# Project assets to ship next to the .exe so the existing server.py / scripts
# can find them via their relative path conventions (APP_ROOT/sWebExt/... etc).
datas = [
    (str(ROOT / "sWebExt"),             "sWebExt"),
    (str(ROOT / "web"),                 "web"),
    (str(ROOT / "scripts"),             "scripts"),
    (str(ROOT / "config.example.json"), "."),
    (str(ROOT / "ffmpeg.exe"),          "."),
    (str(HERE / "icon.ico"),            "windows"),
    (str(HERE / "version.py"),          "windows"),
    (str(HERE / "updater.py"),          "windows"),
]

# yt_dlp ships hundreds of extractor modules that are imported by name at
# runtime — pull them all in so SoundCloud/YouTube/etc. still work in the
# frozen build.
hiddenimports = (
    collect_submodules("yt_dlp")
    + collect_submodules("eyed3")
    + collect_submodules("selenium")
    # First-party packages — several are imported lazily inside functions in
    # server.py (e.g. `from insights import db`), so list them explicitly so
    # PyInstaller's static analysis can't miss a deferred import.
    + collect_submodules("insights")
    + collect_submodules("discover")
    + collect_submodules("lastfm")
    + collect_submodules("library")
    + collect_submodules("follow")
    + collect_submodules("share")
    + collect_submodules("soundcloud")
    + collect_submodules("spotify")
    + ["winreg", "PIL.Image", "pystray._win32"]
)

datas += collect_data_files("yt_dlp")
datas += collect_data_files("eyed3")
datas += collect_data_files("certifi")

a = Analysis(
    [str(HERE / "tray_app.py")],
    pathex=[str(ROOT), str(HERE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # librosa + its analysis stack are an optional local-feature extra and are
    # never bundled — insights uses AcousticBrainz in the exe. Excluded so a
    # build host that happens to have them installed can't bloat the binary.
    excludes=["tkinter", "matplotlib", "pytest", "IPython",
              "librosa", "numba", "llvmlite", "scipy", "sklearn", "scikit_learn"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="aMusicServer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # tray app, no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(HERE / "icon.ico"),
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="aMusicServer",
)
