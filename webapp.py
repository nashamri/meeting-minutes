import asyncio
import hashlib
import os
import re
import subprocess
import sys
import time
import webbrowser
from datetime import date as _date_cls
from pathlib import Path

from nicegui import app, ui

from main import (
    APP_DISPLAY_NAME,
    add_recent_meeting,
    clear_recent_meetings,
    get_app_info,
    load_meetings_root,
    load_recent_meetings,
    load_theme,
    remove_recent_meeting,
    save_meetings_root,
    save_theme,
)
from models import ATTENDANCE_OPTIONS, Article, Meeting, Member
from typst_io import read_meeting, write_meeting
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
_tabs_ref = None
_status_label = None
_status_history: list[dict] = []
_drag_state: dict = {"from": None}
_save_btn = None
_front_tab = None
_articles_tab = None
_end_tab = None
_pdf_tab = None
_recent_menu_refresh = None
_recent_menu = None
_saved_fingerprint: str | None = None
_icon_url: str | None = None


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
        parts.extend([
            mem.name or "", mem.role or "",
            mem.attendance or "", mem.excuse or "",
        ])
    for art in m.articles:
        parts.extend([
            art.title or "", art.body or "", art.decision or "",
            art.legal_refs or "", art.target or "",
        ])
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
    1: "الأولى", 2: "الثانية", 3: "الثالثة", 4: "الرابعة", 5: "الخامسة",
    6: "السادسة", 7: "السابعة", 8: "الثامنة", 9: "التاسعة", 10: "العاشرة",
}
_AR_ONES_FEM_FOR_COMPOUND = {
    1: "الحادية", 2: "الثانية", 3: "الثالثة", 4: "الرابعة", 5: "الخامسة",
    6: "السادسة", 7: "السابعة", 8: "الثامنة", 9: "التاسعة",
}
_AR_TENS = {
    20: "العشرون", 30: "الثلاثون", 40: "الأربعون", 50: "الخمسون",
    60: "الستون", 70: "السبعون", 80: "الثمانون", 90: "التسعون",
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
    1: "الواحدة", 2: "الثانية", 3: "الثالثة", 4: "الرابعة",
    5: "الخامسة", 6: "السادسة", 7: "السابعة", 8: "الثامنة",
    9: "التاسعة", 10: "العاشرة", 11: "الحادية عشرة", 12: "الثانية عشرة",
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
    0: "الإثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس",
    4: "الجمعة", 5: "السبت", 6: "الأحد",
}
_AR_MONTHS_FULL = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل",
    5: "مايو", 6: "يونيو", 7: "يوليو", 8: "أغسطس",
    9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
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
    ui.add_head_html(
        f"<style>{faces} {body_rule} {resize_rule} {table_cell_rule} "
        f"{dark_header_rule} {notify_click_rule}</style>"
        f"<script>{notify_dismiss_js}</script>"
        f"<script>{article_body_js}</script>",
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
    _members_list.refresh()
    _articles_list.refresh()
    _signatures_preview.refresh()
    add_recent_meeting(src)
    if _recent_menu_refresh is not None:
        _recent_menu_refresh()
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
    _members_list.refresh()
    _articles_list.refresh()
    _signatures_preview.refresh()
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

            ui.button("تجاهل وابدأ", on_click=_ok).props(
                "unelevated color=warning"
            )
    dialog.open()


def _apply_duplicate_template() -> None:
    """Clear per-meeting fields, keep council/template fields.

    Kept: name (committee), academic_year, members, approval_text — these
    stay constant across meetings of the same body. Cleared: number/date/
    time/articles/invitees/closing_notes — these are filled in fresh.
    """
    global _preview_urls
    _meeting.number = ""
    _meeting.number_num = ""
    _meeting.date = ""
    _meeting.time = ""
    _meeting.time_digital = ""
    _meeting.articles = []
    _meeting.invitees = ""
    _meeting.closing_notes = ""
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


def _save_current_meeting() -> Path | None:
    dest = _resolve_meeting_dest()
    if dest is None:
        return None
    try:
        write_meeting(_meeting, dest)
    except OSError as exc:
        _notify(f"تعذّر الحفظ: {exc}", type="negative")
        return None
    add_recent_meeting(dest)
    if _recent_menu_refresh is not None:
        _recent_menu_refresh()
    _mark_clean()
    _notify(f"حُفظ في: {dest}", type="positive", timeout=5000)
    return dest


async def _compile_meeting() -> None:
    dest = _save_current_meeting()
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

    try:
        pdf_result = await asyncio.to_thread(
            subprocess.run,
            [str(typst_bin), "compile", *font_args, str(main_typ), str(pdf_path)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
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
    with ui.column().classes("w-full items-center gap-3 p-2"):
        for url in _preview_urls:
            ui.image(url).classes("max-w-3xl w-full border rounded shadow-sm")


_SHORTCUTS = [
    ("اجتماع جديد", ["Ctrl", "N"]),
    ("حفظ", ["Ctrl", "S"]),
    ("فتح", ["Ctrl", "O"]),
    ("الاجتماعات الأخيرة", ["Ctrl", "R"]),
    ("نسخ كقالب", ["Ctrl", "D"]),
    ("فتح مجلد الاجتماع", ["Ctrl", "F"]),
    ("تصدير PDF", ["Ctrl", "E"]),
]


def _kbd_html(keys: list[str]) -> str:
    style = (
        "border:1px solid var(--q-primary,#999); border-radius:4px;"
        "padding:1px 6px; font-family:monospace; font-size:0.85em;"
        "background:rgba(0,0,0,0.04);"
    )
    return ' <span style="opacity:0.5;">+</span> '.join(
        f'<kbd style="{style}">{k}</kbd>' for k in keys
    )


def _show_shortcuts() -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px] max-w-[480px]"):
        ui.label("اختصارات لوحة المفاتيح").classes("text-lg font-semibold")
        for action, keys in _SHORTCUTS:
            with ui.row().classes("w-full justify-between items-center gap-4"):
                ui.label(action).classes("text-sm")
                ui.html(_kbd_html(keys))
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("إغلاق", on_click=dialog.close).props("unelevated")
    dialog.open()


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
                root_input = ui.input(value=str(load_meetings_root())).classes(
                    "flex-1"
                )

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
                    on_click=lambda: _open_in_default_editor(
                        Path(info["config_path"])
                    ),
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

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-2"):
            icon_url = _get_icon_url()
            if icon_url:
                ui.image(icon_url).classes("w-8 h-8")
            ui.label(APP_DISPLAY_NAME).classes("text-lg font-semibold")
        with ui.row().classes("items-center gap-1"):
            ui.button(icon="note_add", on_click=_new_meeting).props(
                "flat round dense color=white"
            ).tooltip("اجتماع جديد")
            ui.button(icon="folder_open", on_click=_open_meeting).props(
                "flat round dense color=white"
            ).tooltip("فتح")
            ui.button(
                icon="content_copy", on_click=_duplicate_as_template
            ).props("flat round dense color=white").tooltip(
                "نسخ الاجتماع كقالب"
            )
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
            global _save_btn
            _save_btn = (
                ui.button(icon="save", on_click=_save_current_meeting)
                .props("flat round dense color=white")
                .tooltip("حفظ")
            )
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
        k = (e.key.name or "").lower()
        if k == "n":
            _new_meeting()
        elif k == "s":
            _save_current_meeting()
        elif k == "o":
            await _open_meeting()
        elif k == "r":
            if _recent_menu is not None:
                if _recent_menu_refresh is not None:
                    _recent_menu_refresh()
                _recent_menu.open()
        elif k == "d":
            _duplicate_as_template()
        elif k == "f":
            _open_meeting_folder()
        elif k == "e":
            await _compile_meeting()

    ui.keyboard(on_key=_handle_key)
    ui.timer(0.7, _refresh_periodic_state)


def _front_matter_panel(meeting: Meeting) -> None:
    with ui.column().classes("w-full gap-4 p-4"):
        ui.label("بيانات الاجتماع").classes("text-base font-semibold")
        with ui.grid(columns=2).classes("w-full gap-3"):
            ui.input("اسم المجلس / اللجنة").bind_value(meeting, "name").classes(
                "w-full"
            )
            with ui.column().classes("w-full gap-0"):
                num_input = ui.input(
                    "رقم الجلسة",
                    validation={
                        "يجب أن يكون عدداً صحيحاً":
                        lambda v: not v or v.strip().isdigit()
                    },
                ).bind_value(meeting, "number_num").classes("w-full")
                word_preview = ui.label().classes(
                    "text-xs text-gray-500 px-2 pt-1"
                )

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
                        lbl.text = (
                            f"الوقت: {meeting.time_digital} ({meeting.time})"
                        )
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
                        datetime_icon = ui.icon("event").classes(
                            "cursor-pointer"
                        )
                        _date_picker_holder: dict = {"picker": None}

                        def _set_date_title(iso: str) -> None:
                            picker = _date_picker_holder["picker"]
                            if picker is None:
                                return
                            picker._props["title"] = (
                                _arabic_full_date(iso) or " "
                            )
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
                            with ui.card().classes("p-2").style(
                                "max-width: none; width: max-content"
                            ):
                                with ui.row().classes(
                                    "items-start gap-2 flex-nowrap"
                                ):
                                    date_picker = ui.date(
                                        value=_date_from_display(meeting.date),
                                        on_change=_on_date_change,
                                    )
                                    _date_picker_holder["picker"] = date_picker
                                    _set_date_title(
                                        _date_from_display(meeting.date)
                                    )
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
            ui.input("السنة الأكاديمية").bind_value(
                meeting, "academic_year"
            ).classes("w-full")

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
            ui.button(icon="add", on_click=lambda: _add_article(meeting)).props(
                "dense unelevated"
            ).tooltip("إضافة موضوع")
        _articles_list(meeting)


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
            drop_target = ui.element("div").classes("flex-1 min-w-0 rounded")
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
                with (
                    ui.expansion(f"{index + 1}. {title_preview}", icon="description")
                    .props('header-class="text-weight-bold text-primary"')
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
                        ui.textarea("الوصف والمناقشة").bind_value(
                            article, "body"
                        ).props(
                            f'rows=4 autogrow input-class="{art_class}"'
                        ).classes("w-full")
                        ui.textarea("القرار / التوصية").bind_value(
                            article, "decision"
                        ).props("rows=2 autogrow").classes("w-full")
                        ui.textarea("مستند القرار").bind_value(
                            article, "legal_refs"
                        ).props("rows=2 autogrow").classes("w-full")
                        ui.input("الجهة ذات العلاقة").bind_value(
                            article, "target"
                        ).classes("w-full")

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

                        with ui.row().classes("w-full justify-end"):
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
            while spec.cells and len(spec.cells) % spec.columns != 0:
                spec.cells.append(TableCell(content="", bracketed=True))
            template = columns_to_css(spec.prelude_args, spec.columns)
            with ui.element("div").style(
                f"display: grid; grid-template-columns: {template}; "
                "gap: 4px; width: 100%;"
            ):
                for i, cell in enumerate(spec.cells):
                    ui.textarea(
                        value=cell.content,
                        on_change=lambda e, idx=i: setattr(
                            spec.cells[idx], "content", e.value
                        ),
                    ).props('autogrow dense outlined dir="auto"').classes(
                        "w-full tbl-cell"
                    )

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
        ui.textarea().bind_value(meeting, "invitees").props(
            "rows=3 autogrow"
        ).classes("w-full")
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
