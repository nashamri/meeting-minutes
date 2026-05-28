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

# Vendored typst binary — populated by scripts/fetch-typst.sh locally or by
# the CI workflow before pyinstaller runs. Bundled at the root so
# typst_runner.find_typst() picks it up via sys._MEIPASS.
if sys.platform == "win32":
    _typst_plat, _typst_exe = "windows", "typst.exe"
elif sys.platform == "darwin":
    _typst_plat, _typst_exe = "macos", "typst"
else:
    _typst_plat, _typst_exe = "linux", "typst"
_typst_path = HERE / "vendor" / "typst" / _typst_plat / _typst_exe
BINARIES = [(str(_typst_path), ".")] if _typst_path.exists() else []

a = Analysis(
    ["gui_main.py"],
    pathex=[str(HERE)],
    binaries=BINARIES,
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
