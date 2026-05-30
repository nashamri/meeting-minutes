import asyncio
import hashlib
import os
import re
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import date as _date_cls
from pathlib import Path
from typing import Callable

from nicegui import app, ui

from main import (
    APP_DISPLAY_NAME,
    add_article_template,
    add_known_targets,
    add_recent_meeting,
    add_to_personal_dict,
    clear_recent_meetings,
    get_app_info,
    load_article_templates,
    load_known_targets,
    load_meetings_root,
    load_personal_dict,
    load_recent_meetings,
    load_theme,
    load_update_last_seen,
    remove_article_template,
    remove_recent_meeting,
    save_meetings_root,
    save_theme,
    save_update_last_seen,
)
from models import ATTENDANCE_OPTIONS, Article, Meeting, Member
from typst_io import read_meeting, write_meeting
from spell_check_camel import (
    check_text as camel_check_text,
    get_suggestions as camel_get_suggestions,
    is_available as camel_available,
)
from typst_runner import find_typst, typst_font_dir
from typst_tables import (
    TableCell,
    TableSpec,
    add_column,
    add_row,
    append_table_to_body,
    columns_to_css,
    find_tables,
    new_table,
    remove_column,
    remove_row,
    replace_table_in_body,
    serialize_table,
)

_meeting = Meeting()
_preview_urls: list[str] = []
_pdf_path: Path | None = None
_tabs_ref = None
_status_label = None
_status_history: list[dict] = []
_drag_state: dict = {"from": None}
_save_btn = None
_spellcheck_all_btn = None


def _set_spellcheck_all_status(status: str | None) -> None:
    """Tint the bulk spell-check button to reflect the last run.

    status: 'clean' → green, 'errors' → yellow, None → default (primary).
    """
    if _spellcheck_all_btn is None:
        return
    if status == "clean":
        _spellcheck_all_btn._props["color"] = "positive"
    elif status == "errors":
        _spellcheck_all_btn._props["color"] = "warning"
    else:
        _spellcheck_all_btn._props["color"] = "primary"
    _spellcheck_all_btn.update()
_front_tab = None
_articles_tab = None
_end_tab = None
_pdf_tab = None
_recent_menu_refresh = None
# Populated by _check_for_update when GitHub reports a newer release.
# The _update_button refreshable reads this and renders the toolbar
# button when a tag is set; until then the slot is empty.
_update_available: dict = {"tag": "", "url": ""}
_recent_menu = None
_templates_menu_refresh = None
_saved_fingerprint: str | None = None
# Path the current meeting was last loaded from or saved to. Used by
# _save_current_meeting to detect when the user is about to overwrite a
# DIFFERENT meeting that happens to share the same (committee, year,
# number) path — e.g. they typed a number that collides with an existing
# meeting. None means "never persisted" (brand-new or just-reset state).
_loaded_meeting_path: Path | None = None
_icon_url: str | None = None
# Per-article spell-check status keyed by id(article). Each entry is the
# current issue count: 0 → clean (green header), >0 → errors (yellow), and
# articles absent from the dict are "unchecked" (default blue). Cleared
# whenever the meeting is reset or a fresh meeting is loaded; otherwise
# survives drag-reorder (id() is stable per Article object).
_article_check_status: dict[int, int] = {}


def _set_article_status(article, count: int) -> None:
    _article_check_status[id(article)] = count


def _article_status_class(article) -> str:
    """Header class for the article expansion based on check status."""
    n = _article_check_status.get(id(article))
    if n is None:
        return "text-primary"
    if n == 0:
        return "text-positive"
    return "text-warning"


def _get_icon_url() -> str | None:
    """Expose assets/meeting-minutes.png as a static URL the UI can <img> at.

    Resolves under _MEIPASS in a PyInstaller bundle, the source tree
    otherwise. Registered once and cached on the module.
    """
    global _icon_url
    if _icon_url is not None:
        return _icon_url
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    icon_path = base / "assets" / "meeting-minutes.png"
    if not icon_path.is_file():
        return None
    _icon_url = app.add_static_file(local_file=icon_path)
    return _icon_url


def _meeting_fingerprint(m: Meeting) -> str:
    # ui.select with with_input=True can push None into bound string fields
    # when empty. Coerce defensively so the join never trips on a None.
    parts: list[str] = [
        m.name or "",
        m.number or "",
        m.number_num or "",
        m.date or "",
        m.time or "",
        m.time_digital or "",
        m.academic_year or "",
        m.approval_text or "",
        m.invitees or "",
        m.closing_notes or "",
    ]
    for mem in m.members:
        parts.extend(
            [
                mem.name or "",
                mem.role or "",
                mem.attendance or "",
                mem.excuse or "",
            ]
        )
    for art in m.articles:
        parts.extend(
            [
                art.title or "",
                art.body or "",
                art.decision or "",
                art.legal_refs or "",
                art.target or "",
            ]
        )
    return hashlib.md5("\x00".join(parts).encode("utf-8")).hexdigest()


def _mark_clean() -> None:
    global _saved_fingerprint
    _saved_fingerprint = _meeting_fingerprint(_meeting)


def _is_dirty() -> bool:
    return (
        _saved_fingerprint is not None
        and _meeting_fingerprint(_meeting) != _saved_fingerprint
    )


def _refresh_periodic_state() -> None:
    if _save_btn is not None:
        target = "warning" if _is_dirty() else "white"
        if _save_btn._props.get("color") != target:
            _save_btn._props["color"] = target
            _save_btn.update()
    if _articles_tab is not None:
        n = len(_meeting.articles)
        label = f"المواضيع ({n})" if n else "المواضيع"
        if _articles_tab._props.get("label") != label:
            _articles_tab._props["label"] = label
            _articles_tab.update()


def _meeting_label_for_path(p: Path) -> str:
    parts = p.parts
    return " / ".join(parts[-3:]) if len(parts) >= 3 else p.name


# Arabic ordinal feminine forms (1-10 stand-alone, plus the ones-shape used
# to build compound numbers 11-99). الجلسة is feminine so this is the form
# that agrees with it. 11-19 take "X عشرة". 21-99 take "X و-tens" where tens
# uses the indefinite-of-the-definite cardinal (العشرون, الثلاثون, ...).
_AR_ORDINAL_FEM_10 = {
    1: "الأولى",
    2: "الثانية",
    3: "الثالثة",
    4: "الرابعة",
    5: "الخامسة",
    6: "السادسة",
    7: "السابعة",
    8: "الثامنة",
    9: "التاسعة",
    10: "العاشرة",
}
_AR_ONES_FEM_FOR_COMPOUND = {
    1: "الحادية",
    2: "الثانية",
    3: "الثالثة",
    4: "الرابعة",
    5: "الخامسة",
    6: "السادسة",
    7: "السابعة",
    8: "الثامنة",
    9: "التاسعة",
}
_AR_TENS = {
    20: "العشرون",
    30: "الثلاثون",
    40: "الأربعون",
    50: "الخمسون",
    60: "الستون",
    70: "السبعون",
    80: "الثمانون",
    90: "التسعون",
}


def _arabic_ordinal_feminine(n: int) -> str:
    if n < 1:
        return ""
    if n <= 10:
        return _AR_ORDINAL_FEM_10[n]
    if 11 <= n <= 19:
        return f"{_AR_ONES_FEM_FOR_COMPOUND[n - 10]} عشرة"
    if 20 <= n <= 99:
        ones = n % 10
        tens = n - ones
        if ones == 0:
            return _AR_TENS[tens]
        return f"{_AR_ONES_FEM_FOR_COMPOUND[ones]} و{_AR_TENS[tens]}"
    # Beyond 99 the word form gets unwieldy; meetings won't realistically
    # hit this. Keep the digit so the document still reads sensibly.
    return f"رقم {n}"


def _sync_number_word(meeting: Meeting) -> None:
    """Derive meeting.number (word form) from meeting.number_num (digit)."""
    s = (meeting.number_num or "").strip()
    if not s:
        meeting.number = ""
        return
    if s.isdigit():
        meeting.number = _arabic_ordinal_feminine(int(s))


# Feminine hours (السَّاعة is feminine), 1-12. _arabic_time_phrase picks the
# right one from a 24h input.
_AR_HOURS_FEM_12 = {
    1: "الواحدة",
    2: "الثانية",
    3: "الثالثة",
    4: "الرابعة",
    5: "الخامسة",
    6: "السادسة",
    7: "السابعة",
    8: "الثامنة",
    9: "التاسعة",
    10: "العاشرة",
    11: "الحادية عشرة",
    12: "الثانية عشرة",
}


def _arabic_period(h: int) -> str:
    if 5 <= h <= 11:
        return "صباحاً"
    if h == 12:
        return "ظهراً"
    if 13 <= h <= 15:
        return "ظهراً"
    if 16 <= h <= 18:
        return "عصراً"
    if 19 <= h <= 23:
        return "مساءً"
    return "ليلاً"  # 0-4


def _arabic_time_phrase(hhmm: str) -> str:
    """Convert HH:MM (24h) into Arabic prose, e.g. '11:00' → 'الحادية عشرة صباحاً'.

    Common quarter-fractions get their idiomatic word form; other minute
    values keep the digit inline so the phrase still reads cleanly.
    """
    parts = (hhmm or "").split(":")
    if len(parts) != 2:
        return ""
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return ""
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return ""
    h12 = h % 12 or 12
    hour_word = _AR_HOURS_FEM_12[h12]
    period = _arabic_period(h)
    if m == 0:
        return f"{hour_word} {period}"
    if m == 15:
        return f"{hour_word} والربع {period}"
    if m == 30:
        return f"{hour_word} والنصف {period}"
    return f"{hour_word} و{m} دقيقة {period}"


_AR_WEEKDAYS_FULL = {
    0: "الإثنين",
    1: "الثلاثاء",
    2: "الأربعاء",
    3: "الخميس",
    4: "الجمعة",
    5: "السبت",
    6: "الأحد",
}
_AR_MONTHS_FULL = {
    1: "يناير",
    2: "فبراير",
    3: "مارس",
    4: "أبريل",
    5: "مايو",
    6: "يونيو",
    7: "يوليو",
    8: "أغسطس",
    9: "سبتمبر",
    10: "أكتوبر",
    11: "نوفمبر",
    12: "ديسمبر",
}


def _arabic_full_date(iso: str) -> str:
    """Convert YYYY-MM-DD (slashes also accepted) into 'الإثنين، 20 مايو 2026'.

    Used to override q-date's abbreviated title (e.g. 'Mon, May 20').
    Returns '' for unparseable input so the caller can use a blank fallback.
    """
    if not iso:
        return ""
    parts = iso.replace("/", "-").split("-")
    if len(parts) != 3:
        return ""
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        dt = _date_cls(y, m, d)
    except (ValueError, KeyError):
        return ""
    return f"{_AR_WEEKDAYS_FULL[dt.weekday()]}، {d} {_AR_MONTHS_FULL[m]} {y}"


def _sync_time_phrase(meeting: Meeting) -> None:
    """Derive meeting.time (Arabic prose) from meeting.time_digital (HH:MM)."""
    s = (meeting.time_digital or "").strip()
    if not s:
        meeting.time = ""
        return
    phrase = _arabic_time_phrase(s)
    if phrase:
        meeting.time = phrase


_DATE_DISPLAY_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})")


def _date_from_display(s: str) -> str:
    m = _DATE_DISPLAY_RE.search(s or "")
    if not m:
        return ""
    d, mo, y = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _date_to_display(iso: str) -> str:
    if not iso:
        return ""
    parts = iso.split("-")
    if len(parts) != 3:
        return ""
    y, mo, d = parts
    return f"{int(d)} / {int(mo)} / {y} م"


