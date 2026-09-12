"""定位界面卡死的来源：分别试「我的页面+无js_api」「我的页面+js_api」「极简页面+js_api」。

用法：python tests/ui_bisect.py <mine-noapi|mine-api|simple-api>
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

import main as app  # noqa: E402

SIMPLE = (
    "<html><head><title>simple</title></head>"
    "<body style='background:#123'><h1>hi</h1></body></html>"
)
PROBE = "document.readyState + '|' + (document.querySelectorAll('#sessionList .item').length)"


def run_probe(window) -> None:
    time.sleep(3)
    try:
        result = window.evaluate_js(PROBE)
        print(f"RESULT_OK {result}", flush=True)
    except Exception as exc:
        print(f"RESULT_ERR {exc}", flush=True)
    try:
        window.destroy()
    except Exception:
        pass


def main(mode: str) -> None:
    api = app.Api()
    start_kwargs = {"debug": False}
    if mode == "simple-api":
        window = webview.create_window("bisect", html=SIMPLE, js_api=api, width=700, height=500)
    elif mode == "mine-api":
        window = webview.create_window(
            "bisect", url=str(app.ui_dir() / "index.html"), js_api=api, width=900, height=700
        )
    elif mode == "mine-api-private":
        window = webview.create_window(
            "bisect", url=str(app.ui_dir() / "index.html"), js_api=api, width=900, height=700
        )
        start_kwargs["private_mode"] = False
    elif mode == "mine-api-full":
        window = webview.create_window(
            "bisect",
            url=str(app.ui_dir() / "index.html"),
            js_api=api,
            width=1320,
            height=860,
            min_size=(1020, 660),
            background_color="#0F0A1E",
            text_select=True,
        )
        start_kwargs["private_mode"] = False
    else:
        window = webview.create_window(
            "bisect", url=str(app.ui_dir() / "index.html"), width=900, height=700
        )

    def on_loaded(win):
        threading.Thread(target=run_probe, args=(win,), daemon=True).start()

    webview.start(on_loaded, window, **start_kwargs)
    print("WINDOW_CLOSED", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "mine-noapi")
