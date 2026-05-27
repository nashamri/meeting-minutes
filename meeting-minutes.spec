# -*- mode: python ; coding: utf-8 -*-
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

HERE = Path(SPECPATH)

with (HERE / "pyproject.toml").open("rb") as f:
    project = tomllib.load(f)["project"]
APP_NAME = project["name"]
APP_VERSION = project["version"]

if sys.platform == "win32":
    ICON = str(HERE / "assets" / f"{APP_NAME}.ico")
elif sys.platform == "darwin":
    ICON = str(HERE / "assets" / f"{APP_NAME}.icns")
else:
    ICON = str(HERE / "assets" / f"{APP_NAME}.png")

EXCLUDES = []
if sys.platform != "linux":
    EXCLUDES += ["PySide6", "qtpy"]

a = Analysis(
    ["gui_main.py"],
    pathex=[str(HERE)],
    # To ship a pre-built binary (e.g. `typst`) with the app, add it here:
    #   binaries=[(str(HERE / "vendor" / "typst"), ".")],
    # and at runtime locate it with:
    #   Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "typst"
    # In CI, download the per-platform binary before running pyinstaller.
    binaries=[],
    datas=[
        (str(HERE / "assets"), "assets") if (HERE / "assets").exists() else None,
        (str(HERE / "pyproject.toml"), "."),
        *collect_data_files("nicegui"),
        *copy_metadata("nicegui"),
    ],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
a.datas = [d for d in a.datas if d is not None]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON if Path(ICON).exists() else None,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="Meeting Minutes.app",
        icon=ICON if Path(ICON).exists() else None,
        bundle_identifier="com.nashamri.meeting-minutes",
        version=APP_VERSION,
        info_plist={
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
