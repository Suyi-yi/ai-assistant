"""核对本机记录的 token 用量，是否等于接口自己返回的用量。

会真实发 3 句话（成本极低）。用法：python tests/usage_check.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import store  # noqa: E402

import main as app  # noqa: E402

QUESTIONS = [
    "只回答两个字：收到",
    "用一句话说明今天适合做什么",
    "把 7 乘 8 的结果只写数字",
]


def main() -> None:
    api = app.Api()
    before = app.normalize_usage_total(api._config.get("usage_total"))
    print(f"开始前累计：{before['turns']} 轮，输入 {before['input']}，输出 {before['output']}\n")

    session = api.session_new("chat")
    session_id = session["id"]
    api._window = None  # 没有窗口时事件会被忽略，不影响落盘

    reported = {"input": 0, "output": 0, "cached": 0}
    for index, question in enumerate(QUESTIONS, start=1):
        before_count = len(store.load_session(session_id)["messages"])
        result = api.chat_send({"text": question, "session_id": session_id})
        if not result.get("ok"):
            print("发送失败：", result)
            return
        for _ in range(120):
            time.sleep(1)
            data = store.load_session(session_id)
            last = data["messages"][-1] if data["messages"] else {}
            if (
                len(data["messages"]) > before_count
                and last.get("role") == "assistant"
                and (last.get("content") or "").strip()
            ):
                break
        data = store.load_session(session_id)
        last = data["messages"][-1]
        # 消息里存的是归一化后的字段（input/cached/output），不要再走一遍原始解析
        usage = last.get("usage") or {}
        usage = {
            "input": int(usage.get("input", 0)),
            "cached": int(usage.get("cached", 0)),
            "output": int(usage.get("output", 0)),
        }
        print(f"第 {index} 问：输入 {usage['input']}（缓存 {usage['cached']}）输出 {usage['output']}")
        for key in reported:
            reported[key] += usage[key]

    after = app.normalize_usage_total(api._config.get("usage_total"))
    recorded = {
        "input": after["input"] - before["input"],
        "output": after["output"] - before["output"],
        "cached": after["cached"] - before["cached"],
    }
    print("\n对比：")
    print(f"  消息里存的（接口返回）：输入 {reported['input']}，缓存 {reported['cached']}，输出 {reported['output']}")
    print(f"  累计统计里的增量：      输入 {recorded['input']}，缓存 {recorded['cached']}，输出 {recorded['output']}")
    same = reported == recorded
    print("  两者一致:", same)
    print(f"  累计总额现在是：{after['turns']} 轮，输入 {after['input']}，输出 {after['output']}，"
          f"估算花费 ¥{after['cost_usd'] * 7.1:.4f}")
    if not same:
        print("  差异：", {k: recorded[k] - reported[k] for k in reported})

    store.delete_session(session_id)
    sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
