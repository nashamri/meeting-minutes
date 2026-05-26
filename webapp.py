import os
import sys
import webbrowser
from pathlib import Path

from nicegui import app, ui

from main import APP_DISPLAY_NAME, get_app_info, load_theme, save_theme
from models import ATTENDANCE_OPTIONS, Article, Meeting, Member

_meeting = Meeting()

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

    with ui.tabs().classes("w-full") as tabs:
        front_tab = ui.tab("front", label="معلومات الاجتماع", icon="event")
        articles_tab = ui.tab("articles", label="الموضوعات", icon="article")
        end_tab = ui.tab("end", label="الاعتماد", icon="verified")

    with ui.tab_panels(tabs, value=front_tab).classes("w-full"):
        with ui.tab_panel(front_tab):
            _front_matter_panel(_meeting)
        with ui.tab_panel(articles_tab):
            _articles_panel(_meeting)
        with ui.tab_panel(end_tab):
            _end_matter_panel(_meeting)


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
                with ui.row().classes("w-full justify-end"):
                    ui.button(
                        "حذف الموضوع",
                        icon="delete",
                        on_click=lambda a=article: _remove_article(meeting, a),
                    ).props("flat color=negative")


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
        ui.textarea("المدعوين والإضافات والملحوظات").bind_value(
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
