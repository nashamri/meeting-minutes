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
# Body and title share the same font catalog. Stored as the Typst family
# name; UI maps to friendly labels via FONT_LABELS.
VALID_FONTS = (
    "Adobe",
    "Calibri",
    "Sultan_medium/ghayaty2020",
    "Almudid",
    "Sakkal Majalla",
)
FONT_LABELS = {
    "Adobe": "Adobe",
    "Calibri": "Calibri",
    "Sultan_medium/ghayaty2020": "Sultan",
    "Almudid": "Almudid",
    "Sakkal Majalla": "Sakkal Majalla",
}
DEFAULT_FONT = "Adobe"
DEFAULT_TITLE_FONT = "Sultan_medium/ghayaty2020"
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 24
DEFAULT_FONT_SIZE = 14
DEFAULT_TITLE_SIZE = 11

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


def load_font() -> str:
    font = _load_config().get("font")
    return font if font in VALID_FONTS else DEFAULT_FONT


def save_font(font: str) -> None:
    if font not in VALID_FONTS:
        raise ValueError(f"Invalid font {font!r}; expected one of {VALID_FONTS}")
    data = _load_config()
    data["font"] = font
    _save_config(data)


def load_font_size() -> int:
    raw = _load_config().get("font_size")
    if isinstance(raw, (int, float)):
        size = int(raw)
        if MIN_FONT_SIZE <= size <= MAX_FONT_SIZE:
            return size
    return DEFAULT_FONT_SIZE


def save_font_size(size: int) -> None:
    size = int(size)
    if not MIN_FONT_SIZE <= size <= MAX_FONT_SIZE:
        raise ValueError(
            f"Invalid font size {size!r}; expected {MIN_FONT_SIZE}-{MAX_FONT_SIZE}"
        )
    data = _load_config()
    data["font_size"] = size
    _save_config(data)


def load_title_font() -> str:
    font = _load_config().get("title_font")
    return font if font in VALID_FONTS else DEFAULT_TITLE_FONT


def save_title_font(font: str) -> None:
    if font not in VALID_FONTS:
        raise ValueError(
            f"Invalid title font {font!r}; expected one of {VALID_FONTS}"
        )
    data = _load_config()
    data["title_font"] = font
    _save_config(data)


def load_title_size() -> int:
    raw = _load_config().get("title_size")
    if isinstance(raw, (int, float)):
        size = int(raw)
        if MIN_FONT_SIZE <= size <= MAX_FONT_SIZE:
            return size
    return DEFAULT_TITLE_SIZE


def save_title_size(size: int) -> None:
    size = int(size)
    if not MIN_FONT_SIZE <= size <= MAX_FONT_SIZE:
        raise ValueError(
            f"Invalid title size {size!r}; expected {MIN_FONT_SIZE}-{MAX_FONT_SIZE}"
        )
    data = _load_config()
    data["title_size"] = size
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


# --- Article templates -----------------------------------------------------
# Reusable per-article presets, each tagged with the committee they belong
# to (captured from meeting.name when saved). Stored in config.json so they
# survive across sessions and are manageable via the "open config in editor"
# button.


_TEMPLATE_FIELDS = ("title", "body", "decision", "legal_refs", "target")


def load_article_templates() -> list[dict]:
    raw = _load_config().get("article_templates", [])
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        name = r.get("name")
        if not isinstance(name, str) or not name:
            continue
        out.append(r)
    return out


def save_article_templates(templates: list[dict]) -> None:
    data = _load_config()
    data["article_templates"] = templates
    _save_config(data)


def add_article_template(
    name: str,
    committee: str = "",
    *,
    title: str = "",
    body: str = "",
    decision: str = "",
    legal_refs: str = "",
    target: str = "",
) -> None:
    """Save an article template, replacing any existing one with the same
    (name, committee) pair."""
    name = (name or "").strip()
    if not name:
        return
    entry = {
        "name": name,
        "committee": committee or "",
        "title": title or "",
        "body": body or "",
        "decision": decision or "",
        "legal_refs": legal_refs or "",
        "target": target or "",
    }
    templates = load_article_templates()
    for i, t in enumerate(templates):
        if t.get("name") == name and t.get("committee", "") == (committee or ""):
            templates[i] = entry
            save_article_templates(templates)
            return
    templates.append(entry)
    save_article_templates(templates)


# --- Known article targets -------------------------------------------------
# Distinct values seen in the "الجهة ذات العلاقة" (target) field, harvested
# every time a meeting is saved. Drives the combobox autocomplete on the
# article target input so the same body names don't have to be retyped.


def load_known_targets() -> list[str]:
    raw = _load_config().get("known_targets", [])
    if not isinstance(raw, list):
        return []
    return sorted({t for t in raw if isinstance(t, str) and t.strip()})


def add_known_targets(targets: list[str] | set[str]) -> None:
    cleaned = {(t or "").strip() for t in targets}
    cleaned.discard("")
    if not cleaned:
        return
    existing = set(load_known_targets())
    merged = existing | cleaned
    if merged == existing:
        return
    data = _load_config()
    data["known_targets"] = sorted(merged)
    _save_config(data)


def load_update_last_seen() -> str:
    """Tag of the newest release we've already notified about. Empty if
    we've never told the user anything."""
    raw = _load_config().get("update_last_seen_version", "")
    return raw if isinstance(raw, str) else ""


def save_update_last_seen(version: str) -> None:
    data = _load_config()
    data["update_last_seen_version"] = version or ""
    _save_config(data)


def remove_article_template(name: str, committee: str = "") -> None:
    templates = load_article_templates()
    filtered = [
        t for t in templates
        if not (
            t.get("name") == name
            and t.get("committee", "") == (committee or "")
        )
    ]
    if len(filtered) != len(templates):
        save_article_templates(filtered)


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
