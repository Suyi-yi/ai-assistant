"""验证程序员消息的排版：命令收进折叠组，回答排在最下面。

不调用 Codex，往会话里注入一条带事件的历史消息再渲染。
用法：python tests/ui_render_test.py
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

INJECT_JS = """(function(){
  state.session.messages = [
    {role:'user', content:'帮我看看这个目录', mode:'prog', ts:0},
    {role:'assistant', content:'目录里有三个文件，我建了一个新文件。', mode:'prog', ts:0,
     usage:{input:1200, cached:800, output:60},
     events:[
       {kind:'status', text:'正在启动 Codex…'},
       {kind:'command', id:'i0', command:'Get-ChildItem -Force', output:'a.txt\\nb.txt\\n', exit_code:0, status:'completed'},
       {kind:'command', id:'i1', command:'New-Item -Path c.txt', output:'', exit_code:0, status:'completed'},
       {kind:'message', text:'目录里有三个文件，我建了一个新文件。'}
     ]}
  ];
  renderMessages();
  return 'ok';
})()"""

PROBE_JS = """(function(){
  const bubble = document.querySelector('.bubble.prog-bubble');
  if (!bubble) return JSON.stringify({found:false});
  const kids = Array.from(bubble.children).map(function(node){
    return (node.className || '').split(' ')[0] || node.tagName.toLowerCase();
  });
  const group = bubble.querySelector('.ev-group');
  const answer = bubble.querySelector('.prog-answer');
  return JSON.stringify({
    found: true,
    order: kids,
    groupCollapsed: group ? !group.open : false,
    groupSummary: group ? group.querySelector('.ev-group-text').textContent : '',
    commandBlocks: bubble.querySelectorAll('.ev-group .ev').length,
    answerText: answer ? answer.innerText.slice(0, 40) : '',
    answerAfterGroup: group && answer
      ? !!(group.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING)
      : false,
    usageShown: !!bubble.querySelector('.usage-chip'),
    error: window.__lastError
  });
})()"""

# 模拟"跑到一半切了模式、界面被重画"：原气泡已经脱离文档，此时结果不能丢
LOST_BUBBLE_JS = """(function(){
  state.session.messages = [
    {role:'user', content:'建一个文件', mode:'prog', ts:0}
  ];
  state.progRunning = true;
  state.progBubble = document.createElement('div');   // 故意造一个没插进页面的气泡
  renderMessages();
  const payload = {
    session_id: state.session.id,
    usage: {input: 900, cached: 700, output: 40},
    session: {
      id: state.session.id,
      mode: 'prog',
      messages: [
        {role:'user', content:'建一个文件', mode:'prog', ts:0},
        {role:'assistant', content:'文件建好了。', mode:'prog', ts:0,
         usage:{input:900, cached:700, output:40},
         events:[{kind:'command', id:'i0', command:'New-Item a.txt', output:'', exit_code:0, status:'completed'}]}
      ]
    },
    list: []
  };
  state.session = payload.session;
  window.__aiAssistant.onProgDone(payload);
  return 'ok';
})()"""

AFTER_LOSS_JS = """JSON.stringify({
  running: state.progRunning,
  bubbles: document.querySelectorAll('.msg.assistant .bubble').length,
  answers: Array.from(document.querySelectorAll('.prog-answer')).map(function(n){return n.innerText.slice(0,20);}),
  groups: document.querySelectorAll('.ev-group').length,
  error: window.__lastError
})"""


def drive(window) -> None:
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            if window.evaluate_js("!!window.pywebview && !!window.pywebview.api"):
                break
        except Exception:
            continue
    time.sleep(3)
    print("注入并渲染：", window.evaluate_js(INJECT_JS), flush=True)
    time.sleep(1.5)
    data = json.loads(window.evaluate_js(PROBE_JS))
    print("探针：", json.dumps(data, ensure_ascii=False), flush=True)
    print("\n判定：")
    print("  命令收进折叠组:", data.get("commandBlocks") == 2, f"命令块={data.get('commandBlocks')}")
    print("  默认是折叠的:", data.get("groupCollapsed") is True)
    print("  折叠标题有步数:", "2 步" in (data.get("groupSummary") or ""), data.get("groupSummary"))
    print("  回答在命令下面:", data.get("answerAfterGroup") is True, "DOM 顺序=" + str(data.get("order")))
    print("  回答内容正确:", "三个文件" in (data.get("answerText") or ""))
    print("  显示了 token 用量:", data.get("usageShown") is True)
    print("  没有异常:", data.get("error") is None)

    print()
    print("模拟中途切模式（气泡已被重画掉）：")
    print("  触发：", window.evaluate_js(LOST_BUBBLE_JS), flush=True)
    time.sleep(2)
    after = json.loads(window.evaluate_js(AFTER_LOSS_JS))
    print("  结果：", json.dumps(after, ensure_ascii=False), flush=True)
    print("  结果没丢、仍然渲染出来:", after["bubbles"] >= 1 and any("文件建好了" in a for a in after["answers"]))
    print("  跑完状态复位:", after["running"] is False)
    time.sleep(1)
    try:
        window.destroy()
    except Exception:
        pass


def main() -> None:
    api = app.Api()
    window = webview.create_window(
        "rendertest",
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
