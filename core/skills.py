"""技能库：扫描本机所有 SKILL.md，解析元数据，供界面浏览与引用。

不引入 YAML 依赖，只按行解析技能用到的固定字段。
"""

from __future__ import annotations

import re
from pathlib import Path

PERSONAL_ROOT = Path.home() / ".codex" / "skills"
PLUGIN_BASES = (
    Path.home() / ".codex" / "plugins" / "cache",
    Path.home() / ".codex" / ".tmp" / "bundled-marketplaces",
    Path.home() / ".cache" / "codex-runtimes",
)

META_BYTES = 8192
DETAIL_LIMIT = 60000
INJECT_LIMIT = 8000

_CACHE: list[dict] | None = None


def _roots() -> list[Path]:
    """按优先级返回要扫描的目录。"""
    roots = [PERSONAL_ROOT]
    for base in PLUGIN_BASES:
        if base.is_dir():
            roots.append(base)
    return [r for r in roots if r.is_dir()]


def _plugin_name(path: Path) -> str:
    """从路径里推断插件名：取 skills 目录前一段，版本哈希再往上退一级。"""
    parts = list(path.parts)
    index = None
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "skills":
            index = i
            break
    if index is None or index == 0:
        return "插件"
    candidate = parts[index - 1]
    # 版本号 / 哈希目录名不是插件名，往上再退一级
    if re.fullmatch(r"[0-9a-f][0-9a-f.]{5,}", candidate) and index >= 2:
        candidate = parts[index - 2]
    return candidate


def _source_of(path: Path) -> str:
    try:
        path.relative_to(PERSONAL_ROOT)
    except ValueError:
        return _plugin_name(path)
    if ".system" in path.parts:
        return "系统"
    return "个人"


def _read_text(path: Path, limit: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _frontmatter(text: str) -> dict:
    """只取 name / description 两个字段，够用且不引依赖。

    description 可能写成多行折叠（>- 或 |），这里把后续缩进行拼起来。
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text[3:]
    lines = block.splitlines()
    result: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not match:
            index += 1
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in (">-", ">", "|", "|-"):
            collected = []
            index += 1
            while index < len(lines) and (lines[index].startswith((" ", "\t")) or not lines[index].strip()):
                collected.append(lines[index].strip())
                index += 1
            result[key] = " ".join(part for part in collected if part).strip()
            continue
        result[key] = _strip_quotes(value)
        index += 1
    return result


def _interface(skill_dir: Path) -> dict:
    """读 agents/openai.yaml 里的界面元数据（中文显示名等）。"""
    path = skill_dir / "agents" / "openai.yaml"
    if not path.is_file():
        return {}
    text = _read_text(path, 4000)
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(display_name|short_description|default_prompt):\s*(.*)$", line)
        if match:
            out[match.group(1)] = _strip_quotes(match.group(2))
    return out


def _as_skill(skill_md: Path) -> dict:
    raw = _read_text(skill_md, META_BYTES)
    meta = _frontmatter(raw)
    interface = _interface(skill_md.parent)
    name = meta.get("name") or skill_md.parent.name
    try:
        size = skill_md.stat().st_size
    except OSError:
        size = 0
    description = meta.get("description", "")
    return {
        "name": name,
        "display_name": interface.get("display_name") or name,
        "short": interface.get("short_description") or description[:80],
        "description": description,
        "default_prompt": interface.get("default_prompt", ""),
        "source": _source_of(skill_md),
        "path": str(skill_md),
        "dir": str(skill_md.parent),
        "bytes": size,
        "chars": size,
        "huge": size > INJECT_LIMIT * 4,
    }


def list_skills(force: bool = False) -> list[dict]:
    """扫描全部技能，同名以优先级高的根目录为准。"""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    found: dict[str, dict] = {}
    order: list[str] = []
    for root in _roots():
        for skill_md in sorted(root.rglob("SKILL.md")):
            try:
                skill = _as_skill(skill_md)
            except Exception:
                continue
            key = skill["name"]
            if key in found:
                continue
            found[key] = skill
            order.append(key)
    skills = [found[key] for key in order]
    skills.sort(key=lambda item: (item["source"] != "个人", item["source"], item["display_name"]))
    _CACHE = skills
    return skills


def read_skill(name: str) -> dict:
    for skill in list_skills():
        if skill["name"] == name:
            text = _read_text(Path(skill["path"]), DETAIL_LIMIT + 1)
            truncated = len(text) > DETAIL_LIMIT
            return {
                "ok": True,
                "name": skill["name"],
                "display_name": skill["display_name"],
                "source": skill["source"],
                "path": skill["path"],
                "bytes": skill["bytes"],
                "content": text[:DETAIL_LIMIT],
                "truncated": truncated,
            }
    return {"ok": False, "error": f"没找到技能：{name}"}


def cached_count() -> int:
    """已经扫过就返回数量，没扫过返回 0——启动路径不该为它付出扫描成本。"""
    return len(_CACHE) if _CACHE is not None else 0


def skill_prompt_block(names: list[str], limit: int = INJECT_LIMIT) -> str:
    """聊天模式用：把技能正文拼成一段提示词，超长截断。"""
    blocks = []
    for name in names or []:
        for skill in list_skills():
            if skill["name"] != name:
                continue
            text = _read_text(Path(skill["path"]), limit + 1)
            cut = len(text) > limit
            body = text[:limit]
            header = f"技能「{skill['display_name']}」（$%s）" % name
            note = "\n\n（该技能说明很长，此处只注入了开头部分）" if cut else ""
            blocks.append(f"{header}：\n{body}{note}")
            break
    if not blocks:
        return ""
    return (
        "用户这次指定了以下技能，请严格按其说明行事；技能说明与你的默认习惯冲突时以技能为准：\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def find_skill(name: str) -> dict | None:
    for skill in list_skills():
        if skill["name"] == name:
            return skill
    return None
