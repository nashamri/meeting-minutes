import os
import sys
import webbrowser

import webview

from main import get_app_info, load_theme, save_theme


_RESOURCE_ROOT = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(_RESOURCE_ROOT, "web", "index.html")


class Api:
    """Methods exposed to the web view's JavaScript via `window.pywebview.api`."""

    def get_initial_state(self):
        return {
            "theme": load_theme(),
        }

    def set_theme(self, theme):
        save_theme(theme)
        return True

    def get_app_info(self):
        return get_app_info()

    def open_url(self, url):
        if isinstance(url, str) and (url.startswith("http://") or url.startswith("https://")):
            webbrowser.open(url)
            return True
        return False


def run_webapp():
    api = Api()
    webview.create_window(
        "Meetings Minutes",
        HTML_PATH,
        js_api=api,
        width=900,
        height=700,
        min_size=(600, 500),
    )
    webview.start()
