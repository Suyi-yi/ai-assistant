"""不开截图、不靠肉眼，用 JS 探针验证界面真的活着。

用法：python tests/ui_selftest.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

import main as app  # noqa: E402

PROBE = """JSON.stringify({
  ready: document.readyState,
  bridge: typeof window.__aiAssistant,
  sessions: document.querySelectorAll('#sessionList .item').length,
  treeRows: document.querySelectorAll('#memoTree .tree-row').length,
  mood: (document.getElementById('moodLabel')||{}).textContent,
  overlay: !document.getElementById('setupOverlay').classList.contains('hidden'),
  title: document.getElementById('assistantName').textContent,
  modelChip: (document.getElementById('modelChip')||{}).textContent,
  modeSwitch: !!document.getElementById('modeChatBtn') && !!document.getElementById('modeProgBtn'),
  modeActive: (document.querySelector('.mode-btn.active')||{}).textContent,
  progBtn: !document.getElementById('modeProgBtn').disabled,
  projectsTab: !!document.getElementById('paneProjects'),
  sandboxOptions: document.querySelectorAll('#progSandbox option').length,
  skillsTab: !!document.getElementById('paneSkills'),
  skillHandler: typeof window.loadSkills,
  toast: (document.getElementById('toast')||{}).textContent,
  treeError: (document.querySelector('#memoTree .empty')||{}).textContent,
  usageBar: ["sbLast","sbSession","sbTotal","sbCost"].map(function(id){
    const node = document.getElementById(id);
    return node ? node.textContent : null;
  }),
  error: window.__lastError
})"""

report: list[str] = []


def probe(window) -> None:
    deadline = time.time() + 25
    seen = False
    while time.time() < deadline:
        time.sleep(8)
        try:
            raw = window.evaluate_js(PROBE)
        except Exception as exc:
            print(f"evaluate_js 失败：{exc}", flush=True)
            continue
        print(f"探针返回：{raw}", flush=True)
        seen = True
        break
    if not seen:
        print("30 秒内 evaluate_js 一直没有返回 —— 界面线程被卡住", flush=True)
    time.sleep(1)
    try:
        window.destroy()
    except Exception:
        pass


def main_test() -> None:
    instance = app.Api()
    index = app.ui_dir() / "index.html"
    window = webview.create_window(
        "selftest",
        html=app.build_html(),
        js_api=instance,
        width=1320,
        height=860,
        min_size=(1020, 660),
        background_color="#0F0A1E",
        text_select=True,
    )
    instance._window = window

    def on_loaded(win):
        threading.Thread(target=probe, args=(win,), daemon=True).start()

    webview.start(on_loaded, window, debug=False, private_mode=False)
    print("WINDOW_CLOSED", flush=True)


if __name__ == "__main__":
    main_test()