async def _move_focused_article(delta: int) -> None:
    """Move the article whose form has focus by `delta` positions.

    Walks up from document.activeElement to find an `article-row-N` class
    on an ancestor. If we find one, mutate the list and refresh. Silent
    no-op otherwise so Ctrl+Arrow keeps its normal text-cursor behaviour
    when the user isn't editing an article field.
    """
    idx = await ui.run_javascript(
        "(function(){"
        " let el=document.activeElement;"
        " while(el && el!==document.body){"
        "  if(el.classList){"
        "   for(const c of el.classList){"
        "    if(c.startsWith('article-row-')){"
        "     return parseInt(c.slice('article-row-'.length));"
        "    }"
        "   }"
        "  }"
        "  el=el.parentElement;"
        " }"
        " return null;"
        "})()"
    )
    if not isinstance(idx, int):
        return
    arts = _meeting.articles
    if not (0 <= idx < len(arts)):
        return
    new_idx = idx + delta
    if not (0 <= new_idx < len(arts)):
        return
    arts.insert(new_idx, arts.pop(idx))
    _articles_list.refresh()
    # Scroll the moved row into view so the user sees what changed.
    ui.run_javascript(
        f"document.querySelector('.article-row-{new_idx}')?.scrollIntoView"
        f"({{behavior:'smooth',block:'center'}})"
    )


def _on_article_drop(to_idx: int) -> None:
    from_idx = _drag_state.get("from")
    _drag_state["from"] = None
    if from_idx is None or from_idx == to_idx:
        return
    article = _meeting.articles.pop(from_idx)
    _meeting.articles.insert(to_idx, article)
    _articles_list.refresh()


def _confirm(title: str, message: str, on_confirm) -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px] max-w-[480px]"):
        ui.label(title).classes("text-lg font-semibold")
        ui.label(message).classes("text-sm")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("إلغاء", on_click=dialog.close).props("flat")

            def _ok() -> None:
                dialog.close()
                on_confirm()

            ui.button("نعم، احذف", on_click=_ok).props("unelevated color=negative")
    dialog.open()


def _notify(message: str, *, type: str = "info", **kwargs) -> None:
    _status_history.append(
        {
            "time": time.strftime("%H:%M:%S"),
            "message": message,
            "type": type,
        }
    )
    if _status_label is not None:
        _status_label.text = message
    kwargs.setdefault("position", "top")
    # Quasar's default close_button=True renders the localized word for
    # "Close". Pass a literal X glyph so the dismiss action shows just an
    # icon, regardless of locale.
    kwargs.setdefault("close_button", "✕")
    ui.notify(message, type=type, **kwargs)


def _show_history() -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[420px] max-w-[640px]"):
        ui.label("سجل الإشعارات").classes("text-lg font-semibold")
        if not _status_history:
            ui.label("لا توجد إشعارات بعد.").classes("text-sm text-gray-500")
        else:
            with ui.column().classes("w-full gap-1 max-h-96 overflow-auto"):
                colors = {
                    "positive": "text-green-700",
                    "negative": "text-red-700",
                    "warning": "text-yellow-700",
                }
                for entry in reversed(_status_history):
                    color = colors.get(entry["type"], "")
                    with ui.row().classes("w-full items-start gap-2 text-sm"):
                        ui.label(entry["time"]).classes(
                            "text-gray-500 shrink-0 w-20 font-mono"
                        )
                        ui.label(entry["message"]).classes(f"flex-1 {color}")
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("إغلاق", on_click=dialog.close).props("unelevated")
    dialog.open()


_RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
_FONTS_DIR = _RESOURCE_ROOT / "assets" / "fonts"

_FONT_WEIGHTS = {
    "Thin": 100,
    "ExtraLight": 200,
    "Light": 300,
    "Regular": 400,
    "Text": 450,
    "Medium": 500,
    "SemiBold": 600,
    "Bold": 700,
}


def _register_fonts() -> None:
    if not _FONTS_DIR.is_dir():
        return
    app.add_static_files("/fonts", str(_FONTS_DIR))
    faces = " ".join(
        f'@font-face {{ font-family: "IBM Plex Sans Arabic"; '
        f'src: url("/fonts/IBMPlexSansArabic-{style}.woff2") format("woff2"); '
        f"font-weight: {weight}; font-style: normal; font-display: swap; }}"
        for style, weight in _FONT_WEIGHTS.items()
    )
    body_rule = (
        "body, .q-field, .q-btn, .q-dialog, .q-tooltip, input, textarea, button "
        '{ font-family: "IBM Plex Sans Arabic", system-ui, sans-serif; }'
    )
    resize_rule = ".q-textarea textarea.q-field__native { resize: vertical; }"
    table_cell_rule = (
        ".tbl-cell, .tbl-cell * { min-width: 0 !important; box-sizing: border-box; } "
        ".tbl-cell .q-field, "
        ".tbl-cell .q-field__inner, "
        ".tbl-cell .q-field__control, "
        ".tbl-cell .q-field__native "
        "{ width: 100% !important; max-width: 100% !important; min-width: 0 !important; } "
        ".tbl-cell .q-field__native { unicode-bidi: plaintext; text-align: start; }"
    )
    # In dark mode, replace the primary-coloured header and footer with a
    # dark gray so the blue accent reads as an accent, not as a wall at the
    # top/bottom of the app.
    dark_header_rule = (
        "body.body--dark .q-header, "
        "body.body--dark .q-footer { "
        "background: #1F2937 !important; "
        "}"
    )
    # Command palette row hover + selection. Done via a CSS rule (not
    # Tailwind `dark:` classes, which Quasar's class processor doesn't
    # understand) so light and dark modes both get sensible contrast.
    cmd_palette_rule = (
        ".cmd-palette-row:hover { background-color: rgba(0,0,0,0.05); } "
        "body.body--dark .cmd-palette-row:hover { "
        "background-color: rgba(255,255,255,0.06); } "
        ".cmd-palette-row--selected, "
        ".cmd-palette-row--selected:hover { "
        "background-color: rgba(25,118,210,0.15); } "
        "body.body--dark .cmd-palette-row--selected, "
        "body.body--dark .cmd-palette-row--selected:hover { "
        "background-color: rgba(100,181,246,0.20); }"
    )
    # Hide any q-menu that carries the warming-up class so the one-shot
    # mount we do at startup (to prime Quasar's Teleport target) never
    # flickers on screen. The class is added before open() and removed
    # after close() in _warmup_recent_menu.
    menu_warmup_rule = (
        ".q-menu.warming-up { "
        "opacity: 0 !important; "
        "pointer-events: none !important; "
        "}"
    )
    # Subtle background tint on an opened article. Scoped via the direct-
    # child selector so the highlight applies only to the article's outer
    # expansion — the nested "tables" expansion inside an article doesn't
    # get its own tint when opened. Transition smooths the open/close.
    article_open_rule = (
        "[class*='article-row-'] > .q-expansion-item { "
        "transition: background-color 0.2s ease; "
        "border-radius: 6px; "
        "} "
        "[class*='article-row-'] > .q-expansion-item--expanded { "
        "background-color: rgba(0, 0, 0, 0.035); "
        "} "
        "body.body--dark [class*='article-row-'] > .q-expansion-item--expanded { "
        "background-color: rgba(255, 255, 255, 0.04); "
        "}"
    )
    # Make notifications clickable to dismiss: cursor hint + a document-level
    # handler that triggers Quasar's own close button (so its dismissal
    # lifecycle stays intact). Skip the trigger if the user already clicked
    # the close button itself, to avoid double-dismiss.
    notify_click_rule = (
        ".q-notification { cursor: pointer; } "
        # Keep the close button in the DOM (we click it from JS to dismiss
        # cleanly through Quasar's lifecycle) but hide it visually.
        ".q-notification__actions { display: none !important; }"
    )
    notify_dismiss_js = """
        document.addEventListener('click', function(e) {
            var notif = e.target.closest && e.target.closest('.q-notification');
            if (!notif) return;
            if (e.target.closest('.q-notification__actions')) return;
            var btn = notif.querySelector('.q-notification__actions .q-btn');
            if (btn) { btn.click(); }
        }, true);
    """
    # Article-body formatting helpers. Each ui.textarea for an article body
    # gets a unique class via input-class on the q-input, which we look up
    # here. Wraps work on the current selection (or insert markers around
    # the caret if nothing is selected); list helpers prefix each line in
    # the selection (or the current line if nothing is selected); the link
    # helper inserts a #link(...)[...] and parks the cursor inside the URL.
    article_body_js = r"""
        (function() {
            function findTA(cls) {
                return document.querySelector('textarea.' + cls);
            }
            function fireInput(ta) {
                ta.dispatchEvent(new Event('input', { bubbles: true }));
            }
            window.articleBodyWrap = function(cls, prefix, suffix) {
                var ta = findTA(cls);
                if (!ta) return;
                var s = ta.selectionStart, e = ta.selectionEnd;
                var sel = ta.value.substring(s, e);
                var ins = prefix + sel + suffix;
                ta.value = ta.value.substring(0, s) + ins + ta.value.substring(e);
                fireInput(ta);
                ta.focus();
                if (sel) {
                    ta.setSelectionRange(s + prefix.length, s + prefix.length + sel.length);
                } else {
                    ta.setSelectionRange(s + prefix.length, s + prefix.length);
                }
            };
            window.articleBodyLine = function(cls, prefix) {
                var ta = findTA(cls);
                if (!ta) return;
                var s = ta.selectionStart, e = ta.selectionEnd;
                var sel = ta.value.substring(s, e);
                var out;
                if (sel) {
                    out = sel.split('\n').map(function(l) {
                        return l.length ? prefix + l : l;
                    }).join('\n');
                } else {
                    // No selection — prefix the current line at its start.
                    var lineStart = ta.value.lastIndexOf('\n', s - 1) + 1;
                    ta.value = ta.value.substring(0, lineStart) + prefix + ta.value.substring(lineStart);
                    fireInput(ta);
                    ta.focus();
                    ta.setSelectionRange(s + prefix.length, s + prefix.length);
                    return;
                }
                ta.value = ta.value.substring(0, s) + out + ta.value.substring(e);
                fireInput(ta);
                ta.focus();
                ta.setSelectionRange(s, s + out.length);
            };
            window.articleBodyLink = function(cls) {
                var ta = findTA(cls);
                if (!ta) return;
                var s = ta.selectionStart, e = ta.selectionEnd;
                var sel = ta.value.substring(s, e) || 'النص';
                var ins = '#link("https://")[' + sel + ']';
                ta.value = ta.value.substring(0, s) + ins + ta.value.substring(e);
                fireInput(ta);
                ta.focus();
                // Park cursor right after `https://` so the user types the URL.
                var urlEnd = s + ('#link("https://').length;
                ta.setSelectionRange(urlEnd, urlEnd);
            };
        })();
    """
    # Quasar's autogrow textarea only recalculates height on input events,
    # so a textarea mounted inside a collapsed q-expansion-item ends up
    # measured at zero height and stays one row tall when the expansion
    # opens. Dispatching a synthetic input event on focus retriggers the
    # autogrow logic, so the textarea snaps to its content size as soon
    # as the user clicks into it.
    autogrow_refocus_js = """
        document.addEventListener('focusin', function(e) {
            var t = e.target;
            if (t && t.tagName === 'TEXTAREA') {
                t.dispatchEvent(new Event('input', { bubbles: true }));
            }
        }, true);
    """
    ui.add_head_html(
        f"<style>{faces} {body_rule} {resize_rule} {table_cell_rule} "
        f"{dark_header_rule} {notify_click_rule} {article_open_rule} "
        f"{menu_warmup_rule} {cmd_palette_rule}</style>"
        f"<script>{notify_dismiss_js}</script>"
        f"<script>{article_body_js}</script>"
        f"<script>{autogrow_refocus_js}</script>",
        shared=True,
    )


# Brand primaries per theme. ui.colors() calls Quasar's runtime setBrand(),
# which updates the whole color system reliably — including classes a plain
# CSS-var override doesn't reach.
_PRIMARY_LIGHT = "#1976D2"  # Quasar default
_PRIMARY_DARK = "#3D8BD8"  # Same hue, +saturation, -lightness vs Blue 300


def _apply_theme_colors(is_dark: bool) -> None:
    ui.colors(primary=_PRIMARY_DARK if is_dark else _PRIMARY_LIGHT)


_register_fonts()


_INVALID_PATH_CHARS = "/\\\n\r\t\0"


def _sanitize_path_component(value: str) -> str:
    return "".join("_" if c in _INVALID_PATH_CHARS else c for c in value.strip())


