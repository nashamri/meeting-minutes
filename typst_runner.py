"""Resolve which `typst` binary to invoke at runtime.

Lookup order:
  1. A copy bundled by PyInstaller (inside `sys._MEIPASS`).
  2. A vendored copy under `vendor/typst/<platform>/` next to the source
     (so `scripts/fetch-typst.sh` lets devs exercise the bundled path).
  3. A `typst` on PATH (Nix flake wires this; manual installs hit it too).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def _platform_dir() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _exe_name() -> str:
    return "typst.exe" if sys.platform == "win32" else "typst"


def _candidates() -> list[Path]:
    exe = _exe_name()
    out: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        out.append(Path(meipass) / exe)
    here = Path(__file__).resolve().parent
    out.append(here / "vendor" / "typst" / _platform_dir() / exe)
    return out


def find_typst() -> Path | None:
    for c in _candidates():
        if c.is_file():
            return c
    sys_path = shutil.which("typst")
    return Path(sys_path) if sys_path else None


def typst_font_dir() -> Path | None:
    """Directory of bundled .ttf/.otf files to pass to `typst --font-path`.

    Returns None if the dir is missing or empty so we don't pass an empty
    --font-path that would shadow the system font set on the user's machine.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) if meipass else Path(__file__).resolve().parent
    candidate = base / "assets" / "typst_fonts"
    if not candidate.is_dir():
        return None
    for p in candidate.iterdir():
        if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
            return candidate
    return None
