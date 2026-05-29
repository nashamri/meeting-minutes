import argparse
import importlib.metadata
import json
import os
import platform
import sys
import tomllib
from pathlib import Path

import platformdirs


APP_NAME = "meeting-minutes"
APP_DISPLAY_NAME = "محاضر الاجتماعات"
APP_TAGLINE = "تسجيل وتلخيص محاضر الاجتماعات"
APP_REPO_URL = "https://github.com/nashamri/meeting-minutes"

CONFIG_DIR = Path(platformdirs.user_config_dir(APP_NAME))
CONFIG_FILE = CONFIG_DIR / "config.json"

VALID_THEMES = ("light", "dark")

DEFAULT_MEETINGS_ROOT = Path(platformdirs.user_documents_dir()) / "محاضر الاجتماعات"


def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_config(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def load_theme() -> str | None:
    theme = _load_config().get("theme")
    return theme if theme in VALID_THEMES else None


def save_theme(theme: str) -> None:
    if theme not in VALID_THEMES:
        raise ValueError(f"Invalid theme {theme!r}; expected one of {VALID_THEMES}")
    data = _load_config()
    data["theme"] = theme
    _save_config(data)


def load_meetings_root() -> Path:
    raw = _load_config().get("meetings_root")
    return Path(raw).expanduser() if isinstance(raw, str) and raw else DEFAULT_MEETINGS_ROOT


def save_meetings_root(path: Path | str) -> None:
    data = _load_config()
    data["meetings_root"] = str(path)
    _save_config(data)


RECENT_MAX = 10


def load_recent_meetings() -> list[Path]:
    raw = _load_config().get("recent_meetings", [])
    if not isinstance(raw, list):
        return []
    return [Path(r) for r in raw if isinstance(r, str) and r]


def save_recent_meetings(paths: list[Path | str]) -> None:
    data = _load_config()
    data["recent_meetings"] = [str(p) for p in paths]
    _save_config(data)


def add_recent_meeting(path: Path | str) -> None:
    path = Path(path)
    paths = [p for p in load_recent_meetings() if p != path]
    paths.insert(0, path)
    save_recent_meetings(paths[:RECENT_MAX])


def remove_recent_meeting(path: Path | str) -> None:
    path = Path(path)
    save_recent_meetings([p for p in load_recent_meetings() if p != path])


def clear_recent_meetings() -> None:
    save_recent_meetings([])


# --- Personal spell-check dictionary ---------------------------------------
# Words the user explicitly marks as correct in the spell-check dialog.
# Stored in config.json so they persist across sessions; on every check
# they're filtered out before the analyzer runs.


def load_personal_dict() -> set[str]:
    raw = _load_config().get("personal_dict", [])
    if not isinstance(raw, list):
        return set()
    return {w for w in raw if isinstance(w, str) and w}


def save_personal_dict(words: set[str]) -> None:
    data = _load_config()
    data["personal_dict"] = sorted(words)
    _save_config(data)


def add_to_personal_dict(word: str) -> None:
    word = (word or "").strip()
    if not word:
        return
    words = load_personal_dict()
    if word in words:
        return
    words.add(word)
    save_personal_dict(words)


def remove_from_personal_dict(word: str) -> None:
    words = load_personal_dict()
    if word in words:
        words.discard(word)
        save_personal_dict(words)


def _read_version() -> str:
    try:
        return importlib.metadata.version(APP_NAME)
    except importlib.metadata.PackageNotFoundError:
        pass
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    pyproject = base / "pyproject.toml"
    try:
        with pyproject.open("rb") as f:
            return tomllib.load(f)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def get_app_info() -> dict:
    return {
        "name": APP_DISPLAY_NAME,
        "version": _read_version(),
        "tagline": APP_TAGLINE,
        "python_version": platform.python_version(),
        "platform": platform.platform(terse=True),
        "config_path": str(CONFIG_FILE),
        "repo_url": APP_REPO_URL,
    }


def main():
    argparse.ArgumentParser(
        description=f"{APP_DISPLAY_NAME} — {APP_TAGLINE}"
    ).parse_args()

    from webapp import run_webapp
    run_webapp()


if __name__ == "__main__":
    main()