def _apply_loaded_meeting(loaded: Meeting, src: Path) -> None:
    for attr in (
        "name",
        "number",
        "number_num",
        "date",
        "time",
        "time_digital",
        "academic_year",
        "approval_text",
        "invitees",
        "closing_notes",
    ):
        setattr(_meeting, attr, getattr(loaded, attr))
    _meeting.members = list(loaded.members)
    _meeting.articles = list(loaded.articles)
    _article_check_status.clear()
    _set_spellcheck_all_status(None)
    global _loaded_meeting_path
    _loaded_meeting_path = Path(src)
    _members_list.refresh()
    _articles_list.refresh()
    _signatures_preview.refresh()
    add_recent_meeting(src)
    if _recent_menu_refresh is not None:
        _recent_menu_refresh()
    if _templates_menu_refresh is not None:
        _templates_menu_refresh()
    _mark_clean()
    _notify(f"فُتح: {src.name}", type="positive", timeout=4000)


def _reset_to_blank_meeting() -> None:
    """Wipe the in-memory meeting back to defaults and refresh the UI.

    Mirrors _apply_loaded_meeting but without a source path: no recents
    update, and the preview tab gets cleared so stale SVGs from the
    previous meeting don't linger."""
    global _preview_urls
    fresh = Meeting()
    for attr in (
        "name",
        "number",
        "number_num",
        "date",
        "time",
        "time_digital",
        "academic_year",
        "approval_text",
        "invitees",
        "closing_notes",
    ):
        setattr(_meeting, attr, getattr(fresh, attr))
    _meeting.members = list(fresh.members)
    _meeting.articles = list(fresh.articles)
    _article_check_status.clear()
    _set_spellcheck_all_status(None)
    global _loaded_meeting_path
    _loaded_meeting_path = None
    _members_list.refresh()
    _articles_list.refresh()
    _signatures_preview.refresh()
    if _templates_menu_refresh is not None:
        _templates_menu_refresh()
    _preview_urls = []
    _pdf_view.refresh()
    if _tabs_ref is not None:
        _tabs_ref.set_value("front")
    _mark_clean()
    _notify("اجتماع جديد.", type="positive", timeout=3000)


def _new_meeting() -> None:
    """Entry point for the New-Meeting button. Confirms only if the user
    has unsaved edits — clean state means no point asking."""
    if not _is_dirty():
        _reset_to_blank_meeting()
        return
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px] max-w-[480px]"):
        ui.label("إنشاء اجتماع جديد").classes("text-lg font-semibold")
        ui.label(
            "يوجد اجتماع مفتوح بتغييرات غير محفوظة. هل تريد تجاهل التغييرات وبدء اجتماع جديد؟"
        ).classes("text-sm")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("إلغاء", on_click=dialog.close).props("flat")

            def _ok() -> None:
                dialog.close()
                _reset_to_blank_meeting()

            ui.button("تجاهل وابدأ", on_click=_ok).props("unelevated color=warning")
    dialog.open()


def _apply_duplicate_template() -> None:
    """Clear per-meeting fields, keep council/template fields.

    Kept: name (committee), academic_year, members, approval_text — these
    stay constant across meetings of the same body. Cleared: number/date/
    time/articles/invitees/closing_notes — these are filled in fresh.
    """
    global _preview_urls, _loaded_meeting_path
    _meeting.number = ""
    _meeting.number_num = ""
    _meeting.date = ""
    _meeting.time = ""
    _meeting.time_digital = ""
    _meeting.articles = []
    _meeting.invitees = ""
    _meeting.closing_notes = ""
    # The duplicate is a NEW meeting, not a copy that lives at the
    # original's path — clearing this lets the collision check warn if
    # the user picks a number that already exists.
    _loaded_meeting_path = None
    _members_list.refresh()
    _articles_list.refresh()
    _signatures_preview.refresh()
    _preview_urls = []
    _pdf_view.refresh()
    if _tabs_ref is not None:
        _tabs_ref.set_value("front")
    _mark_clean()
    _notify(
        "تم نسخ الاجتماع كقالب. أدخل البيانات الجديدة ثم احفظ.",
        type="positive",
        timeout=5000,
    )


def _duplicate_as_template() -> None:
    """Use the currently open meeting as a starting point for a new one.

    Always confirms before discarding the current view: the duplicate clears
    the article list and date/number, so it's worth a deliberate click even
    when the meeting is already saved.
    """
    dirty = _is_dirty()
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px] max-w-[480px]"):
        ui.label("نسخ الاجتماع كقالب").classes("text-lg font-semibold")
        if dirty:
            body = (
                "يوجد اجتماع مفتوح بتغييرات غير محفوظة. "
                "سيُحذف رقم الجلسة، التاريخ، المواضيع، والملاحظات. "
                "هل تريد المتابعة؟"
            )
        else:
            body = (
                "سيُحذف رقم الجلسة، التاريخ، المواضيع، والملاحظات، "
                "وستبقى بيانات المجلس والأعضاء. هل تريد المتابعة؟"
            )
        ui.label(body).classes("text-sm")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("إلغاء", on_click=dialog.close).props("flat")

            def _ok() -> None:
                dialog.close()
                _apply_duplicate_template()

            ui.button("نعم، انسخ", on_click=_ok).props(
                "unelevated color=warning" if dirty else "unelevated color=primary"
            )
    dialog.open()


def _is_meeting_dir(src: Path) -> bool:
    """A saved meeting always has a main.typ entry point. read_meeting is
    permissive and returns an empty Meeting if files are missing, so we
    have to check ourselves before calling it."""
    return (src / "main.typ").is_file()


def _open_meeting_at(src: Path) -> None:
    if not _is_meeting_dir(src):
        remove_recent_meeting(src)
        if _recent_menu_refresh is not None:
            _recent_menu_refresh()
        _notify(f"الاجتماع غير موجود: {src}", type="negative")
        return
    try:
        loaded = read_meeting(src)
    except OSError as exc:
        _notify(f"تعذّر فتح الاجتماع: {exc}", type="negative")
        return
    _apply_loaded_meeting(loaded, src)


async def _open_meeting() -> None:
    import webview

    win = app.native.main_window
    if win is None:
        _notify("ميزة الفتح متاحة في الوضع الأصلي فقط.", type="negative")
        return
    dialog_type = (
        webview.FileDialog.FOLDER
        if hasattr(webview, "FileDialog")
        else webview.FOLDER_DIALOG
    )
    root = load_meetings_root()
    paths = await win.create_file_dialog(
        dialog_type=dialog_type,
        directory=str(root) if root.exists() else "",
    )
    if not paths:
        return
    src = Path(paths[0])
    if not _is_meeting_dir(src):
        _notify(f"المجلد المختار لا يحتوي على اجتماع: {src.name}", type="negative")
        return
    try:
        loaded = read_meeting(src)
    except OSError as exc:
        _notify(f"تعذّر فتح الاجتماع: {exc}", type="negative")
        return
    _apply_loaded_meeting(loaded, src)


def _resolve_meeting_dest() -> Path | None:
    missing = []
    if not _meeting.name.strip():
        missing.append("اسم المجلس")
    if not _meeting.academic_year.strip():
        missing.append("السنة الأكاديمية")
    if not _meeting.number_num.strip():
        missing.append("رقم الجلسة")
    if missing:
        _notify("الرجاء تعبئة: " + "، ".join(missing), type="negative")
        return None
    if not _meeting.number_num.strip().isdigit():
        _notify("رقم الجلسة يجب أن يكون عدداً صحيحاً.", type="negative")
        return None
    root = load_meetings_root()
    return (
        root
        / _sanitize_path_component(_meeting.name)
        / _sanitize_path_component(_meeting.academic_year)
        / _sanitize_path_component(_meeting.number_num)
    )


def _open_meeting_folder() -> None:
    """Reveal the meeting's folder in the OS file manager.

    Reuses _open_in_default_editor since xdg-open / `open` / os.startfile
    all open folders too. If the meeting isn't yet saved (fields missing
    or the resolved path doesn't exist on disk), tell the user instead of
    silently opening the parent root.
    """
    dest = _resolve_meeting_dest()
    if dest is None:
        return  # _resolve_meeting_dest already surfaced what's missing
    if not dest.exists():
        _notify("لم يُحفظ الاجتماع بعد.", type="negative")
        return
    _open_in_default_editor(dest)


async def _confirm_overwrite(dest: Path) -> bool:
    """Modal confirmation when about to overwrite an existing meeting dir.

    Returns True if the user clicks the warning-coloured 'استبدال' button.
    """
    with ui.dialog() as dialog, ui.card().classes("min-w-[360px] max-w-[540px]"):
        ui.label("اجتماع بنفس الرقم موجود").classes("text-lg font-semibold")
        ui.label(
            "يوجد اجتماع محفوظ بنفس المجلس والسنة والرقم. "
            "الحفظ سيستبدل بياناته. هل تريد المتابعة؟"
        ).classes("text-sm")
        ui.label(str(dest)).classes("text-xs text-gray-500 font-mono break-all")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("إلغاء", on_click=lambda: dialog.submit(False)).props("flat")
            ui.button("استبدال", on_click=lambda: dialog.submit(True)).props(
                "unelevated color=warning"
            )
    result = await dialog
    return bool(result)


async def _save_current_meeting() -> Path | None:
    dest = _resolve_meeting_dest()
    if dest is None:
        return None
    # Collision check: only warn if the dest is a different folder than
    # the one we're tracking AND it already looks like a meeting on disk.
    # When _loaded_meeting_path matches dest, the user is just re-saving
    # the same meeting — no warning needed.
    global _loaded_meeting_path
    if _loaded_meeting_path != dest and _is_meeting_dir(dest):
        if not await _confirm_overwrite(dest):
            return None
    try:
        write_meeting(_meeting, dest)
    except OSError as exc:
        _notify(f"تعذّر الحفظ: {exc}", type="negative")
        return None
    _loaded_meeting_path = dest
    add_recent_meeting(dest)
    # Harvest every non-empty article target into the config-persisted
    # list so it shows up in the autocomplete on the next render — both
    # for new articles in this meeting and for any future meeting.
    add_known_targets([a.target for a in _meeting.articles if a.target])
    if _recent_menu_refresh is not None:
        _recent_menu_refresh()
    _mark_clean()
    _notify(f"حُفظ في: {dest}", type="positive", timeout=5000)
    return dest


