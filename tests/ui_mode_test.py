"""验证「同一个对话里切换聊天 / 天才程序员」：会话不重建、消息不丢。

会真发一句聊天（成本极低）。用法：python tests/ui_mode_test.py
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
  newSession().then(function(){
    window.__firstId = state.session.id;
    document.getElementById('input').value = '只回答两个字：你好';
    document.getElementById('sendBtn').click();
  });
  return 'sent';
})()"""

PROBE_JS = """JSON.stringify({
  id: (state.session || {}).id,
  mode: (state.session || {}).mode,
  messages: ((state.session || {}).messages || []).length,
  first: window.__firstId,
  chatActive: document.getElementById('modeChatBtn').classList.contains('active'),
  progToolsHidden: document.getElementById('progTools').classList.contains('hidden'),
  dividers: document.querySelectorAll('.mode-divider').length,
  streaming: !!state.streaming,
  error: window.__lastError
})"""

TO_PROG_JS = "(function(){ state.prog.notice_shown = true; setMode('prog'); return 'ok'; })()"
TO_CHAT_JS = "(function(){ setMode('chat'); return 'ok'; })()"
TO_PLAN_JS = "(function(){ state.prog.notice_shown = true; setMode('plan'); return 'ok'; })()"

# 往会话里塞两条程序员模式的消息，验证分隔线会画出来（只改内存，不落盘）
DIVIDER_JS = """(function(){
  state.session.messages.push({role:'user', content:'看看这个目录', mode:'prog', ts:0});
  state.session.messages.push({role:'assistant', content:'看完了', mode:'prog', ts:0, events:[]});
  renderMessages();
  return document.querySelectorAll('.mode-divider').length;
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
    print("发起聊天：", window.evaluate_js(START_JS), flush=True)

    for _ in range(20):
        time.sleep(2)
        before = json.loads(window.evaluate_js(PROBE_JS))
        if before["messages"] and not before["streaming"]:
            break
    print("聊天后：", json.dumps(before, ensure_ascii=False), flush=True)

    window.evaluate_js(TO_PROG_JS)
    time.sleep(2.5)
    after_prog = json.loads(window.evaluate_js(PROBE_JS))
    print("切到天才程序员：", json.dumps(after_prog, ensure_ascii=False), flush=True)

    window.evaluate_js(TO_CHAT_JS)
    time.sleep(2.5)
    after_chat = json.loads(window.evaluate_js(PROBE_JS))
    print("切回聊天：", json.dumps(after_chat, ensure_ascii=False), flush=True)

    window.evaluate_js(TO_PLAN_JS)
    time.sleep(2.5)
    after_plan = json.loads(window.evaluate_js(PROBE_JS))
    plan_badge = window.evaluate_js("!document.getElementById('planBadge').classList.contains('hidden')")
    sandbox_hidden = window.evaluate_js("document.getElementById('progSandbox').classList.contains('hidden')")
    print("切到计划模式：", json.dumps(after_plan, ensure_ascii=False), flush=True)
    print("  计划模式标识可见:", plan_badge, "| 权限下拉隐藏:", sandbox_hidden)

    print("\n判定：")
    print("  切换不换会话:", before["id"] == after_prog["id"] == after_chat["id"])
    print("  模式确实切了:", after_prog["mode"] == "prog" and after_chat["mode"] == "chat")
    print("  消息没丢:", after_chat["messages"] == before["messages"], before["messages"], after_chat["messages"])
    print("  工具栏跟着模式隐藏:", after_prog["progToolsHidden"] is False and after_chat["progToolsHidden"] is True)
    print("  计划模式可用:", after_plan["mode"] == "plan" and plan_badge is True and sandbox_hidden is True)
    dividers = window.evaluate_js(DIVIDER_JS)
    print("  混模式时画出分隔线:", dividers >= 1, f"分隔线数={dividers}")
    print("  没有异常:", after_chat["error"] is None)
    time.sleep(1)
    try:
        window.destroy()
    except Exception:
        pass


def main() -> None:
    api = app.Api()
    window = webview.create_window(
        "modetest",
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
