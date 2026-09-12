"""离线自检：不调 DeepSeek，只验证记忆库读写、检索、渲染、情绪解析。

用法：python tests/smoke_test.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import store  # noqa: E402

# 备份和回收站也指向临时目录，别把测试垃圾倒进项目自己的 data/
_sandbox = Path(tempfile.mkdtemp(prefix="ai-assistant-smoke-"))
store.DATA_DIR = _sandbox
store.SESSIONS_DIR = _sandbox / "sessions"
store.BACKUPS_DIR = _sandbox / "backups"
store.TRASH_DIR = _sandbox / "backups" / "trash"

from core.llm import EMOTIONS, build_system_prompt, split_emotion  # noqa: E402
from core.memory import (  # noqa: E402
    MemoryBank,
    check_memory_root,
    extract_memo_writes,
    render_markdown,
    scaffold_memory_bank,
    strip_memo_writes,
)

passed = 0
failed = 0


def check(label: str, condition, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [ok] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label} {detail}")


def test_real_bank() -> None:
    print("1. 本机记忆库（只读）")
    root = store.DEFAULT_CONFIG["memory_root"]
    bank = MemoryBank(root)
    if not bank.exists:
        print(f"  [skip] 记忆库不存在：{root}")
        return
    tree = bank.tree()
    check("目录树可读取", tree.get("ok"))
    stats = bank.stats()
    check("能统计到笔记", stats["count"] > 0, f"count={stats['count']}")
    hits = bank.search("门锁", 5)
    check("搜『门锁』有结果", len(hits) > 0, f"hits={len(hits)}")
    if hits:
        print(f"       首位命中：{hits[0]['path']}")
    review = bank.search("复盘", 5)
    check("搜『复盘』有结果", len(review) > 0, f"hits={len(review)}")
    refs = bank.retrieve("智能门锁毕设现在做到哪了", k=2)
    check("检索能带出正文", len(refs) > 0 and len(refs[0]["content"]) > 50)


def test_temp_bank() -> None:
    print("2. 新建记忆库与读写（临时目录）")
    temp = Path(tempfile.mkdtemp(prefix="ai-assistant-test-"))
    try:
        created = scaffold_memory_bank(temp)
        check("能初始化空白记忆库", created.get("ok"))
        check("目录结构齐全", (temp / "04-复盘" / "周复盘").is_dir())
        check("总览已生成", (temp / "记忆库总览.md").exists())

        bank = MemoryBank(temp)
        result = bank.create_note(
            "03-思考",
            "桌面助手的技术选型",
            ["类型/想法", "状态/待确认", "来源/对话"],
            "选了 Python + WebView，因为文件少、体积小。",
        )
        check("能新建思考笔记", result.get("ok"), str(result))
        path = temp / result.get("path", "")
        check("落到了 03-思考/年-月 下", "03-思考/" in result.get("path", ""), result.get("path", ""))
        text = path.read_text(encoding="utf-8")
        check("有 frontmatter", text.startswith("---"))
        check("标签没写 # 号", "#" not in text.split("---")[1])
        check("补了 created", "created:" in text)

        saved = bank.save_note(result["path"], text + "\n追加一行\n")
        check("保存会先备份", saved.get("ok") and saved.get("backup"))
        check("备份文件真实存在", Path(saved.get("backup", "")).exists())
        check("updated 已刷新", f"updated: {__import__('datetime').date.today().isoformat()}" in path.read_text(encoding="utf-8"))

        findings = bank.search("技术选型", 5)
        check("新笔记能被搜到", len(findings) == 1, f"hits={len(findings)}")

        denied = bank.save_note("07-附件/试.md", "x")
        check("07-附件 只读", not denied.get("ok"))
        escaped = bank.save_note("../外面.md", "x")
        check("路径越界被拦", not escaped.get("ok"))
        secret = bank.create_note("00-收件箱", "含密钥", [], "api_key = sk-abcdefghijklmnopqrstuvwx")
        check("敏感内容被拦", not secret.get("ok"), str(secret))

        deleted = bank.delete_note(result["path"])
        check("删除是移入回收站", deleted.get("ok") and "trash" in deleted.get("trash", ""))
        check("原文件已不在", not path.exists())
        check("回收站里能找回", Path(deleted.get("trash", "")).exists())

        check("检查目录可用", check_memory_root(temp).get("ok"))
        check("不存在的目录会报错", not check_memory_root(temp / "没有这个").get("ok"))
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def test_text_utils() -> None:
    print("3. 渲染与解析")
    html = render_markdown("# 标题\n\n- 一\n- 二\n\n```python\nprint(1)\n```\n")
    check("Markdown 渲染出标题", "<h1>" in html)
    check("代码块带复制按钮", "copy-btn" in html)
    emotion, clean = split_emotion("[emotion:happy]诶，这个改动干净多了～")
    check("情绪标记被拆出", emotion == "happy" and clean.startswith("诶"))
    check("没有标记时保持原样", split_emotion("普通回答") == ("", "普通回答"))
    check("情绪表非空", len(EMOTIONS) >= 10)
    raw = '正文\n\n```memo-write\n{"dir":"03-思考","title":"测试","tags":["类型/想法"],"content":"内容"}\n```\n'
    writes = extract_memo_writes(raw)
    check("能识别写入申请", len(writes) == 1 and writes[0]["title"] == "测试")
    check("写入块被从正文移除", "memo-write" not in strip_memo_writes(raw))
    prompt = build_system_prompt(
        {"assistant_name": "小助理", "persona": "温和"}, MemoryBank(store.DEFAULT_CONFIG["memory_root"])
    )
    check("系统提示词含情绪规则", "emotion:" in prompt)
    check("系统提示词含记忆库规则", "frontmatter" in prompt)


if __name__ == "__main__":
    test_real_bank()
    test_temp_bank()
    test_text_utils()
    print(f"\n通过 {passed} 项，失败 {failed} 项")
    sys.exit(1 if failed else 0)
