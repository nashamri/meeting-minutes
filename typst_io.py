import re
import shutil
import sys
from pathlib import Path

from models import Article, Meeting, Member

_RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
_LIB_TEMPLATE_DIR = _RESOURCE_ROOT / "assets" / "meeting_template" / "lib"


def _copy_writable(src: Path, dst: Path) -> None:
    """Copy a template file, then force owner-write on the destination.

    Templates may live in a read-only location (the Nix store ships files
    at mode 0444). shutil.copy2 propagates that mode, leaving the user with
    files they can't edit. Always add the owner-write bit to anything we
    drop into a user meeting folder.
    """
    shutil.copy2(src, dst)
    dst.chmod(dst.stat().st_mode | 0o200)


def _copytree_writable(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, copy_function=_copy_writable)


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


_FILENAME_INVALID_CHARS = '/\\\n\r\t\0:*?"<>|'


def _article_filename(index: int, title: str) -> str:
    words = title.strip().split()[:15]
    slug = " ".join(words)
    slug = "".join(" " if c in _FILENAME_INVALID_CHARS else c for c in slug).strip()
    if not slug:
        slug = "article"
    return f"{index + 1:02d}_{slug}.typ"


def _render_constants(meeting: Meeting) -> str:
    q = _quote
    return (
        '#let accent-color = "#84469D"\n'
        '#let light-accent-color = "#f8f7fa"\n'
        "#let highlight-papers = false\n"
        "\n"
        f'#let meeting-date = "{q(meeting.date)}"\n'
        f'#let meeting-number = "{q(meeting.number)}"\n'
        f'#let meeting-number-num = "{q(meeting.number_num)}"\n'
        f'#let meeting-time = "{q(meeting.time)}"\n'
        f'#let meeting-name = "{q(meeting.name)}"\n'
        f'#let academic-year = "{q(meeting.academic_year)}"\n'
    )


def _render_article(article: Article) -> str:
    return (
        '#import "../lib/topics.typ": topic\n'
        '#import "../lib/constants.typ": light-accent-color\n'
        "\n"
        f"#let title = [\n{article.title}\n]\n"
        "\n"
        f"#let body = [\n{article.body}\n]\n"
        "\n"
        f"#let decision = [\n{article.decision}\n]\n"
        "\n"
        f"#let articles = [\n{article.legal_refs}\n]\n"
        "\n"
        f"#let target = [{article.target}]\n"
        "\n"
        "#topic(title, body, decision, articles, target)\n"
    )


def _render_main(meeting: Meeting, article_filenames: list[str]) -> str:
    members_rows = "\n".join(
        f"  [{i + 1}], [{m.name}], [{m.role}], [{m.attendance}], [{m.excuse}],"
        for i, m in enumerate(meeting.members)
    )
    signatures_rows = "\n".join(
        f"  [{i + 1}], [{m.name}], []," for i, m in enumerate(meeting.members)
    )
    includes = "\n".join(f'#include "articles/{n}"' for n in article_filenames)
    return (
        '#import "lib/setup.typ": *\n'
        "\n"
        "#show: setup\n"
        "#meeting-header\n"
        "#table(\n"
        "  columns: (1cm, 2fr, 1fr, 1fr, 1fr),\n"
        "  align: (center, right, center, center, center),\n"
        "  stroke: 0.04em,\n"
        "  fill: (x, y) => if y == 0 { rgb(light-accent-color) },\n"
        "  inset: 1em,\n"
        "  [م], [#align(center)[الاسم]], [الصفة], [حالة الحضور], [سبب التغيب],\n"
        f"{members_rows}\n"
        ")\n"
        "#agenda-section\n"
        "\n"
        f"{includes}\n"
        "\n"
        f"#recommendation([\n{meeting.approval_text}\n])\n"
        "#table(\n"
        "  columns: (1cm, 1fr, 1fr),\n"
        "  align: (center, right, center),\n"
        "  stroke: 0.04em,\n"
        "  fill: (x, y) => if y == 0 { rgb(light-accent-color) },\n"
        "  inset: 1em,\n"
        "  [م], [#align(center)[الاسم]], [التوقيع],\n"
        f"{signatures_rows}\n"
        ")\n"
        "#closing-notes(\n"
        f"  invitees: [\n{meeting.invitees}\n],\n"
        f"  notes: [\n{meeting.closing_notes}\n],\n"
        ")\n"
    )


