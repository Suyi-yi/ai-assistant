"""端到端演练：用程序驱动界面发一条消息，验证流式输出与情绪联动真的生效。

会真实调用一次 DeepSeek（消耗极少额度）。
用法：python tests/ui_chat_test.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webview  # noqa: E402

import main as app  # noqa: E402

QUESTION = "用一句话告诉我，我的记忆库里现在有哪些项目在推进"

SEND_JS = (
    "(function(){"
    "newSession('chat').then(function(){"
    f"document.getElementById('input').value = {QUESTION!r};"
    "document.getElementById('sendBtn').click();"
    "}); return 'sent'; })()"
)

PROBE_JS = """(function(){
  const bubbles = document.querySelectorAll('.msg.assistant .bubble');
  const last = bubbles[bubbles.length - 1];
  return JSON.stringify({
    bubbles: bubbles.length,
    streaming: !!document.querySelector('.cursor'),
    text: last ? last.innerText.slice(0, 400) : '',
    md: last ? last.classList.contains('md') : false,
    mood: document.getElementById('moodLabel').textContent,
    emoji: document.getElementById('moodEmoji').textContent,
    refs: document.querySelectorAll('#refsList .ref').length,
    barLast: (document.getElementById('sbLast') || {}).textContent,
    barTotal: (document.getElementById('sbTotal') || {}).textContent,
    error: window.__lastError
  });
})()"""


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
    try:
        print("发送：", window.evaluate_js(SEND_JS), flush=True)
    except Exception as exc:
        print("发送失败：", exc, flush=True)
        return

    final = None
    for _ in range(40):
        time.sleep(2)
        try:
            raw = window.evaluate_js(PROBE_JS)
        except Exception as exc:
            print("探针异常：", exc, flush=True)
            break
        print("轮询：", raw, flush=True)
        try:
            import json

            data = json.loads(raw)
        except Exception:
            continue
        if data.get("bubbles") and not data.get("streaming") and data.get("text"):
            final = data
            break
    print("最终结果：", final, flush=True)
    time.sleep(1)
    try:
        window.destroy()
    except Exception:
        pass


def main() -> None:
    api = app.Api()
    window = webview.create_window(
        "chattest",
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
