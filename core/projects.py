"""项目：把「工作目录」记录在记忆库的项目笔记里。

一个项目 = 记忆库 01-项目/ 下的一篇项目笔记，frontmatter 里的 path 字段指向它的工作目录。
这样项目清单跟着记忆库走，换机器也用同一份记录。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_DIR = "01-项目"


def _parse_path(text: str) -> str:
    match = re.search(r"^path:\s*(.+)$", text or "", re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def _write_path(text: str, path: str) -> str:
    line = f"path: {path}"
    if re.search(r"^path:.*$", text, re.MULTILINE):
        # 用函数做替换：直接传字符串的话，Windows 路径里的 \U 会被当成正则转义符
        return re.sub(r"^path:.*$", lambda _match: line, text, count=1, flags=re.MULTILINE)
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[:end] + f"\n{line}" + text[end:]
    return f"---\n{line}\n---\n\n" + text


def project_dir(root: str | Path) -> Path:
    return Path(root or "") / PROJECT_DIR


def list_projects(root: str | Path) -> list[dict]:
    folder = project_dir(root)
    if not folder.is_dir():
        return []
    items = []
    for note in sorted(folder.glob("*.md")):
        try:
            text = note.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        path = _parse_path(text)
        name = re.sub(r"-项目笔记$", "", note.stem)
        items.append(
            {
                "name": name,
                "note": f"{PROJECT_DIR}/{note.name}",
                "path": path,
                "exists": bool(path) and Path(path).is_dir(),
                "has_path": bool(path),
            }
        )
    return items


def _slug(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "", (name or "").strip())
    return cleaned.replace(" ", "-")[:40] or "未命名项目"


def create_project(root: str | Path, name: str, path: str = "", create_dir: bool = False) -> dict:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "项目名不能为空"}
    folder = project_dir(root)
    if not folder.is_dir():
        return {"ok": False, "error": f"记忆库里没有 {PROJECT_DIR} 目录"}
    note = folder / f"{_slug(name)}-项目笔记.md"
    if note.exists():
        return {"ok": False, "error": f"已经有同名项目了：{note.name}"}

    path = (path or "").strip().strip('"')
    if path and not Path(path).is_dir():
        if create_dir:
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"ok": False, "error": f"建不了这个目录：{exc}"}
        else:
            return {"ok": False, "error": f"目录不存在：{path}（可以勾选自动创建）"}

    stamp = __import__("datetime").date.today().isoformat()
    body = (
        "---\n"
        "tags: [类型/项目, 状态/进行中, 来源/对话]\n"
        f"created: {stamp}\n"
        f"updated: {stamp}\n"
        f"path: {path}\n"
        "---\n\n"
        f"# {name}\n\n"
        "## 目标\n\n"
        "## 关键决策\n\n"
        "## 当前进展\n\n"
        "## 任务清单\n\n- [ ] \n"
    )
    try:
        note.write_text(body, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"写不了项目笔记：{exc}"}
    return {"ok": True, "name": name, "note": f"{PROJECT_DIR}/{note.name}", "path": path}


def set_project_path(root: str | Path, note_rel: str, path: str) -> dict:
    note = Path(root or "") / note_rel
    if not note.is_file():
        return {"ok": False, "error": "项目笔记不存在"}
    try:
        text = note.read_text(encoding="utf-8", errors="replace")
        note.write_text(_write_path(text, (path or "").strip()), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"写入失败：{exc}"}
    return {"ok": True}
