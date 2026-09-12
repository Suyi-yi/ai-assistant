"""助手规则：用户可以自己改的行为准则，存在 data/rules.md。

优先级：内置硬规则（情绪表达、写入协议、记忆库规范）＜ 全局规则文件 ＜ 记忆库里的专属规则。
"""

from __future__ import annotations

from pathlib import Path

from .store import DATA_DIR, ensure_dirs

RULES_FILENAME = "rules.md"
BANK_RULES_FILENAME = "小助理规则.md"
MAX_RULES_CHARS = 6000

DEFAULT_TEMPLATE = """# 小助理规则

> 这个文件决定小助理怎么跟你说话、怎么干活。改完保存立刻生效，不用重启。
> 每条都可以删改，删掉就退回默认行为。

## 说话方式

- 先给结论，再给理由，不要「首先 / 其次 / 最后」这种模板腔。
- 不确定就说不确定，不要编一个看起来合理的答案。
- 我说错了直接指出来，别顺着我讲。

## 干活方式

- 要动我的记忆库时，先说清动哪个文件、为什么，等我确认。
- 引用我的笔记时标明是哪一篇。
- 不要一口气甩给我一堆选项，先给一个你推荐的。

## 边界

- 不联网、不执行命令，只做对话和记忆库读写。
- 不把密码、API Key、Token 这类东西写进任何笔记。
- 拿不准该不该记的内容，先问我要不要记，别直接写。
"""


def rules_path() -> Path:
    ensure_dirs()
    return DATA_DIR / RULES_FILENAME


def load_rules(create: bool = True) -> str:
    path = rules_path()
    if not path.exists():
        if not create:
            return ""
        path.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
        return DEFAULT_TEMPLATE
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def save_rules(text: str) -> dict:
    path = rules_path()
    try:
        path.write_text(text or "", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"保存失败：{exc}"}
    return {"ok": True, "path": str(path)}


def reset_rules() -> dict:
    result = save_rules(DEFAULT_TEMPLATE)
    return {**result, "content": DEFAULT_TEMPLATE}


def bank_rules(memory_root: str | Path) -> str:
    """记忆库目录里可以再放一份专属规则，覆盖全局规则。"""
    path = Path(memory_root or "") / BANK_RULES_FILENAME
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def compose_rules(config: dict) -> str:
    parts = []
    global_rules = load_rules().strip()
    if global_rules:
        parts.append("# 用户给这个助手定的规则（优先于你的默认习惯）\n\n" + global_rules[:MAX_RULES_CHARS])
    local = bank_rules(config.get("memory_root", "")).strip()
    if local:
        parts.append(
            "# 当前记忆库的专属规则（优先级最高）\n\n" + local[:MAX_RULES_CHARS]
        )
    return "\n\n---\n\n".join(parts)
