"""算清楚每次对话到底花多少 token。

用法：
    python tests/token_report.py            # 只按字符估算，不联网、不花钱
    python tests/token_report.py --real     # 真调一次接口，读官方返回的 token 用量
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from core import store
from core.llm import build_system_prompt
from core.memory import MemoryBank

# 中文大致 1 字 ≈ 0.6~1 token，取 0.7 做保守估算
CHARS_PER_TOKEN = 0.7


def estimate(text: str) -> int:
    return int(len(text) * CHARS_PER_TOKEN)


def build_messages(config: dict, bank: MemoryBank, question: str, history: list[dict]):
    refs = bank.retrieve(question, k=int(config.get("max_context_notes", 4) or 4))
    system = build_system_prompt(config, bank, refs)
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": question}]
    detail = {
        "system": len(system),
        "refs": sum(len(r["content"]) for r in refs),
        "ref_files": [f"{r['path']}({len(r['content'])}字)" for r in refs],
        "history": sum(len(m["content"]) for m in history),
        "question": len(question),
    }
    return messages, detail


def real_usage(config: dict, messages: list[dict]) -> dict:
    """非流式调一次，只为拿官方 usage 数字。"""
    base = (config.get("base_url") or "https://api.deepseek.com").rstrip("/")
    response = requests.post(
        f"{base}/chat/completions",
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        data=json.dumps(
            {
                "model": config.get("model") or "deepseek-chat",
                "messages": messages,
                "stream": False,
                "temperature": 0.7,
                "max_tokens": 120,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        timeout=90,
    )
    if response.status_code != 200:
        return {"error": f"{response.status_code} {response.text[:200]}"}
    return response.json().get("usage", {})


def main() -> None:
    config = store.load_config()
    bank = MemoryBank(config["memory_root"])
    total_chars = sum(len(n["text"]) for n in bank.all_notes())
    print(f"记忆库：{bank.stats()['count']} 篇笔记，正文合计 {total_chars:,} 字\n")

    cases = [
        ("打个招呼（不触发检索）", "在吗"),
        ("追问一句（不该带笔记）", "继续说说这个"),
        ("问一句项目进展（触发检索）", "我的智能门锁毕设现在到哪一步了？"),
    ]
    use_real = "--real" in sys.argv
    if use_real and not config.get("api_key"):
        print("没有配置 API Key，跳过实测")
        use_real = False

    for label, question in cases:
        messages, detail = build_messages(config, bank, question, [])
        total = sum(len(m["content"]) for m in messages)
        print(f"【{label}】{question}")
        print(f"  系统提示词 {detail['system']:,} 字（含记忆库总览节选与规则）")
        print(f"  自动附带的笔记 {detail['refs']:,} 字  {detail['ref_files']}")
        print(f"  提问 {detail['question']} 字")
        est = estimate("x" * total)
        print(f"  合计约 {total:,} 字 ≈ 输入 {est:,} token（按 0.7 token/字 估算）")
        if use_real:
            usage = real_usage(config, messages)
            print(f"  官方实测：{usage}")
        print()

    print("【多轮对话会累积多少】")
    history: list[dict] = []
    per_turn = []
    for turn in range(1, 11):
        messages, _ = build_messages(config, bank, "继续说说这个", history)
        size = sum(len(m["content"]) for m in messages)
        per_turn.append(size)
        history.append({"role": "user", "content": "继续说说这个"})
        history.append({"role": "assistant", "content": "x" * 320})  # 假设每轮回答约 320 字
    for turn, size in enumerate(per_turn, start=1):
        if turn in (1, 3, 5, 10):
            print(f"  第 {turn:>2} 轮：输入约 {size:,} 字")
    avg = sum(per_turn) / len(per_turn)
    print(f"  前 10 轮平均每轮输入 {avg:,.0f} 字")
    print("  说明：会话只保留最近 20 条消息，超过后更早的内容不再进上下文。")


if __name__ == "__main__":
    main()
