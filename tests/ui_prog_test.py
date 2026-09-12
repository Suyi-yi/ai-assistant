"""界面级验证：程序员模式发出去之后，是否立刻有反馈、是否能停。

会真实启动一次 codex（有 token 成本），跑到一半主动停掉。
用法：python tests/ui_prog_test.py
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

START_JS = """(function(){
  state.prog.notice_shown = true;          // 跳过首次成本提示，测的是反馈链路
  window.__progLog = [];
  newSession('prog').then(function(){
    progSend('列出当前目录的文件名');
  });
  return 'started';
})()"""

PROBE_JS = """(function(){
  const live = document.querySelector('.prog-live');
  const status = document.querySelector('.prog-status');
  return JSON.stringify({
    mode: (state.session && state.session.mode) || '',
    workspace: (state.session && state.session.workspace) || '',
    barVisible: !document.getElementById('progBar').classList.contains('hidden'),
    workspaceLabel: document.getElementById('progWorkspaceBtn').textContent,
    liveCard: !!live,
    liveText: live ? live.querySelector('.prog-live-text').textContent : '',
    liveSeconds: live ? live.querySelector('.prog-live-time').textContent : '',
    statusLine: status ? status.textContent : '',
    spin: !!document.querySelector('.spinner'),
    sendDisabled: document.getElementById('sendBtn').disabled,
    running: !!state.progRunning,
    error: window.__lastError
  });
})()"""

STOP_JS = "(function(){ progStop(); return 'stopped'; })()"


def drive(window) -> None:
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            if window.evaluate_js("!!window.pywebview && !!window.pywebview.api"):
                break
        except Exception:
            continue
    time.sleep(2)
    print("启动：", window.evaluate_js(START_JS), flush=True)

    seen_live = False
    seen_seconds = set()
    for index in range(6):
        time.sleep(3)
        raw = window.evaluate_js(PROBE_JS)
        data = json.loads(raw)
        print(f"  t+{(index + 1) * 3}s {raw}", flush=True)
        if data.get("liveCard") and data.get("liveText"):
            seen_live = True
        if data.get("liveSeconds"):
            seen_seconds.add(data["liveSeconds"])
        if data.get("barVisible") and data.get("liveCard") and data.get("liveText"):
            break

    stopped = window.evaluate_js(STOP_JS)
    print("停止：", stopped, flush=True)
    time.sleep(4)
    final = json.loads(window.evaluate_js(PROBE_JS))
    print("停止后：", json.dumps(final, ensure_ascii=False), flush=True)

    print("\n判定：")
    print("  立刻有反馈卡片:", seen_live)
    print("  计时器在走:", any(value != "0s" for value in seen_seconds), sorted(seen_seconds))
    print("  停止后不再运行:", final.get("running") is False)
    time.sleep(1)
    try:
        window.destroy()
    except Exception:
        pass


def main() -> None:
    api = app.Api()
    window = webview.create_window(
        "progtest",
        html=app.build_html(),
        js_api=api,
        width=1320,
        height=860,
        min_size=(1020, 660),
        background_color="#0F0A1E",
        text_select=True,
    )
    api._window = window

    def on_loaded(win):
        threading.Thread(target=drive, args=(win,), daemon=True).start()

    webview.start(on_loaded, window, debug=False, private_mode=False)
    print("WINDOW_CLOSED", flush=True)


if __name__ == "__main__":
    main()