def write_meeting(meeting: Meeting, dest_dir: Path) -> Path:
    """Materialize the meeting as a Typst project at dest_dir.

    Creates dest_dir if missing. Always overwrites generated files
    (main.typ, lib/constants.typ, articles/article-NN.typ). Copies static
    lib/ assets (setup.typ, topics.typ, letters.typ, images) from the
    bundled template the first time; subsequent saves leave them alone so
    user customizations are preserved. Stale article files matching the
    article-NN.typ pattern that are no longer in the model are removed.

    Returns dest_dir.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    lib_dir = dest_dir / "lib"
    lib_dir.mkdir(exist_ok=True)
    for src in _LIB_TEMPLATE_DIR.iterdir():
        if src.name == "constants.typ":
            continue
        target = lib_dir / src.name
        if src.suffix == ".typ":
            _copy_writable(src, target)
        elif not target.exists():
            if src.is_dir():
                _copytree_writable(src, target)
            else:
                _copy_writable(src, target)
    # Heal pre-existing meetings that were saved before _copy_writable existed:
    # walk the user's lib_dir once and ensure every file is owner-writable.
    for p in lib_dir.rglob("*"):
        if p.is_file():
            p.chmod(p.stat().st_mode | 0o200)
    (lib_dir / "constants.typ").write_text(_render_constants(meeting), encoding="utf-8")

    articles_dir = dest_dir / "articles"
    articles_dir.mkdir(exist_ok=True)
    article_filenames = [
        _article_filename(i, art.title) for i, art in enumerate(meeting.articles)
    ]
    keep = set(article_filenames)
    for old in articles_dir.glob("*.typ"):
        if old.name not in keep:
            old.unlink()
    for filename, article in zip(article_filenames, meeting.articles):
        (articles_dir / filename).write_text(_render_article(article), encoding="utf-8")

    (dest_dir / "main.typ").write_text(
        _render_main(meeting, article_filenames), encoding="utf-8"
    )

    return dest_dir


def _find_matching_bracket(source: str, start: int) -> int | None:
    """Given index just after an opening [, return index of the matching ]."""
    depth = 1
    i = start
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == '"':
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _unquote(raw: str) -> str:
    return re.sub(r"\\(.)", r"\1", raw)


def _extract_string_let(source: str, name: str) -> str:
    pattern = re.compile(rf'#let\s+{re.escape(name)}\s*=\s*"((?:[^"\\]|\\.)*)"')
    m = pattern.search(source)
    if not m:
        return ""
    return _unquote(m.group(1))


def _extract_content_let(source: str, name: str) -> str:
    pattern = re.compile(rf"#let\s+{re.escape(name)}\s*=\s*\[")
    m = pattern.search(source)
    if not m:
        return ""
    start = m.end()
    end = _find_matching_bracket(source, start)
    if end is None:
        return ""
    return source[start:end].strip("\n")


_MEMBER_ROW_RE = re.compile(
    r"\[\d+\]\s*,\s*"
    r"\[([^\]]*)\]\s*,\s*"
    r"\[([^\]]*)\]\s*,\s*"
    r"\[([^\]]*)\]\s*,\s*"
    r"\[([^\]]*)\]"
)


def _extract_members(main_typ: str) -> list[Member]:
    after_header = main_typ.find("#meeting-header")
    if after_header < 0:
        after_header = 0
    agenda = main_typ.find("#agenda-section", after_header)
    end = agenda if agenda >= 0 else len(main_typ)
    members: list[Member] = []
    for m in _MEMBER_ROW_RE.finditer(main_typ, after_header, end):
        name, role, attendance, excuse = (g.strip() for g in m.groups())
        members.append(
            Member(name=name, role=role, attendance=attendance, excuse=excuse)
        )
    return members


def _extract_article_includes(main_typ: str) -> list[str]:
    return re.findall(r'#include\s+"articles/([^"]+)"', main_typ)


def _extract_function_content_arg(source: str, fn_name: str) -> str:
    pattern = re.compile(rf"#{re.escape(fn_name)}\s*\(\s*\[")
    m = pattern.search(source)
    if not m:
        return ""
    start = m.end()
    end = _find_matching_bracket(source, start)
    if end is None:
        return ""
    return source[start:end].strip("\n")


def _extract_kwarg_content(source: str, fn_name: str, kwarg: str) -> str:
    fn_match = re.search(rf"#{re.escape(fn_name)}\s*\(", source)
    if not fn_match:
        return ""
    kw_match = re.search(rf"\b{re.escape(kwarg)}\s*:\s*\[", source[fn_match.end() :])
    if not kw_match:
        return ""
    start = fn_match.end() + kw_match.end()
    end = _find_matching_bracket(source, start)
    if end is None:
        return ""
    return source[start:end].strip("\n")


def read_meeting(src_dir: Path) -> Meeting:
    """Parse a Typst meeting project at src_dir into a Meeting dataclass.

    Reads lib/constants.typ for metadata, main.typ for the members table
    and the ordered article #include list, and each articles/*.typ for the
    per-topic #let fields. Tolerant of missing fields (returns empty
    strings/lists for anything not found).
    """
    src_dir = Path(src_dir)
    meeting = Meeting(
        name="", number="", number_num="", date="", time="", academic_year=""
    )

    constants_path = src_dir / "lib" / "constants.typ"
    if constants_path.exists():
        text = constants_path.read_text(encoding="utf-8")
        meeting.name = _extract_string_let(text, "meeting-name")
        meeting.number = _extract_string_let(text, "meeting-number")
        meeting.number_num = _extract_string_let(text, "meeting-number-num")
        meeting.date = _extract_string_let(text, "meeting-date")
        meeting.time = _extract_string_let(text, "meeting-time")
        meeting.academic_year = _extract_string_let(text, "academic-year")

    main_path = src_dir / "main.typ"
    article_filenames: list[str] = []
    if main_path.exists():
        main_text = main_path.read_text(encoding="utf-8")
        meeting.members = _extract_members(main_text)
        article_filenames = _extract_article_includes(main_text)
        approval = _extract_function_content_arg(main_text, "recommendation")
        if approval:
            meeting.approval_text = approval
        invitees = _extract_kwarg_content(main_text, "closing-notes", "invitees")
        if invitees:
            meeting.invitees = invitees
        closing = _extract_kwarg_content(main_text, "closing-notes", "notes")
        if closing:
            meeting.closing_notes = closing

    articles_dir = src_dir / "articles"
    for filename in article_filenames:
        article_path = articles_dir / filename
        if not article_path.exists():
            continue
        text = article_path.read_text(encoding="utf-8")
        meeting.articles.append(
            Article(
                title=_extract_content_let(text, "title"),
                body=_extract_content_let(text, "body"),
                decision=_extract_content_let(text, "decision"),
                legal_refs=_extract_content_let(text, "articles"),
                target=_extract_content_let(text, "target"),
            )
        )

    return meeting