async def _compile_meeting() -> None:
    dest = await _save_current_meeting()
    if dest is None:
        return
    typst_bin = find_typst()
    if typst_bin is None:
        _notify("لم يتم العثور على برنامج typst. الرجاء تثبيته.", type="negative")
        return
    main_typ = dest / "main.typ"
    pdf_path = dest / "main.pdf"
    preview_dir = dest / "_preview"
    if preview_dir.exists():
        for p in preview_dir.glob("page-*.svg"):
            p.unlink()
    preview_dir.mkdir(exist_ok=True)
    svg_template = preview_dir / "page-{0p}.svg"

    # When we ship bundled fonts, lock typst to ONLY that set. Otherwise
    # platform-specific system fonts leak in and produce different PDFs on
    # Linux/macOS/Windows — including bidi-mirror differences for glyphs
    # like sym.paren.stroked that depend on font shaping tables.
    font_args: list[str] = []
    bundled_fonts = typst_font_dir()
    if bundled_fonts is not None:
        font_args = ["--font-path", str(bundled_fonts), "--ignore-system-fonts"]

    sp_kw = _subprocess_kwargs()
    try:
        pdf_result = await asyncio.to_thread(
            subprocess.run,
            [str(typst_bin), "compile", *font_args, str(main_typ), str(pdf_path)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **sp_kw,
        )
        if pdf_result.returncode == 0:
            svg_result = await asyncio.to_thread(
                subprocess.run,
                [
                    str(typst_bin),
                    "compile",
                    *font_args,
                    str(main_typ),
                    str(svg_template),
                    "--format",
                    "svg",
                ],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                **sp_kw,
            )
        else:
            svg_result = None
    except subprocess.TimeoutExpired:
        _notify("انتهى وقت التصدير.", type="negative")
        return

    if pdf_result.returncode != 0:
        msg = (pdf_result.stderr or pdf_result.stdout).strip()[:400] or "خطأ غير معروف"
        _notify(f"فشل التصدير: {msg}", type="negative", multi_line=True)
        return

    global _preview_urls
    if svg_result is not None and svg_result.returncode == 0:
        ts = int(time.time())
        _preview_urls = [
            f"{app.add_static_file(local_file=p, max_cache_age=0)}?t={ts}"
            for p in sorted(preview_dir.glob("page-*.svg"))
        ]
    else:
        _preview_urls = []
    global _pdf_path
    _pdf_path = pdf_path if pdf_path.exists() else None
    _pdf_view.refresh()
    if _tabs_ref is not None:
        _tabs_ref.set_value("pdf")
    _notify(f"تم التصدير: {pdf_path}", type="positive", timeout=5000)


@ui.refreshable
def _pdf_view() -> None:
    if not _preview_urls:
        ui.label("اضغط زر التصدير لإنشاء الملف وعرضه هنا.").classes(
            "text-sm text-gray-500"
        )
        return
    if _pdf_path is not None and _pdf_path.exists():
        with ui.row().classes("w-full items-center justify-center gap-2 p-2"):
            ui.button(
                "فتح PDF",
                icon="open_in_new",
                on_click=lambda: _open_in_default_editor(_pdf_path),
            ).props("flat")
            ui.button(
                "طباعة التواقيع",
                icon="draw",
                on_click=_print_last_page,
            ).props("flat").tooltip("طباعة آخر صفحة فقط")
            ui.button(
                "طباعة الكل",
                icon="print",
                on_click=lambda: _print_pdf(_pdf_path),
            ).props("flat")
    with ui.column().classes("w-full items-center gap-3 p-2"):
        for url in _preview_urls:
            ui.image(url).classes("max-w-3xl w-full border rounded shadow-sm")


@dataclass
class _Command:
    """A user-invocable action.

    label    — Arabic title shown in the palette and shortcuts dialog.
    action   — sync or async callable; the palette awaits coroutine results.
    shortcut — tuple of key-name parts (e.g. ("Ctrl", "N")) or None. The
               last element is matched case-insensitively against
               KeyboardEvent.key.name. Modifier names (Ctrl, Shift) are
               read from event.modifiers, not from this list.
    when     — predicate returning True iff the command should be active
               right now. None = always available. Disabled commands are
               hidden from the palette and refuse to fire from a shortcut.
    icon     — Material Symbols icon shown next to the label in the palette.
    """
    label: str
    action: Callable
    shortcut: tuple[str, ...] | None = None
    when: Callable[[], bool] | None = None
    icon: str | None = None

    def is_active(self) -> bool:
        return self.when is None or bool(self.when())


# Set by _index() once the per-page commands are built. The shortcut
# dispatcher, the help dialog, and the command palette all read from this.
_commands: list[_Command] = []


def _kbd_html(keys: list[str]) -> str:
    style = (
        "border:1px solid var(--q-primary,#999); border-radius:4px;"
        "padding:1px 6px; font-family:monospace; font-size:0.85em;"
        "background:rgba(0,0,0,0.04);"
    )
    return ' <span style="opacity:0.5;">+</span> '.join(
        f'<kbd style="{style}">{k}</kbd>' for k in keys
    )


def _shortcut_extras() -> list[tuple[str, list[str]]]:
    """Shortcuts not represented as discrete commands.

    Article reordering is a contextual action (moves whichever article has
    focus) so it's wired directly in _handle_key, not in the command list.
    Surface it here so the cheatsheet still mentions it.
    """
    return [("نقل الموضوع للأعلى/للأسفل", ["Ctrl", "↑↓"])]


def _show_shortcuts() -> None:
    entries: list[tuple[str, list[str]]] = []
    for cmd in _commands:
        if cmd.shortcut is None:
            continue
        entries.append((cmd.label, list(cmd.shortcut)))
    entries.extend(_shortcut_extras())
    entries.append(("لوحة الأوامر", ["Ctrl", "Shift", "P"]))

    with ui.dialog() as dialog, ui.card().classes("min-w-[320px] max-w-[480px]"):
        ui.label("اختصارات لوحة المفاتيح").classes("text-lg font-semibold")
        for action, keys in entries:
            with ui.row().classes("w-full justify-between items-center gap-4"):
                ui.label(action).classes("text-sm")
                ui.html(_kbd_html(keys))
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("إغلاق", on_click=dialog.close).props("unelevated")
    dialog.open()


async def _run_command(cmd: _Command) -> None:
    """Invoke a command's action, awaiting it if it's a coroutine."""
    result = cmd.action()
    if asyncio.iscoroutine(result):
        await result


def _open_command_palette() -> None:
    """VS Code-style command palette (Ctrl+Shift+P).

    Lists every active command (those whose `when` predicate passes), with
    a substring filter on the Arabic label. Enter runs the highlighted
    item, ↑/↓ move the highlight, Esc closes (handled by Quasar's q-dialog).
    """
    active = [c for c in _commands if c.is_active()]
    state = {"selected": 0, "filtered": list(active)}

    with ui.dialog() as dialog, ui.card().classes(
        "w-[480px] max-w-[90vw] p-2 gap-2 overflow-hidden"
    ):
        # dir=rtl (not auto) so the placeholder right-aligns immediately
        # even while the field is empty — auto only flips once the user
        # starts typing Arabic. w-full + the card's own p-2 keeps the
        # field flush within the dialog without overflowing.
        search = (
            ui.input(placeholder="ابحث عن أمر...")
            .props('autofocus dense outlined dir="rtl"')
            .classes("w-full")
        )

        list_container = ui.column().classes(
            "w-full gap-0 max-h-[50vh] overflow-y-auto"
        )

        def _refilter() -> None:
            q = (search.value or "").strip().lower()
            state["filtered"] = [
                c for c in active if not q or q in c.label.lower()
            ]
            if state["selected"] >= len(state["filtered"]):
                state["selected"] = 0
            _render()

        async def _run(cmd: _Command) -> None:
            dialog.close()
            await _run_command(cmd)

        def _render() -> None:
            list_container.clear()
            with list_container:
                if not state["filtered"]:
                    ui.label("لا توجد أوامر مطابقة").classes(
                        "p-3 text-sm text-gray-500"
                    )
                    return
                for i, cmd in enumerate(state["filtered"]):
                    classes = (
                        "w-full items-center gap-3 px-3 py-2 rounded "
                        "cursor-pointer no-wrap cmd-palette-row"
                    )
                    if i == state["selected"]:
                        classes += " cmd-palette-row--selected"
                    row = ui.row().classes(classes)
                    row.on("click", lambda c=cmd: _run(c))
                    with row:
                        if cmd.icon:
                            ui.icon(cmd.icon).classes("text-base")
                        ui.label(cmd.label).classes("flex-1 text-sm")
                        if cmd.shortcut:
                            ui.html(_kbd_html(list(cmd.shortcut))).classes(
                                "text-xs"
                            )

        def _move(delta: int) -> None:
            if not state["filtered"]:
                return
            n = len(state["filtered"])
            state["selected"] = (state["selected"] + delta) % n
            _render()

        async def _accept() -> None:
            if state["filtered"]:
                await _run(state["filtered"][state["selected"]])

        search.on_value_change(lambda _: _refilter())
        search.on("keydown.down.prevent", lambda _: _move(+1))
        search.on("keydown.up.prevent", lambda _: _move(-1))
        search.on("keydown.enter.prevent", lambda _: _accept())

        _render()

    dialog.open()


def _warmup_recent_menu() -> None:
    """Force Quasar's q-menu to do its first-time Teleport mount + layout
    before the user ever clicks the recent meetings button.

    Quasar lazily attaches q-menu content into <body> only on the first
    open, which is what causes the "appears, then jumps into place"
    behaviour. We do that first mount proactively right after the page
    settles. The menu_warmup_rule CSS hides anything tagged
    'warming-up' so the brief open is invisible; we strip the class
    after a small delay so a real subsequent open animates normally.
    """
    if _recent_menu is None:
        return
    _recent_menu.classes(add="warming-up")
    _recent_menu.open()

    def _close_and_unhide() -> None:
        if _recent_menu is None:
            return
        _recent_menu.close()
        # One more tick before stripping the class — gives the close
        # transition (if any) time to fully settle in the hidden state.
        ui.timer(
            0.1,
            lambda: _recent_menu.classes(remove="warming-up"),
            once=True,
        )

    ui.timer(0.08, _close_and_unhide, once=True)


def _subprocess_kwargs() -> dict:
    """Extra subprocess kwargs to suppress the transient console window
    Windows pops for each child process. No-op on macOS/Linux.

    Without CREATE_NO_WINDOW, every typst call from a windowed pywebview
    parent flashes a console — bad UX during compile/print since multiple
    typst invocations happen back-to-back.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _parse_version(s: str) -> tuple:
    """Lenient version parser: '1.2.3', 'v1.2.3', '1.2.3-rc1' all work.

    Each dotted segment yields its leading-digit run (so '3-rc1' → 3).
    Missing segments are treated as 0. The returned tuple is suitable
    for direct comparison.
    """
    s = (s or "").lstrip("vV").strip()
    parts: list[int] = []
    for chunk in s.split("."):
        digits = ""
        for c in chunk:
            if c.isdigit():
                digits += c
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _fetch_latest_release_blocking() -> dict | None:
    """Fetch the latest GitHub release for the app's repo. Returns the
    parsed JSON dict, or None on any failure (network, rate limit, etc.).
    Runs synchronously — call via asyncio.to_thread.

    Uses certifi's CA bundle explicitly because Nix-built Pythons
    (and some PyInstaller-packaged Pythons) don't pick up the system
    bundle, which makes the default ssl context reject every HTTPS cert.
    Falls back to the default context if certifi isn't installed.
    """
    import json
    import re
    import ssl
    import urllib.request

    repo_url = get_app_info().get("repo_url", "")
    m = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
    if not m:
        return None
    api = f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/releases/latest"
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            api, headers={"User-Agent": "meeting-minutes-update-check"}
        )
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


async def _check_for_update() -> None:
    """Startup check: if GitHub has a newer release than we are, surface
    a persistent header button. Also fire a one-shot toast on the first
    session that detects this specific version (debounced via the
    update_last_seen_version key in config.json).

    Silent on any failure — no network, GitHub rate limit, malformed
    response: we just don't bother the user."""
    info = await asyncio.to_thread(_fetch_latest_release_blocking)
    if not info:
        return
    tag = info.get("tag_name") or ""
    current = _parse_version(get_app_info().get("version", "0"))
    latest = _parse_version(tag)
    if not tag or latest <= current:
        return

    url = info.get("html_url") or get_app_info().get("repo_url", "")
    # Persistent state: show the header button every session until the
    # user actually upgrades and `current` catches up.
    _update_available["tag"] = tag
    _update_available["url"] = url
    _update_button.refresh()

    # One-shot toast: only the first session that sees this version
    # nags via a notification. last_seen suppresses repeat toasts.
    last_seen = _parse_version(load_update_last_seen())
    if latest > last_seen:
        _notify(
            f"إصدار جديد {tag} متوفر\n{url}",
            type="info",
            multi_line=True,
            timeout=12000,
        )
        save_update_last_seen(tag)


@ui.refreshable
def _update_button() -> None:
    """Toolbar button that appears only when a newer release exists.

    Renders nothing in the common case; materialises in the header
    after _check_for_update populates _update_available. Clicking opens
    the release URL in the user's default browser.
    """
    if not _update_available.get("url"):
        return
    ui.button(
        icon="system_update_alt",
        on_click=lambda: webbrowser.open(_update_available["url"]),
    ).props("flat round dense color=warning").tooltip(
        f"تحديث متوفر: {_update_available['tag']}"
    )


def _print_pdf(path: Path) -> None:
    """Send a PDF to the system's print pipeline.

    Windows uses the file's registered 'print' verb (typically the
    default PDF viewer). macOS/Linux pipe through `lp` (CUPS) — but if
    that fails (no default printer, CUPS not running, etc.) we surface
    the error and open the PDF in the default viewer so the user can
    print from there instead.
    """
    if sys.platform == "win32":
        try:
            os.startfile(str(path), "print")  # noqa: S606
        except OSError as exc:
            _notify(f"تعذّر الطباعة: {exc}", type="negative")
        return

    try:
        result = subprocess.run(
            ["lp", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        _notify(
            "lp غير مثبت. سيُفتح PDF للطباعة يدوياً.",
            type="warning",
            multi_line=True,
        )
        _open_in_default_editor(path)
        return
    except subprocess.TimeoutExpired:
        _notify("انتهى وقت الطباعة.", type="negative")
        return

    if result.returncode == 0:
        _notify("أُرسلت إلى الطابعة.", type="positive", timeout=3000)
        return

    # lp ran but rejected the job (commonly "No default destination").
    # Fall back to opening the PDF so the user can print interactively.
    err = (result.stderr or result.stdout).strip()
    _notify(
        f"تعذّر الإرسال إلى الطابعة. سيُفتح PDF.\n{err}",
        type="warning",
        multi_line=True,
        timeout=6000,
    )
    _open_in_default_editor(path)


async def _print_last_page() -> None:
    """Recompile only the last page (signatures) and send it to the printer.

    Uses typst's `--pages` flag against the saved main.typ to produce a
    one-page PDF in the meeting's _preview folder, then routes it through
    the same _print_pdf path as the full document. Works the same on
    every platform without needing a PDF-manipulation dep.
    """
    if _pdf_path is None or not _pdf_path.exists():
        _notify("لم يتم تصدير PDF بعد.", type="negative")
        return
    if not _preview_urls:
        _notify("لا توجد صفحات للطباعة.", type="negative")
        return
    main_typ = _pdf_path.parent / "main.typ"
    if not main_typ.is_file():
        _notify("main.typ غير موجود.", type="negative")
        return
    typst_bin = find_typst()
    if typst_bin is None:
        _notify("لم يتم العثور على برنامج typst.", type="negative")
        return

    page_count = len(_preview_urls)
    last_pdf = _pdf_path.parent / "_preview" / "last-page.pdf"
    last_pdf.parent.mkdir(exist_ok=True)

    font_args: list[str] = []
    bundled_fonts = typst_font_dir()
    if bundled_fonts is not None:
        font_args = ["--font-path", str(bundled_fonts), "--ignore-system-fonts"]

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                str(typst_bin),
                "compile",
                *font_args,
                "--pages",
                str(page_count),
                str(main_typ),
                str(last_pdf),
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            **_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        _notify("انتهى وقت تجهيز صفحة التواقيع.", type="negative")
        return

    if result.returncode != 0:
        msg = (result.stderr or result.stdout).strip()[:300]
        _notify(
            f"تعذّر تجهيز صفحة التواقيع: {msg}",
            type="negative",
            multi_line=True,
        )
        return

    _print_pdf(last_pdf)


def _open_in_default_editor(path: Path) -> None:
    """Open path in the OS-default handler — usually the user's text editor
    for .json. No third-party dep: os.startfile on Windows, `open` on macOS,
    `xdg-open` on Linux."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        _notify(f"تعذّر الفتح: {exc}", type="negative")


def _open_settings(info: dict) -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[480px] max-w-[640px]"):
        ui.label("الإعدادات").classes("text-lg font-semibold")

        # Meetings root — the only field worth a structured input. Theme
        # has its own header button; recent_meetings has the history menu.
        with ui.column().classes("w-full gap-1"):
            ui.label("مجلد الاجتماعات").classes("text-sm text-gray-500")
            with ui.row().classes("w-full items-center gap-2"):
                root_input = ui.input(value=str(load_meetings_root())).classes("flex-1")

                async def _browse() -> None:
                    import webview

                    win = app.native.main_window
                    if win is None:
                        _notify(
                            "التصفّح متاح في الوضع الأصلي فقط.",
                            type="negative",
                        )
                        return
                    dialog_type = (
                        webview.FileDialog.FOLDER
                        if hasattr(webview, "FileDialog")
                        else webview.FOLDER_DIALOG
                    )
                    start = root_input.value or str(Path.home())
                    paths = await win.create_file_dialog(
                        dialog_type=dialog_type, directory=start
                    )
                    if paths:
                        root_input.value = paths[0]

                ui.button(icon="folder_open", on_click=_browse).props(
                    "flat dense"
                ).tooltip("تصفّح")
            ui.label(
                "الموقع الافتراضي لحفظ المحاضر الجديدة. لن تُنقل المحاضر الموجودة."
            ).classes("text-xs text-gray-400")

        # Escape hatch for anything not exposed in the form.
        with ui.column().classes("w-full gap-1 mt-3"):
            ui.label("ملف الإعدادات").classes("text-sm text-gray-500")
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(info["config_path"]).classes(
                    "flex-1 text-xs font-mono break-all"
                )
                ui.button(
                    icon="open_in_new",
                    on_click=lambda: _open_in_default_editor(Path(info["config_path"])),
                ).props("flat dense").tooltip("فتح في المحرر الافتراضي")

        with ui.row().classes("w-full justify-end mt-3 gap-2"):
            ui.button("إلغاء", on_click=dialog.close).props("flat")

            def _save() -> None:
                new_root = (root_input.value or "").strip()
                if not new_root:
                    _notify("الرجاء إدخال مسار صالح.", type="negative")
                    return
                save_meetings_root(new_root)
                _notify("تم حفظ الإعدادات.", type="positive")
                dialog.close()

            ui.button("حفظ", on_click=_save).props("unelevated color=primary")
    dialog.open()


def _open_about(info: dict) -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px] max-w-[500px]"):
        icon_url = _get_icon_url()
        if icon_url:
            with ui.row().classes("w-full justify-center"):
                ui.image(icon_url).classes("w-24 h-24")
        rows = [
            ("الإصدار", info["version"]),
            ("Python", info["python_version"]),
            ("النظام", info["platform"]),
            ("ملف الإعدادات", info["config_path"]),
        ]
        with ui.row().classes("w-full justify-center"):
            ui.label(info["name"]).classes("text-lg font-semibold")
        for key, value in rows:
            with ui.row().classes("w-full justify-between items-center gap-4"):
                ui.label(key).classes("text-sm text-gray-500")
                ui.label(value).classes("text-sm")
        with ui.row().classes("w-full justify-between items-center gap-4"):
            ui.label("المصدر").classes("text-sm text-gray-500")
            link = ui.label(info["repo_url"]).classes(
                "text-sm text-primary cursor-pointer underline"
            )
            link.on("click", lambda: webbrowser.open(info["repo_url"]))
        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(
                "اختصارات لوحة المفاتيح",
                icon="keyboard",
                on_click=_show_shortcuts,
            ).props("flat")
            ui.button("إغلاق", on_click=dialog.close).props("unelevated")
    dialog.open()


@ui.page("/")
def _index() -> None:
    info = get_app_info()
    is_dark = load_theme() == "dark"
    dark = ui.dark_mode(value=is_dark)
    _apply_theme_colors(is_dark)

    def toggle_theme() -> None:
        new_value = not dark.value
        dark.set_value(new_value)
        _apply_theme_colors(new_value)
        save_theme("dark" if new_value else "light")
        theme_btn.props(f"icon={'light_mode' if new_value else 'dark_mode'}")

    def _open_recent_menu() -> None:
        if _recent_menu is None:
            return
        if _recent_menu_refresh is not None:
            _recent_menu_refresh()
        _recent_menu.open()

    def _switch_tab(name: str) -> None:
        if _tabs_ref is not None:
            _tabs_ref.set_value(name)

    def _has_pdf() -> bool:
        return _pdf_path is not None and _pdf_path.exists()

    # Single source of truth for every keyboard-shortcut and palette entry.
    # Order here is also the palette order when no query is typed.
    global _commands
    _commands = [
        _Command(
            "موضوع جديد", lambda: _add_article(_meeting),
            ("Ctrl", "N"), icon="add",
        ),
        _Command(
            "اجتماع جديد", _new_meeting,
            ("Ctrl", "Shift", "N"), icon="note_add",
        ),
        _Command(
            "حفظ", _save_current_meeting,
            ("Ctrl", "S"), icon="save",
        ),
        _Command(
            "فتح", _open_meeting,
            ("Ctrl", "O"), icon="folder_open",
        ),
        _Command(
            "الاجتماعات الأخيرة", _open_recent_menu,
            ("Ctrl", "R"),
            when=lambda: bool(load_recent_meetings()),
            icon="history",
        ),
        _Command(
            "نسخ كقالب", _duplicate_as_template,
            ("Ctrl", "D"), icon="content_copy",
        ),
        _Command(
            "فتح مجلد الاجتماع", _open_meeting_folder,
            ("Ctrl", "F"), icon="folder",
        ),
        _Command(
            "التدقيق الإملائي لكل المواضيع",
            lambda: _check_all_articles(_meeting),
            ("Ctrl", "P"),
            when=lambda: bool(_meeting.articles),
            icon="spellcheck",
        ),
        _Command(
            "تصدير PDF", _compile_meeting,
            ("Ctrl", "E"), icon="picture_as_pdf",
        ),
        _Command(
            "بيانات الاجتماع", lambda: _switch_tab("front"),
            ("Ctrl", "1"), icon="event",
        ),
        _Command(
            "المواضيع", lambda: _switch_tab("articles"),
            ("Ctrl", "2"), icon="article",
        ),
        _Command(
            "الاعتماد", lambda: _switch_tab("end"),
            ("Ctrl", "3"), icon="verified",
        ),
        _Command(
            "معاينة PDF", lambda: _switch_tab("pdf"),
            ("Ctrl", "4"), icon="picture_as_pdf",
        ),
        # Commands without a default shortcut — palette-only:
        _Command(
            "تبديل المظهر", toggle_theme, icon="dark_mode",
        ),
        _Command(
            "الإعدادات", lambda: _open_settings(info), icon="settings",
        ),
        _Command(
            "حول التطبيق", lambda: _open_about(info), icon="info",
        ),
        _Command(
            "اختصارات لوحة المفاتيح", _show_shortcuts, icon="keyboard",
        ),
        _Command(
            "فتح PDF في القارئ الافتراضي",
            lambda: _open_in_default_editor(_pdf_path),
            when=_has_pdf, icon="open_in_new",
        ),
        _Command(
            "طباعة الكل", lambda: _print_pdf(_pdf_path),
            when=_has_pdf, icon="print",
        ),
        _Command(
            "طباعة آخر صفحة", _print_last_page,
            when=_has_pdf, icon="draw",
        ),
    ]

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-2"):
            icon_url = _get_icon_url()
            if icon_url:
                ui.image(icon_url).classes("w-8 h-8")
            ui.label(APP_DISPLAY_NAME).classes("text-lg font-semibold")
        with ui.row().classes("items-center gap-1"):
            _update_button()
            ui.button(icon="note_add", on_click=_new_meeting).props(
                "flat round dense color=white"
            ).tooltip("اجتماع جديد")
            ui.button(icon="folder_open", on_click=_open_meeting).props(
                "flat round dense color=white"
            ).tooltip("فتح")
            global _save_btn
            _save_btn = (
                ui.button(icon="save", on_click=_save_current_meeting)
                .props("flat round dense color=white")
                .tooltip("حفظ")
            )
            ui.button(icon="content_copy", on_click=_duplicate_as_template).props(
                "flat round dense color=white"
            ).tooltip("نسخ الاجتماع كقالب")
            recent_btn = (
                ui.button(icon="history")
                .props("flat round dense color=white")
                .tooltip("الاجتماعات الأخيرة")
            )
            with recent_btn:
                _recent_menu_local = ui.menu()
                with _recent_menu_local:

                    @ui.refreshable
                    def _recent_items() -> None:
                        recents = load_recent_meetings()
                        # Prune entries that no longer exist on disk so the
                        # menu only shows openable meetings. Side-effect: the
                        # stored list shrinks too, so the user doesn't have to
                        # × them away manually.
                        live = [p for p in recents if _is_meeting_dir(p)]
                        if len(live) != len(recents):
                            for dead in [p for p in recents if p not in live]:
                                remove_recent_meeting(dead)
                        recents = live
                        if not recents:
                            ui.label("لا توجد اجتماعات سابقة").classes(
                                "p-2 text-sm text-gray-500"
                            )
                            return
                        for p in recents:
                            with ui.menu_item(
                                on_click=lambda x=p: (
                                    _open_meeting_at(x),
                                    _recent_menu_local.close(),
                                )
                            ):
                                with ui.row().classes(
                                    "w-full items-center justify-between gap-3 min-w-[280px]"
                                ):
                                    ui.label(_meeting_label_for_path(p)).classes(
                                        "flex-1"
                                    )
                                    ui.button(icon="close").props(
                                        "flat round dense size=sm color=negative"
                                    ).tooltip("إزالة من القائمة").on(
                                        "click.stop",
                                        lambda e, x=p: (
                                            remove_recent_meeting(x),
                                            _recent_items.refresh(),
                                        ),
                                    )
                        ui.separator()
                        ui.menu_item(
                            "نسيان كل الاجتماعات",
                            on_click=lambda: (
                                clear_recent_meetings(),
                                _recent_items.refresh(),
                            ),
                        ).classes("text-negative")

                    _recent_items()
            global _recent_menu_refresh, _recent_menu
            _recent_menu_refresh = _recent_items.refresh
            _recent_menu = _recent_menu_local
            ui.button(icon="picture_as_pdf", on_click=_compile_meeting).props(
                "flat round dense color=white"
            ).tooltip("تصدير PDF")
            theme_btn = (
                ui.button(
                    icon="light_mode" if dark.value else "dark_mode",
                    on_click=toggle_theme,
                )
                .props("flat round dense color=white")
                .tooltip("تبديل المظهر")
            )
            ui.button(icon="settings", on_click=lambda: _open_settings(info)).props(
                "flat round dense color=white"
            ).tooltip("الإعدادات")
            ui.button(icon="info", on_click=lambda: _open_about(info)).props(
                "flat round dense color=white"
            ).tooltip("حول")

    global _tabs_ref, _front_tab, _articles_tab, _end_tab, _pdf_tab
    with ui.tabs().classes("w-full") as tabs:
        front_tab = ui.tab("front", label="معلومات الاجتماع", icon="event")
        articles_tab = ui.tab("articles", label="المواضيع", icon="article")
        end_tab = ui.tab("end", label="الاعتماد", icon="verified")
        pdf_tab = ui.tab("pdf", label="معاينة", icon="picture_as_pdf")
    _tabs_ref = tabs
    _front_tab, _articles_tab, _end_tab, _pdf_tab = (
        front_tab,
        articles_tab,
        end_tab,
        pdf_tab,
    )

    with ui.tab_panels(tabs, value=front_tab).classes("w-full"):
        with ui.tab_panel(front_tab):
            _front_matter_panel(_meeting)
        with ui.tab_panel(articles_tab):
            _articles_panel(_meeting)
        with ui.tab_panel(end_tab):
            _end_matter_panel(_meeting)
        with ui.tab_panel(pdf_tab):
            _pdf_view()

    global _status_label
    with ui.footer().classes("items-center gap-2 px-3 py-1 cursor-pointer") as footer:
        ui.icon("history").classes("text-sm")
        _status_label = ui.label("جاهز").classes("text-sm")
    footer.on("click", _show_history)

    _mark_clean()

    async def _handle_key(e) -> None:
        if not e.action.keydown:
            return
        if not e.modifiers.ctrl:
            return

        name = (e.key.name or "").lower()

        # Ctrl+Shift+P is the command palette itself — handled before the
        # generic registry lookup so it can't be shadowed by anything.
        if e.modifiers.shift and name == "p":
            _open_command_palette()
            return

        # Ctrl+↑/↓ is contextual (moves whichever article holds focus),
        # not a discrete registry command — keep the direct binding.
        if e.key.arrow_up and not e.modifiers.shift:
            await _move_focused_article(-1)
            return
        if e.key.arrow_down and not e.modifiers.shift:
            await _move_focused_article(+1)
            return

        # Registry-driven dispatch. A shortcut like ("Ctrl", "Shift", "N")
        # matches only when Shift is held; ("Ctrl", "N") matches only when
        # Shift is NOT held — so plain Ctrl+N and Ctrl+Shift+N stay
        # distinct without an explicit branch here.
        for cmd in _commands:
            if cmd.shortcut is None:
                continue
            keys = [k.lower() for k in cmd.shortcut]
            target = keys[-1]
            if target != name:
                continue
            wants_shift = "shift" in keys[:-1]
            if wants_shift != bool(e.modifiers.shift):
                continue
            if not cmd.is_active():
                return
            await _run_command(cmd)
            return

    ui.keyboard(on_key=_handle_key)
    ui.timer(0.7, _refresh_periodic_state)
    # Auto-update check — fire once a few seconds after the page has
    # settled so the network call doesn't fight with first paint.
    ui.timer(3.0, _check_for_update, once=True)
    # Pre-mount the recent meetings menu so the first user-triggered
    # open doesn't show Quasar's lazy-mount jitter. CSS keeps it
    # invisible during this warmup window.
    ui.timer(0.8, _warmup_recent_menu, once=True)


def _front_matter_panel(meeting: Meeting) -> None:
    with ui.column().classes("w-full gap-4 p-4"):
        ui.label("بيانات الاجتماع").classes("text-base font-semibold")
        with ui.grid(columns=2).classes("w-full gap-3"):
            # ui.select with with_input acts as a combobox: pre-populated
            #     dropdown from known committees (recent meetings +
            # article-template tags) plus free typing for new names.
            # new_value_mode='add-unique' lets the typed value become
            # the selected one without polluting the dropdown options
            # source (which only updates on the next render anyway).
            ui.select(
                options=_get_known_committees(),
                label="اسم المجلس / اللجنة",
                with_input=True,
                new_value_mode="add-unique",
            ).bind_value(meeting, "name").on_value_change(
                lambda _: _templates_menu_refresh()
                if _templates_menu_refresh is not None
                else None
            ).classes("w-full")
            with ui.column().classes("w-full gap-0"):
                num_input = (
                    ui.input(
                        "رقم الجلسة",
                        validation={
                            "يجب أن يكون عدداً صحيحاً": lambda v: not v
                            or v.strip().isdigit()
                        },
                    )
                    .bind_value(meeting, "number_num")
                    .classes("w-full")
                )
                word_preview = ui.label().classes("text-xs text-gray-500 px-2 pt-1")

                def _refresh_word_preview() -> None:
                    _sync_number_word(meeting)
                    word_preview.text = (
                        f"يكتب في المحضر: الجلسة {meeting.number}"
                        if meeting.number
                        else ""
                    )

                num_input.on_value_change(lambda _: _refresh_word_preview())
                _refresh_word_preview()
            # Date and time share one popup. The date input's text shows the
            # date; the picked time is reflected in the small preview label
            # below — both digital (for verification) and the Arabic prose
            # that gets written to the PDF.
            with ui.column().classes("w-full gap-0"):
                # Mutable holder so _refresh_time_preview can be defined
                # before the label exists. NiceGUI fires the time picker's
                # on_change during construction, so the refresh function has
                # to already be bound by then; the label gets attached later
                # and the function picks it up via this dict.
                _preview_holder: dict = {"label": None}

                def _refresh_time_preview() -> None:
                    _sync_time_phrase(meeting)
                    lbl = _preview_holder["label"]
                    if lbl is None:
                        return
                    if meeting.time_digital and meeting.time:
                        lbl.text = f"الوقت: {meeting.time_digital} ({meeting.time})"
                    elif meeting.time_digital:
                        lbl.text = f"الوقت: {meeting.time_digital}"
                    else:
                        lbl.text = ""

                with (
                    ui.input("التاريخ والوقت")
                    .bind_value(meeting, "date")
                    .classes("w-full") as date_input
                ):
                    with date_input.add_slot("append"):
                        datetime_icon = ui.icon("event").classes("cursor-pointer")
                        _date_picker_holder: dict = {"picker": None}

                        def _set_date_title(iso: str) -> None:
                            picker = _date_picker_holder["picker"]
                            if picker is None:
                                return
                            picker._props["title"] = _arabic_full_date(iso) or " "
                            picker.update()

                        def _on_date_change(e) -> None:
                            if not e.value:
                                return
                            meeting.date = _date_to_display(e.value)
                            _set_date_title(e.value)

                        # Dialog instead of q-menu: viewport-centered by
                        # default and rendered consistently on Linux/Windows.
                        # q-menu anchors to the icon element, whose pixel
                        # position drifts between QtWebEngine builds.
                        with ui.dialog() as datetime_dialog:
                            with (
                                ui.card()
                                .classes("p-2")
                                .style("max-width: none; width: max-content")
                            ):
                                with ui.row().classes("items-start gap-2 flex-nowrap"):
                                    date_picker = ui.date(
                                        value=_date_from_display(meeting.date),
                                        on_change=_on_date_change,
                                    )
                                    _date_picker_holder["picker"] = date_picker
                                    _set_date_title(_date_from_display(meeting.date))
                                    ui.time(
                                        on_change=lambda _: _refresh_time_preview()
                                    ).bind_value(meeting, "time_digital")
                                with ui.row().classes("w-full justify-end mt-2"):
                                    ui.button(
                                        "إغلاق", on_click=datetime_dialog.close
                                    ).props("flat")
                        datetime_icon.on("click", lambda: datetime_dialog.open())
                _preview_holder["label"] = ui.label().classes(
                    "text-xs text-gray-500 px-2 pt-1"
                )
                _refresh_time_preview()
            ui.input("السنة الأكاديمية").bind_value(meeting, "academic_year").classes(
                "w-full"
            )

        # ui.separator()

        with ui.row().classes("w-full items-center justify-between"):
            ui.label("الأعضاء").classes("text-base font-semibold")
            ui.button(icon="add", on_click=lambda: _add_member(meeting)).props(
                "dense unelevated"
            ).tooltip("إضافة عضو")
        _members_list(meeting)


@ui.refreshable
def _members_list(meeting: Meeting) -> None:
    if not meeting.members:
        ui.label("لا يوجد أعضاء بعد. اضغط + لإضافة عضو.").classes(
            "text-sm text-gray-500"
        )
        return
    with ui.column().classes("w-full gap-2"):
        for index, member in enumerate(meeting.members):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.label(f"{index + 1}.").classes("w-6 text-sm shrink-0")
                ui.input("الاسم").bind_value(member, "name").classes("flex-1")
                ui.input("الصفة").bind_value(member, "role").classes("w-28")
                ui.select(ATTENDANCE_OPTIONS).bind_value(member, "attendance").classes(
                    "w-28"
                )
                ui.input("سبب التغيب").bind_value(member, "excuse").classes("w-32")
                ui.button(
                    icon="delete",
                    on_click=lambda m=member: _remove_member(meeting, m),
                ).props("flat dense round color=negative").tooltip("حذف")


def _add_member(meeting: Meeting) -> None:
    meeting.members.append(Member())
    _members_list.refresh()
    _signatures_preview.refresh()


def _remove_member(meeting: Meeting, member: Member) -> None:
    meeting.members.remove(member)
    _members_list.refresh()
    _signatures_preview.refresh()


def _articles_panel(meeting: Meeting) -> None:
    with ui.column().classes("w-full gap-4 p-4"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("المواضيع").classes("text-base font-semibold")
            with ui.row().classes("items-center gap-2"):
                global _spellcheck_all_btn
                _spellcheck_all_btn = (
                    ui.button(
                        icon="spellcheck",
                        on_click=lambda: _check_all_articles(meeting),
                    )
                    .props("dense flat color=primary")
                    .tooltip("التدقيق الإملائي لكل المواضيع")
                )
                templates_btn = (
                    ui.button(icon="library_books")
                    .props("dense flat")
                    .tooltip("إضافة من قالب")
                )
                with templates_btn:
                    with ui.menu():
                        _templates_menu(meeting)
                ui.button(icon="add", on_click=lambda: _add_article(meeting)).props(
                    "dense unelevated"
                ).tooltip("إضافة موضوع فارغ")
        _articles_list(meeting)


def _templates_menu(meeting: Meeting) -> None:
    """Menu listing article templates scoped to the current committee.

    Wraps the actual rows in a @ui.refreshable so save/delete operations
    can repaint the list without re-creating the parent menu. Stores the
    refresh hook in _templates_menu_refresh so _open_save_article_template
    and the per-row delete handler can call it.
    """

    @ui.refreshable
    def _items() -> None:
        current = (meeting.name or "").strip()
        templates = [
            t
            for t in load_article_templates()
            if (t.get("committee") or "").strip() == current
        ]
        if not templates:
            ui.label("لا توجد قوالب لهذا المجلس بعد.").classes(
                "p-2 text-sm text-gray-500"
            )
            return
        for t in templates:
            with ui.menu_item(on_click=lambda tpl=t: _apply_template(meeting, tpl)):
                with ui.row().classes(
                    "w-full items-center justify-between gap-3 min-w-[280px]"
                ):
                    ui.label(t.get("name", "")).classes("flex-1")
                    ui.button(icon="close").props(
                        "flat round dense size=sm color=negative"
                    ).tooltip("حذف القالب").on(
                        "click.stop",
                        lambda e, tpl=t: (
                            remove_article_template(
                                tpl.get("name", ""),
                                tpl.get("committee", ""),
                            ),
                            _items.refresh(),
                        ),
                    )

    _items()
    global _templates_menu_refresh
    _templates_menu_refresh = _items.refresh


def _get_known_committees() -> list[str]:
    """Committee names harvested from recent meetings and saved templates.

    Recent meetings sit at `<root>/<committee>/<year>/<number>`, so
    `parts[-3]` of each saved path is the committee. Template entries also
    carry a committee tag. Returned sorted with empties dropped.
    """
    names: set[str] = set()
    for p in load_recent_meetings():
        parts = Path(p).parts
        if len(parts) >= 3:
            name = parts[-3].strip()
            if name:
                names.add(name)
    for t in load_article_templates():
        c = (t.get("committee") or "").strip()
        if c:
            names.add(c)
    return sorted(names)


def _get_known_targets() -> list[str]:
    """Candidates for the article 'الجهة ذات العلاقة' combobox.

    Pulls from three sources: targets persisted to config.json on save,
    target values stored on article templates, and any targets already
    typed into the current meeting's other articles. Sorted, deduped,
    empties dropped.
    """
    names: set[str] = set(load_known_targets())
    for t in load_article_templates():
        v = (t.get("target") or "").strip()
        if v:
            names.add(v)
    for a in _meeting.articles:
        v = (a.target or "").strip()
        if v:
            names.add(v)
    return sorted(names)


def _apply_template(meeting: Meeting, template: dict) -> None:
    """Spawn a new article from a template.

    Only legal_refs and target are populated from the template — the user
    fills in title, body, decision per-meeting. Title/body/decision exist
    as empty keys in the stored JSON so anyone editing config.json can
    pre-fill them if they want to expand the template later.
    """
    article = Article()
    article.legal_refs = template.get("legal_refs", "") or ""
    article.target = template.get("target", "") or "المجلس العلمي"
    # title / body / decision deliberately left at their dataclass defaults
    meeting.articles.append(article)
    _articles_list.refresh()
    _notify(
        f"أُضيف موضوع من القالب: {template.get('name', '')}",
        type="positive",
        timeout=3000,
    )


def _open_save_article_template(article: Article, meeting: Meeting) -> None:
    """Dialog to save the current article's legal_refs/target as a template.

    Title, body and decision are saved as empty strings — they're meeting-
    specific and should be filled in per use. The empty keys stay in the
    JSON so a user editing config.json can populate them if they want.
    """
    suggested = (article.title or "").strip()[:60]
    committee = (meeting.name or "").strip()

    with ui.dialog() as dialog, ui.card().classes("min-w-[380px] max-w-[560px]"):
        ui.label("حفظ كقالب").classes("text-lg font-semibold")
        name_input = ui.input("اسم القالب", value=suggested).classes("w-full")
        with ui.row().classes("w-full items-center gap-2 text-sm text-gray-500"):
            ui.icon("groups").classes("text-base")
            ui.label(f"المجلس: {committee or '(غير محدد)'}")
        ui.label(
            "سيُحفظ المستند والجهة فقط. "
            "العنوان والوصف والقرار يبقون فارغين لتعبئتهم لكل اجتماع."
        ).classes("text-xs text-gray-500")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("إلغاء", on_click=dialog.close).props("flat")

            def _save() -> None:
                name = (name_input.value or "").strip()
                if not name:
                    _notify("الرجاء إدخال اسم للقالب.", type="negative")
                    return
                add_article_template(
                    name=name,
                    committee=committee,
                    title="",
                    body="",
                    decision="",
                    legal_refs=article.legal_refs or "",
                    target=article.target or "",
                )
                if _templates_menu_refresh is not None:
                    _templates_menu_refresh()
                dialog.close()
                _notify(
                    f"حُفظ القالب: {name}",
                    type="positive",
                    timeout=3000,
                )

            ui.button("حفظ", on_click=_save).props("unelevated color=primary")
    dialog.open()


async def _check_all_articles(meeting: Meeting) -> None:
    """Spell-check every article body in one sweep, updating header colours.

    Runs the analyzer off-thread per article so the WebSocket heartbeat
    keeps ticking. Doesn't open a dialog — the only feedback is the row
    headers turning green/yellow as each article completes, plus a
    summary toast at the end.
    """
    if not meeting.articles:
        _notify("لا توجد مواضيع للفحص.", type="warning", timeout=2000)
        return

    # Show the progress toast BEFORE any heavy work so the user gets
    # immediate feedback. camel_available() can take 1-2s on first call
    # (triggers the morphology DB load); don't make that delay the
    # "جارٍ فحص..." notification. asyncio.sleep(0) yields to the loop so
    # the notify message flushes to the browser before we start checking.
    total = len(meeting.articles)
    _notify(f"جارٍ فحص {total} موضوعاً...", type="info", timeout=4000)
    await asyncio.sleep(0)

    if not await asyncio.to_thread(camel_available):
        _notify(
            "قاعدة بيانات الصرف غير متوفرة. "
            "نزّلها بأمر: camel_data -i morphology-db-msa-r13",
            type="negative",
            multi_line=True,
        )
        return

    # Use each article's persisted personal dictionary so the bulk pass
    # respects the same exclusions the per-article dialog would.
    personal_dict = load_personal_dict()
    clean_count = 0
    issue_count = 0

    for article in meeting.articles:
        issues = await asyncio.to_thread(
            camel_check_text, article.body or "", personal_dict
        )
        n = len(issues)
        _set_article_status(article, n)
        if n == 0:
            clean_count += 1
        else:
            issue_count += n

    _articles_list.refresh()
    if issue_count == 0:
        _set_spellcheck_all_status("clean")
        _notify(
            f"اكتمل الفحص: {total} موضوعاً، لا توجد أخطاء.",
            type="positive",
            timeout=4000,
        )
    else:
        _set_spellcheck_all_status("errors")
        _notify(
            f"اكتمل الفحص: {clean_count}/{total} نظيف.",
            type="warning",
            timeout=5000,
        )


@ui.refreshable
def _articles_list(meeting: Meeting) -> None:
    if not meeting.articles:
        ui.label("لا توجد مواضيع بعد. اضغط + لإضافة موضوع.").classes(
            "text-sm text-gray-500"
        )
        return
    for index, article in enumerate(meeting.articles):
        with ui.row().classes("w-full items-stretch gap-0 no-wrap"):
            handle = (
                ui.element("div")
                .classes("flex items-center px-1 cursor-grab text-gray-400")
                .props("draggable=true")
            )
            with handle:
                ui.icon("drag_indicator")
            handle.on("dragstart", lambda e, i=index: _drag_state.update({"from": i}))
            drop_target = ui.element("div").classes(
                f"flex-1 min-w-0 rounded article-row-{index}"
            )
            drop_target.on("dragover.prevent", lambda e: None)
            drop_target.on(
                "dragenter", lambda e, el=drop_target: el.classes(add="bg-blue-1")
            )
            drop_target.on(
                "dragleave", lambda e, el=drop_target: el.classes(remove="bg-blue-1")
            )
            drop_target.on(
                "drop",
                lambda e, i=index, el=drop_target: (
                    el.classes(remove="bg-blue-1"),
                    _on_article_drop(i),
                ),
            )
            with drop_target:
                title_preview = (article.title or f"موضوع جديد {index + 1}").strip()
                # Header colour reflects spell-check status (set in the
                # dialog): blue=unchecked, green=clean, yellow=has errors.
                status_class = _article_status_class(article)
                with (
                    ui.expansion(f"{index + 1}. {title_preview}", icon="description")
                    .props(f'header-class="text-weight-bold {status_class}"')
                    .classes("w-full")
                ):
                    with ui.column().classes("w-full gap-3 p-2"):
                        ui.input("العنوان").bind_value(article, "title").classes(
                            "w-full"
                        )
                        # Unique class on the textarea so the JS helpers
                        # below can target the right body when there are
                        # several articles open.
                        art_class = f"article-body-{index}"
                        with ui.row().classes("gap-1 items-center"):
                            ui.button(
                                icon="format_bold",
                                on_click=lambda c=art_class: ui.run_javascript(
                                    f"articleBodyWrap('{c}', '*', '*')"
                                ),
                            ).props("flat dense").tooltip("غامق")
                            ui.button(
                                icon="format_italic",
                                on_click=lambda c=art_class: ui.run_javascript(
                                    f"articleBodyWrap('{c}', '_', '_')"
                                ),
                            ).props("flat dense").tooltip("مائل")
                            ui.button(
                                icon="format_list_bulleted",
                                on_click=lambda c=art_class: ui.run_javascript(
                                    f"articleBodyLine('{c}', '- ')"
                                ),
                            ).props("flat dense").tooltip("قائمة نقطية")
                            ui.button(
                                icon="format_list_numbered",
                                on_click=lambda c=art_class: ui.run_javascript(
                                    f"articleBodyLine('{c}', '+ ')"
                                ),
                            ).props("flat dense").tooltip("قائمة مرقمة")
                            ui.button(
                                icon="link",
                                on_click=lambda c=art_class: ui.run_javascript(
                                    f"articleBodyLink('{c}')"
                                ),
                            ).props("flat dense").tooltip("رابط")
                            ui.button(
                                icon="spellcheck",
                                on_click=lambda a=article: _open_spell_check(a),
                            ).props("flat dense").tooltip("التدقيق الإملائي")
                        ui.textarea("الوصف والمناقشة").bind_value(
                            article, "body"
                        ).props(f'rows=4 autogrow input-class="{art_class}"').classes(
                            "w-full"
                        )
                        ui.textarea("القرار / التوصية").bind_value(
                            article, "decision"
                        ).props("rows=2 autogrow").classes("w-full")
                        ui.textarea("مستند القرار").bind_value(
                            article, "legal_refs"
                        ).props("rows=2 autogrow").classes("w-full")
                        # Combobox over previously-used targets (config-
                        # persisted, harvested at save time) plus template
                        # targets and any target typed elsewhere in this
                        # meeting. Free text still allowed via with_input
                        # + add-unique, same as the committee field.
                        ui.select(
                            options=_get_known_targets(),
                            label="الجهة ذات العلاقة",
                            with_input=True,
                            new_value_mode="add-unique",
                        ).bind_value(article, "target").classes("w-full")

                        tables = find_tables(article.body)
                        with ui.expansion(
                            f"الجداول ({len(tables)})", icon="grid_on"
                        ).classes("w-full"):
                            if tables:
                                for ti, t in enumerate(tables):
                                    rows = max(1, len(t.cells) // max(1, t.columns))
                                    with ui.row().classes(
                                        "w-full items-center justify-between gap-2"
                                    ):
                                        ui.label(
                                            f"الجدول {ti + 1} — {rows}×{t.columns}"
                                        ).classes("text-sm")
                                        ui.button(
                                            "تعديل",
                                            icon="edit",
                                            on_click=lambda a=article,
                                            idx=ti: _open_table_editor(
                                                a, idx, _articles_list.refresh
                                            ),
                                        ).props("flat dense")
                            else:
                                ui.label("لا يوجد جداول.").classes(
                                    "text-sm text-gray-500"
                                )
                            ui.button(
                                "إضافة جدول",
                                icon="add",
                                on_click=lambda a=article: _open_table_editor(
                                    a, None, _articles_list.refresh
                                ),
                            ).props("dense unelevated").classes("mt-2")

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(
                                "حفظ كقالب",
                                icon="library_add",
                                on_click=(
                                    lambda a=article,
                                    m=meeting: _open_save_article_template(a, m)
                                ),
                            ).props("flat")
                            ui.button(
                                "حذف الموضوع",
                                icon="delete",
                                on_click=lambda a=article: _confirm(
                                    "تأكيد الحذف",
                                    f"هل تريد حذف الموضوع: «{(a.title.strip() or 'بدون عنوان')[:80]}»؟",
                                    lambda art=a: _remove_article(meeting, art),
                                ),
                            ).props("flat color=negative")


def _open_table_editor(article: Article, table_index: int | None, on_save) -> None:
    if table_index is None:
        spec = new_table(rows=2, columns=3)
    else:
        tables = find_tables(article.body)
        if table_index >= len(tables):
            _notify("الجدول غير موجود.", type="negative")
            return
        spec = tables[table_index]

    with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full h-full"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(
                "محرر الجدول"
                if table_index is None
                else f"تعديل الجدول {table_index + 1}"
            ).classes("text-lg font-semibold")
            ui.label(
                'تلميح: المحتوى داخل الخلية يُلفّ بـ [ ] تلقائياً. التعابير الخام (مثل strong("..")) تبقى كما هي.'
            ).classes("text-xs text-gray-500")

        @ui.refreshable
        def grid_view() -> None:
            # NB: do NOT pad spec.cells here. serialize_table already keeps
            # the last row rectangular (with colspan-aware math), and any
            # mutation we do at render time leaks into the saved table —
            # every open of a table with a merged cell would otherwise
            # accumulate phantom rows.
            template = columns_to_css(spec.prelude_args, spec.columns)
            with ui.element("div").style(
                f"display: grid; grid-template-columns: {template}; "
                "gap: 4px; width: 100%;"
            ):
                for i, cell in enumerate(spec.cells):
                    cell_style = ""
                    if cell.colspan > 1:
                        # Span the CSS grid so the textarea visually
                        # occupies the merged width, matching how the
                        # cell renders in the final typst output.
                        cell_style = f"grid-column: span {cell.colspan};"
                    ui.textarea(
                        value=cell.content,
                        on_change=lambda e, idx=i: setattr(
                            spec.cells[idx], "content", e.value
                        ),
                    ).props('autogrow dense outlined dir="auto"').style(
                        cell_style
                    ).classes("w-full tbl-cell")

        grid_view()

        with ui.row().classes("w-full items-center gap-2 mt-3"):
            ui.button(
                "صف +",
                icon="add",
                on_click=lambda: (add_row(spec), grid_view.refresh()),
            ).props("dense outline")
            ui.button(
                "صف -",
                icon="remove",
                on_click=lambda: (remove_row(spec), grid_view.refresh()),
            ).props("dense outline")
            ui.button(
                "عمود +",
                icon="add",
                on_click=lambda: (add_column(spec), grid_view.refresh()),
            ).props("dense outline")
            ui.button(
                "عمود -",
                icon="remove",
                on_click=lambda: (remove_column(spec), grid_view.refresh()),
            ).props("dense outline")

        def _save() -> None:
            new_src = serialize_table(spec)
            if table_index is None:
                article.body = append_table_to_body(article.body, new_src)
            else:
                article.body = replace_table_in_body(article.body, spec, new_src)
            on_save()
            dialog.close()
            _notify(
                "تم إضافة الجدول." if table_index is None else "تم تحديث الجدول.",
                type="positive",
            )

        with ui.row().classes("w-full justify-end gap-2 mt-3"):
            ui.button("إلغاء", on_click=dialog.close).props("flat")
            ui.button("حفظ", on_click=_save).props("unelevated color=primary")

    dialog.open()


def _add_article(meeting: Meeting) -> None:
    meeting.articles.append(Article())
    _articles_list.refresh()


async def _open_spell_check(article: Article) -> None:
    """Arabic spell-check dialog backed by CAMeL Tools.

    Heavy work (initial morphology DB load, per-check analysis, suggestion
    generation) runs in a worker thread so the WebSocket heartbeat doesn't
    time out. Suggestions are lazy-loaded per word — clicking 'اقتراحات'
    on a row triggers generation only for that word. Apply/ignore edits
    the in-memory list in place (no full re-check) so the dialog stays
    snappy.
    """
    # Immediate feedback — the first camel_available() call triggers a
    # 1-2s morphology DB load, which otherwise leaves the user staring
    # at a frozen UI before the dialog opens. yield once so NiceGUI
    # flushes the toast before we start the blocking work.
    _notify("جارٍ فحص الموضوع...", type="info", timeout=4000)
    await asyncio.sleep(0)

    if not await asyncio.to_thread(camel_available):
        _notify(
            "قاعدة بيانات الصرف غير متوفرة. "
            "نزّلها بأمر: camel_data -i morphology-db-msa-r13",
            type="negative",
            multi_line=True,
        )
        return

    # `ignored` is session-only; `personal_dict` persists across sessions
    # via config.json. The check treats them the same — both skip the
    # analyzer entirely — so we union them when running the check.
    ignored: set[str] = set()
    personal_dict: set[str] = load_personal_dict()
    state: dict = {"issues": [], "loading": True, "loading_word": None}
    suggestion_cache: dict[str, list[str]] = {}

    with ui.dialog() as dialog, ui.card().classes("min-w-[480px] max-w-[720px]"):
        ui.label("التدقيق الإملائي").classes("text-lg font-semibold")

        @ui.refreshable
        def _issues_panel() -> None:
            if state["loading"]:
                with ui.row().classes("items-center gap-2 p-4"):
                    ui.spinner()
                    ui.label("جارٍ الفحص...").classes("text-sm")
                return
            issues = state["issues"]
            if not issues:
                ui.label("لا توجد أخطاء إملائية.").classes("text-sm text-positive")
                return
            ui.label(f"عدد المشاكل: {len(issues)}").classes("text-sm text-gray-500")
            with ui.column().classes("w-full gap-2 max-h-[60vh] overflow-y-auto"):
                for issue in issues:
                    with ui.row().classes(
                        "w-full items-center gap-2 border-b py-2 no-wrap"
                    ):
                        ui.label(issue.word).classes("font-bold shrink-0 w-32 truncate")
                        with ui.row().classes("flex-1 flex-wrap gap-1 items-center"):
                            if issue.word in suggestion_cache:
                                sugs = suggestion_cache[issue.word]
                                if sugs:
                                    for sug in sugs:
                                        ui.button(
                                            sug,
                                            on_click=(
                                                lambda s=sug, iss=issue: _apply(s, iss)
                                            ),
                                        ).props("flat dense no-caps")
                                else:
                                    ui.label("لا توجد اقتراحات").classes(
                                        "text-xs text-gray-500"
                                    )
                            elif state["loading_word"] == issue.word:
                                ui.spinner().classes("text-sm")
                            else:
                                ui.button(
                                    "اقتراحات",
                                    icon="auto_fix_high",
                                    on_click=(lambda iss=issue: _load_suggestions(iss)),
                                ).props("flat dense no-caps")
                        ui.button(
                            icon="library_add",
                            on_click=(lambda w=issue.word: _add_to_dict(w)),
                        ).props("flat dense round").tooltip(
                            "إضافة للقاموس الخاص"
                        ).classes("shrink-0")
                        ui.button(
                            "تجاهل",
                            on_click=(lambda w=issue.word: _ignore(w)),
                        ).props("flat dense").classes("shrink-0")

        async def _run_check() -> None:
            state["loading"] = True
            _issues_panel.refresh()
            issues = await asyncio.to_thread(
                camel_check_text,
                article.body or "",
                ignored | personal_dict,
            )
            state["issues"] = issues
            state["loading"] = False
            _set_article_status(article, len(issues))
            _issues_panel.refresh()

        async def _load_suggestions(issue) -> None:
            if issue.word in suggestion_cache:
                return
            state["loading_word"] = issue.word
            _issues_panel.refresh()
            sugs = await asyncio.to_thread(
                camel_get_suggestions,
                issue.word,
                5,
            )
            suggestion_cache[issue.word] = sugs
            state["loading_word"] = None
            _issues_panel.refresh()

        def _apply(suggestion: str, issue) -> None:
            body = article.body or ""
            if not (
                0 <= issue.start <= len(body)
                and body[issue.start : issue.end] == issue.word
            ):
                _notify("تغيّر النص. أعد الفحص.", type="warning")
                return
            delta = len(suggestion) - len(issue.word)
            article.body = body[: issue.start] + suggestion + body[issue.end :]
            for other in state["issues"]:
                if other.start > issue.start:
                    other.start += delta
                    other.end += delta
            state["issues"].remove(issue)
            _set_article_status(article, len(state["issues"]))
            _issues_panel.refresh()

        def _ignore(word: str) -> None:
            ignored.add(word)
            state["issues"] = [i for i in state["issues"] if i.word != word]
            _set_article_status(article, len(state["issues"]))
            _issues_panel.refresh()

        def _add_to_dict(word: str) -> None:
            personal_dict.add(word)
            add_to_personal_dict(word)
            state["issues"] = [i for i in state["issues"] if i.word != word]
            _set_article_status(article, len(state["issues"]))
            _issues_panel.refresh()
            _notify(f"أُضيف للقاموس: {word}", type="positive", timeout=2000)

        _issues_panel()

        with ui.row().classes("w-full justify-end mt-2 gap-2"):
            ui.button(
                "إعادة الفحص",
                icon="refresh",
                on_click=lambda: _run_check(),
            ).props("flat")
            ui.button("إغلاق", on_click=dialog.close).props("unelevated")

    # When the dialog is dismissed (button, X, click-outside) refresh the
    # articles list so the expansion header picks up the new status colour.
    dialog.on("hide", lambda _: _articles_list.refresh())
    dialog.open()
    await _run_check()


def _remove_article(meeting: Meeting, article: Article) -> None:
    meeting.articles.remove(article)
    _articles_list.refresh()


def _end_matter_panel(meeting: Meeting) -> None:
    with ui.column().classes("w-full gap-4 p-4"):
        ui.label("الاعتماد").classes("text-base font-semibold")
        ui.textarea("نص التوصية").bind_value(meeting, "approval_text").props(
            "rows=2 autogrow"
        ).classes("w-full")

        # ui.separator()

        ui.label("جدول التوقيعات").classes("text-base font-semibold")
        ui.label("يُبنى تلقائياً من قائمة الأعضاء في تبويب «معلومات الاجتماع».").classes(
            "text-sm text-gray-500"
        )
        _signatures_preview(meeting)

        # ui.separator()

        ui.label("المدعوين").classes("text-base font-semibold")
        ui.textarea().bind_value(meeting, "invitees").props("rows=3 autogrow").classes(
            "w-full"
        )
        ui.label("الإضافات والملحوظات").classes("text-base font-semibold")
        ui.textarea().bind_value(meeting, "closing_notes").props(
            "rows=4 autogrow"
        ).classes("w-full")


@ui.refreshable
def _signatures_preview(meeting: Meeting) -> None:
    if not meeting.members:
        ui.label("لا يوجد أعضاء.").classes("text-sm text-gray-500")
        return
    columns = [
        {"name": "n", "label": "م", "field": "n", "align": "center"},
        {"name": "name", "label": "الاسم", "field": "name", "align": "right"},
        {
            "name": "signature",
            "label": "التوقيع",
            "field": "signature",
            "align": "center",
        },
    ]
    rows = [
        {"n": str(i + 1), "name": m.name or "(بدون اسم)", "signature": ""}
        for i, m in enumerate(meeting.members)
    ]
    ui.table(columns=columns, rows=rows, row_key="n").classes("w-full")


def run_webapp() -> None:
    if sys.platform.startswith("linux"):
        os.environ.setdefault("PYWEBVIEW_GUI", "qt")
    ui.run(
        native=True,
        title=APP_DISPLAY_NAME,
        window_size=(900, 700),
        language="ar",
        reload=False,
        show=False,
    )
