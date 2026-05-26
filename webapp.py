import os
import sys
import webbrowser

from nicegui import ui

from main import APP_DISPLAY_NAME, get_app_info, load_theme, save_theme


def _open_about(info: dict) -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px] max-w-[500px]"):
        ui.label("About").classes("text-lg font-semibold")
        rows = [
            ("Name", info["name"]),
            ("Version", info["version"]),
            ("Tagline", info["tagline"]),
            ("Python", info["python_version"]),
            ("Platform", info["platform"]),
            ("Config", info["config_path"]),
        ]
        for key, value in rows:
            with ui.row().classes("w-full justify-between items-center gap-4"):
                ui.label(key).classes("text-sm text-gray-500")
                ui.label(value).classes("text-sm")
        with ui.row().classes("w-full justify-between items-center gap-4"):
            ui.label("Source").classes("text-sm text-gray-500")
            link = ui.label(info["repo_url"]).classes(
                "text-sm text-primary cursor-pointer underline"
            )
            link.on("click", lambda: webbrowser.open(info["repo_url"]))
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("Close", on_click=dialog.close).props("unelevated")
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
                .tooltip("Toggle theme")
            )
            ui.button(icon="info", on_click=lambda: _open_about(info)).props(
                "flat round dense color=white"
            ).tooltip("About")

    with ui.column().classes("p-5"):
        ui.label("Welcome to Meetings Minutes.")


def run_webapp() -> None:
    if sys.platform.startswith("linux"):
        os.environ.setdefault("PYWEBVIEW_GUI", "qt")
    ui.run(
        native=True,
        title=APP_DISPLAY_NAME,
        window_size=(900, 700),
        reload=False,
        show=False,
    )
