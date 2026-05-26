import asyncio
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from nicegui import app, ui

from main import (
    APP_DISPLAY_NAME,
    get_app_info,
    load_meetings_root,
    load_theme,
    save_theme,
)
from models import ATTENDANCE_OPTIONS, Article, Meeting, Member
from typst_io import read_meeting, write_meeting
from typst_tables import (
    TableCell,
    TableSpec,
    add_column,
    add_row,
    append_table_to_body,
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
    kwargs.setdefault("position", "top-left")
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
        'body, .q-field, .q-btn, .q-dialog, .q-tooltip, input, textarea, button '
        '{ font-family: "IBM Plex Sans Arabic", system-ui, sans-serif; }'
    )
    ui.add_head_html(f"<style>{faces} {body_rule}</style>", shared=True)


_register_fonts()


_INVALID_PATH_CHARS = '/\\\n\r\t\0'


def _sanitize_path_component(value: str) -> str:
    return "".join("_" if c in _INVALID_PATH_CHARS else c for c in value.strip())


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
    try:
        loaded = read_meeting(src)
    except OSError as exc:
        _notify(f"تعذّر فتح الاجتماع: {exc}", type="negative")
        return
    for attr in (
        "name",
        "number",
        "number_num",
        "date",
        "time",
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
    _notify(f"فُتح: {src.name}", type="positive", timeout=4000)


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
    root = load_meetings_root()
    return (
        root
        / _sanitize_path_component(_meeting.name)
        / _sanitize_path_component(_meeting.academic_year)
        / _sanitize_path_component(_meeting.number_num)
    )


def _save_current_meeting() -> Path | None:
    dest = _resolve_meeting_dest()
    if dest is None:
        return None
    try:
        write_meeting(_meeting, dest)
    except OSError as exc:
        _notify(f"تعذّر الحفظ: {exc}", type="negative")
        return None
    _notify(f"حُفظ في: {dest}", type="positive", timeout=5000)
    return dest


async def _compile_meeting() -> None:
    dest = _save_current_meeting()
    if dest is None:
        return
    if not shutil.which("typst"):
        _notify(
            "لم يتم العثور على برنامج typst. الرجاء تثبيته.", type="negative"
        )
        return
    main_typ = dest / "main.typ"
    pdf_path = dest / "main.pdf"
    preview_dir = dest / "_preview"
    if preview_dir.exists():
        for p in preview_dir.glob("page-*.svg"):
            p.unlink()
    preview_dir.mkdir(exist_ok=True)
    svg_template = preview_dir / "page-{0p}.svg"

    try:
        pdf_result = await asyncio.to_thread(
            subprocess.run,
            ["typst", "compile", str(main_typ), str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if pdf_result.returncode == 0:
            svg_result = await asyncio.to_thread(
                subprocess.run,
                ["typst", "compile", str(main_typ), str(svg_template), "--format", "svg"],
                capture_output=True,
                text=True,
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


def _open_about(info: dict) -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px] max-w-[500px]"):
        ui.label("حول").classes("text-lg font-semibold")
        rows = [
            ("الاسم", info["name"]),
            ("الإصدار", info["version"]),
            ("الوصف", info["tagline"]),
            ("Python", info["python_version"]),
            ("النظام", info["platform"]),
            ("ملف الإعدادات", info["config_path"]),
        ]
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
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("إغلاق", on_click=dialog.close).props("unelevated")
    dialog.open()


@ui.page("/")
def _index() -> None:
    info = get_app_info()
    dark = ui.dark_mode(value=(load_theme() == "dark"))

    def toggle_theme() -> None:
        new_value = not dark.value
        dark.set_value(new_value)
        save_theme("dark" if new_value else "light")
        theme_btn.props(f"icon={'light_mode' if new_value else 'dark_mode'}")

    with ui.header().classes("items-center justify-between"):
        ui.label(APP_DISPLAY_NAME).classes("text-lg font-semibold")
        with ui.row().classes("items-center gap-1"):
            ui.button(icon="folder_open", on_click=_open_meeting).props(
                "flat round dense color=white"
            ).tooltip("فتح")
            ui.button(icon="save", on_click=_save_current_meeting).props(
                "flat round dense color=white"
            ).tooltip("حفظ")
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
            ui.button(icon="info", on_click=lambda: _open_about(info)).props(
                "flat round dense color=white"
            ).tooltip("حول")

    global _tabs_ref
    with ui.tabs().classes("w-full") as tabs:
        front_tab = ui.tab("front", label="معلومات الاجتماع", icon="event")
        articles_tab = ui.tab("articles", label="الموضوعات", icon="article")
        end_tab = ui.tab("end", label="الاعتماد", icon="verified")
        pdf_tab = ui.tab("pdf", label="معاينة", icon="picture_as_pdf")
    _tabs_ref = tabs

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


def _front_matter_panel(meeting: Meeting) -> None:
    with ui.column().classes("w-full gap-4 p-4"):
        ui.label("بيانات الاجتماع").classes("text-base font-semibold")
        with ui.grid(columns=2).classes("w-full gap-3"):
            ui.input("اسم المجلس / اللجنة").bind_value(meeting, "name").classes("w-full")
            ui.input("الجلسة (نصاً)", placeholder="السادسة عشرة").bind_value(
                meeting, "number"
            ).classes("w-full")
            ui.input("الجلسة (رقماً)", placeholder="16").bind_value(
                meeting, "number_num"
            ).classes("w-full")
            ui.input("التاريخ", placeholder="20 / 5 / 2026 م").bind_value(
                meeting, "date"
            ).classes("w-full")
            ui.input("الوقت", placeholder="الحادية عشرة صباحاً").bind_value(
                meeting, "time"
            ).classes("w-full")
            ui.input("السنة الأكاديمية", placeholder="1447").bind_value(
                meeting, "academic_year"
            ).classes("w-full")

        ui.separator()

        with ui.row().classes("w-full items-center justify-between"):
            ui.label("الأعضاء").classes("text-base font-semibold")
            ui.button(icon="add", on_click=lambda: _add_member(meeting)).props(
                "dense unelevated"
            ).tooltip("إضافة عضو")
        _members_list(meeting)


@ui.refreshable
def _members_list(meeting: Meeting) -> None:
    if not meeting.members:
        ui.label("لا توجد أعضاء بعد. اضغط + لإضافة عضو.").classes(
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
            ui.label("الموضوعات").classes("text-base font-semibold")
            ui.button(icon="add", on_click=lambda: _add_article(meeting)).props(
                "dense unelevated"
            ).tooltip("إضافة موضوع")
        _articles_list(meeting)


@ui.refreshable
def _articles_list(meeting: Meeting) -> None:
    if not meeting.articles:
        ui.label("لا توجد موضوعات بعد. اضغط + لإضافة موضوع.").classes(
            "text-sm text-gray-500"
        )
        return
    for index, article in enumerate(meeting.articles):
        title_preview = (article.title or f"موضوع جديد {index + 1}").strip()
        with ui.expansion(f"{index + 1}. {title_preview}", icon="description").classes(
            "w-full"
        ):
            with ui.column().classes("w-full gap-3 p-2"):
                ui.input("العنوان").bind_value(article, "title").classes("w-full")
                ui.textarea("الوصف والمناقشة").bind_value(article, "body").props(
                    "rows=4 autogrow"
                ).classes("w-full")
                ui.textarea("القرار / التوصية").bind_value(article, "decision").props(
                    "rows=2 autogrow"
                ).classes("w-full")
                ui.textarea("مستند القرار").bind_value(article, "legal_refs").props(
                    "rows=2 autogrow"
                ).classes("w-full")
                ui.input("الجهة ذات العلاقة").bind_value(article, "target").classes(
                    "w-full"
                )

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
                                    on_click=lambda a=article, idx=ti: _open_table_editor(
                                        a, idx, _articles_list.refresh
                                    ),
                                ).props("flat dense")
                    else:
                        ui.label("لا يوجد جداول.").classes("text-sm text-gray-500")
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
                        on_click=lambda a=article: _remove_article(meeting, a),
                    ).props("flat color=negative")


def _open_table_editor(
    article: Article, table_index: int | None, on_save
) -> None:
    if table_index is None:
        spec = new_table(rows=2, columns=3)
    else:
        tables = find_tables(article.body)
        if table_index >= len(tables):
            _notify("الجدول غير موجود.", type="negative")
            return
        spec = tables[table_index]

    with ui.dialog().props("maximized") as dialog, ui.card().classes(
        "w-full h-full"
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(
                "محرر الجدول" if table_index is None else f"تعديل الجدول {table_index + 1}"
            ).classes("text-lg font-semibold")
            ui.label(
                "تلميح: المحتوى داخل الخلية يُلفّ بـ [ ] تلقائياً. التعابير الخام (مثل strong(\"..\")) تبقى كما هي."
            ).classes("text-xs text-gray-500")

        @ui.refreshable
        def grid_view() -> None:
            while spec.cells and len(spec.cells) % spec.columns != 0:
                spec.cells.append(TableCell(content="", bracketed=True))
            with ui.element("div").style(
                f"display: grid; grid-template-columns: repeat({spec.columns}, minmax(0, 1fr)); "
                "gap: 4px; width: 100%;"
            ):
                for i, cell in enumerate(spec.cells):
                    ui.textarea(
                        value=cell.content,
                        on_change=lambda e, idx=i: setattr(
                            spec.cells[idx], "content", e.value
                        ),
                    ).props("autogrow dense outlined").classes("w-full")

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

        ui.separator()

        ui.label("جدول التوقيعات").classes("text-base font-semibold")
        ui.label(
            "يُبنى تلقائياً من قائمة الأعضاء في تبويب «معلومات الاجتماع»."
        ).classes("text-sm text-gray-500")
        _signatures_preview(meeting)

        ui.separator()

        ui.label("ملاحظات الختام").classes("text-base font-semibold")
        ui.input("المدعوين").bind_value(meeting, "invitees").classes("w-full")
        ui.textarea("الإضافات والملحوظات").bind_value(
            meeting, "closing_notes"
        ).props("rows=4 autogrow").classes("w-full")


@ui.refreshable
def _signatures_preview(meeting: Meeting) -> None:
    if not meeting.members:
        ui.label("لا يوجد أعضاء.").classes("text-sm text-gray-500")
        return
    columns = [
        {"name": "n", "label": "م", "field": "n", "align": "center"},
        {"name": "name", "label": "الاسم", "field": "name", "align": "right"},
        {"name": "signature", "label": "التوقيع", "field": "signature", "align": "center"},
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
