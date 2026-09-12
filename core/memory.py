"""记忆库工作台：扫描、检索、读写、备份、回收站。

所有写入都限制在记忆库根目录内，07-附件 与 .obsidian 只读。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from pathlib import Path

from . import store

READONLY_DIRS = ("07-附件", ".obsidian")
SKIP_DIRS = {".obsidian", ".git", ".trash"}

FRONT_SECTIONS = ["00-收件箱", "01-项目", "02-知识", "03-思考", "04-复盘", "05-目标", "06-模板", "08-归档"]

TAG_SYSTEM = {
    "类型": ["想法", "知识", "项目", "复盘", "目标"],
    "状态": ["进行中", "已完成", "待办", "搁置", "待确认"],
    "领域": ["编程", "嵌入式", "电子工程", "书法", "求职"],
    "优先级": ["高", "中", "低"],
    "来源": ["对话", "阅读", "实践", "灵感"],
}

DEFAULT_TAGS_BY_DIR = {
    "00-收件箱": ["状态/待确认", "来源/对话"],
    "01-项目": ["类型/项目", "状态/进行中", "来源/对话"],
    "02-知识": ["类型/知识", "状态/进行中", "来源/对话"],
    "03-思考": ["类型/想法", "状态/待确认", "来源/对话"],
    "04-复盘": ["类型/复盘", "来源/对话"],
    "05-目标": ["类型/目标", "状态/待确认", "来源/对话"],
}

SENSITIVE_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "API Key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "GitHub Token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "云厂商 Access Key"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*\S{12,}"), "疑似密钥/口令"),
]

# 自动附带笔记时的闸门：只有问到自己身上的事才自动带，避免每句话都塞一万 token
SELF_HINT_WORDS = (
    "我的", "笔记", "记忆库", "上周", "本周", "这周", "上次", "之前", "最近",
    "进展", "进度", "复盘", "记录", "整理", "总结", "回顾", "项目", "目标",
    "待办", "收件箱", "做了什么", "干到哪", "文档",
)
PER_NOTE_CHARS = 3000
RETRIEVE_BUDGET = 8000
MIN_SCORE = 6.0


def today() -> str:
    return _dt.date.today().isoformat()


class MemoryError_(Exception):
    pass


def scan_sensitive(text: str) -> list[str]:
    hits = []
    for pattern, label in SENSITIVE_PATTERNS:
        if pattern.search(text or ""):
            hits.append(label)
    return hits


class MemoryBank:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    # ---------- 基础 ----------

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    def _resolve(self, rel: str) -> Path:
        rel = (rel or "").replace("\\", "/").strip("/")
        target = (self.root / rel).resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            raise MemoryError_("路径越界，已拒绝")
        return target

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def _is_readonly(self, rel: str) -> bool:
        parts = (rel or "").replace("\\", "/").split("/")
        return any(p in READONLY_DIRS for p in parts)

    def _check_writable(self, rel: str) -> None:
        if self._is_readonly(rel):
            raise MemoryError_("该目录是只读的（07-附件 / .obsidian）")

    # ---------- 读取 ----------

    def tree(self) -> dict:
        if not self.exists:
            return {"ok": False, "error": f"记忆库不存在：{self.root}", "tree": []}
        return {"ok": True, "root": str(self.root), "tree": self._scan(self.root)}

    def _scan(self, folder: Path) -> list[dict]:
        nodes: list[dict] = []
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return nodes
        for entry in entries:
            if entry.name in SKIP_DIRS or entry.name.startswith("."):
                continue
            if entry.is_dir():
                children = self._scan(entry)
                nodes.append(
                    {
                        "type": "dir",
                        "name": entry.name,
                        "path": self._rel(entry),
                        "readonly": self._is_readonly(self._rel(entry)),
                        "children": children,
                    }
                )
            elif entry.suffix.lower() == ".md":
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                nodes.append(
                    {
                        "type": "file",
                        "name": entry.name,
                        "path": self._rel(entry),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "readonly": self._is_readonly(self._rel(entry)),
                    }
                )
        return nodes

    def read_note(self, rel: str) -> dict:
        path = self._resolve(rel)
        if not path.is_file():
            return {"ok": False, "error": "文件不存在"}
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"ok": False, "error": f"读取失败：{exc}"}
        return {
            "ok": True,
            "path": self._rel(path),
            "name": path.name,
            "content": content,
            "readonly": self._is_readonly(rel),
            "title": extract_title(content, path.stem),
            "tags": extract_tags(content),
        }

    def all_notes(self) -> list[dict]:
        notes = []
        if not self.exists:
            return notes
        for path in self.root.rglob("*.md"):
            rel = self._rel(path)
            if any(p in SKIP_DIRS for p in rel.split("/")):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            notes.append(
                {
                    "path": rel,
                    "name": path.name,
                    "title": extract_title(text, path.stem),
                    "tags": extract_tags(text),
                    "text": text,
                    "mtime": mtime,
                }
            )
        return notes

    def search(self, query: str, limit: int = 30) -> list[dict]:
        terms = query_terms(query)
        if not terms:
            return []
        results = []
        for note in self.all_notes():
            haystack_title = note["title"] + " " + note["name"]
            haystack_tags = " ".join(note["tags"])
            haystack_body = note["text"]
            score = 0.0
            title_hit = False
            tag_hit = False
            for term in terms:
                low = term.lower()
                weight = min(len(term), 6)
                if low in haystack_title.lower():
                    score += weight * 4
                    title_hit = True
                if low in haystack_tags.lower():
                    score += weight * 2
                    tag_hit = True
                body_hits = haystack_body.lower().count(low)
                score += min(body_hits, 6) * weight * 0.5
            if score <= 0:
                continue
            results.append(
                {
                    "path": note["path"],
                    "title": note["title"],
                    "name": note["name"],
                    "tags": note["tags"],
                    "score": round(score, 1),
                    "title_hit": title_hit,
                    "tag_hit": tag_hit,
                    "excerpt": make_excerpt(note["text"], terms),
                }
            )
        results.sort(key=lambda r: (-r["score"], r["path"]))
        return results[:limit]

    def retrieve(
        self, query: str, k: int = 4, budget: int = RETRIEVE_BUDGET, force: bool = False
    ) -> list[dict]:
        """给模型用的上下文：检索相关笔记并附上正文。

        默认只在「问到自己身上的事」或者「命中标题/标签」时才附带，
        否则聊闲天、追问一句都会带上一万 token 的无关正文。
        """
        hits = self.search(query, limit=max(k * 4, 12))
        if not hits:
            return []
        if not force and not is_self_reference(query):
            strong = [h for h in hits if h["title_hit"] or h["tag_hit"]]
            if not strong:
                return []
            hits = strong
        floor = max(MIN_SCORE, hits[0]["score"] * 0.25)
        hits = [h for h in hits if h["score"] >= floor]
        picked = []
        used = 0
        for hit in hits:
            if len(picked) >= k:
                break
            note = self.read_note(hit["path"])
            if not note.get("ok"):
                continue
            body = note["content"]
            if len(body) > PER_NOTE_CHARS:
                body = body[:PER_NOTE_CHARS] + "\n\n（本笔记较长，此处只取了前面一部分）"
            if used + len(body) > budget:
                body = body[: max(400, budget - used)]
            used += len(body)
            picked.append({"path": hit["path"], "title": hit["title"], "content": body})
        return picked

    def read_many(self, rels: list[str], budget: int = 16000) -> list[dict]:
        out = []
        used = 0
        for rel in rels or []:
            note = self.read_note(rel)
            if not note.get("ok"):
                continue
            body = note["content"]
            if used + len(body) > budget:
                body = body[: max(400, budget - used)]
            used += len(body)
            out.append({"path": rel, "title": note["title"], "content": body})
        return out

    def overview(self, max_chars: int = 2000) -> str:
        path = self.root / "记忆库总览.md"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        except OSError:
            return ""

    def stats(self) -> dict:
        notes = self.all_notes()
        return {
            "ok": self.exists,
            "root": str(self.root),
            "count": len(notes),
            "newest": max((n["mtime"] for n in notes), default=0),
        }

    # ---------- 写入 ----------

    def save_note(self, rel: str, content: str) -> dict:
        try:
            self._check_writable(rel)
            path = self._resolve(rel)
        except MemoryError_ as exc:
            return {"ok": False, "error": str(exc)}
        if path.suffix.lower() != ".md":
            return {"ok": False, "error": "只能写入 .md 文件"}
        hits = scan_sensitive(content)
        if hits:
            return {
                "ok": False,
                "error": f"内容里疑似有敏感数据（{'、'.join(hits)}），已拒绝写入。",
            }
        existed = path.exists()
        if existed:
            backup = self._backup(path)
        else:
            backup = ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(touch_updated(content), encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"写入失败：{exc}"}
        return {"ok": True, "path": rel, "backup": backup, "created": not existed}

    def create_note(
        self,
        dir_rel: str,
        title: str,
        tags: list[str] | None = None,
        content: str = "",
        kind: str = "",
    ) -> dict:
        title = (title or "").strip()
        if not title:
            return {"ok": False, "error": "标题不能为空"}
        dir_rel = normalize_dir(dir_rel, title, kind)
        try:
            self._check_writable(dir_rel)
        except MemoryError_ as exc:
            return {"ok": False, "error": str(exc)}
        hits = scan_sensitive(content)
        if hits:
            return {
                "ok": False,
                "error": f"内容里疑似有敏感数据（{'、'.join(hits)}），已拒绝写入。",
            }
        filename = suggest_filename(dir_rel, title, kind)
        rel = f"{dir_rel.rstrip('/')}/{filename}"
        path = self._resolve(rel)
        if path.exists():
            stem = path.stem
            rel = f"{dir_rel.rstrip('/')}/{stem}-2.md"
            path = self._resolve(rel)
        final_tags = normalize_tags(tags) or default_tags(dir_rel)
        body = ensure_frontmatter(content, final_tags)
        if not re.search(r"^#\s+\S", body, re.MULTILINE):
            body = body.rstrip() + f"\n\n# {title}\n"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"写入失败：{exc}"}
        return {
            "ok": True,
            "path": rel,
            "title": title,
            "tags": final_tags,
            "overview_stale": True,
        }

    def delete_note(self, rel: str) -> dict:
        try:
            self._check_writable(rel)
            path = self._resolve(rel)
        except MemoryError_ as exc:
            return {"ok": False, "error": str(exc)}
        if not path.is_file():
            return {"ok": False, "error": "文件不存在"}
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        store.ensure_dirs()
        target = store.TRASH_DIR / f"{stamp}-{path.name}"
        try:
            shutil.move(str(path), str(target))
        except OSError as exc:
            return {"ok": False, "error": f"删除失败：{exc}"}
        return {"ok": True, "trash": str(target)}

    def _backup(self, path: Path) -> str:
        store.ensure_dirs()
        rel = self._rel(path)
        target = store.BACKUPS_DIR / _dt.date.today().isoformat() / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return str(target)

    def open_in_explorer(self, rel: str) -> dict:
        path = self._resolve(rel) if rel else self.root
        if not path.exists():
            return {"ok": False, "error": "路径不存在"}
        import subprocess

        if path.is_dir():
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["explorer", "/select,", str(path)])
        return {"ok": True}


# ---------- 纯函数 ----------


def extract_title(text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+)$", text or "", re.MULTILINE)
    if match:
        return match.group(1).strip()
    return fallback


def extract_tags(text: str) -> list[str]:
    match = re.search(r"^tags:\s*\[(.*?)\]", text or "", re.MULTILINE)
    if not match:
        return []
    return [t.strip() for t in match.group(1).split(",") if t.strip()]


def normalize_tags(tags) -> list[str]:
    valid = {f"{group}/{name}" for group, names in TAG_SYSTEM.items() for name in names}
    out = []
    for tag in tags or []:
        tag = str(tag).strip().lstrip("#")
        if tag in valid and tag not in out:
            out.append(tag)
    return out


def _section_of(dir_rel: str) -> str:
    first = (dir_rel or "").replace("\\", "/").split("/")[0]
    return first if first in FRONT_SECTIONS else ""


def default_tags(dir_rel: str) -> list[str]:
    return DEFAULT_TAGS_BY_DIR.get(_section_of(dir_rel), ["状态/待确认", "来源/对话"])


def normalize_dir(dir_rel: str, title: str, kind: str = "") -> str:
    """把目标目录补全成规范目录，例如 03-思考 → 03-思考/YYYY-MM。"""
    dir_rel = (dir_rel or "").replace("\\", "/").strip("/")
    section = _section_of(dir_rel)
    if section == "03-思考":
        return f"03-思考/{today()[:7]}"
    if section == "04-复盘":
        sub = "日复盘"
        for candidate, keyword in (("日复盘", "日复盘"), ("周复盘", "周复盘"), ("月复盘", "月复盘"), ("项目复盘", "项目复盘")):
            if keyword in kind or keyword in dir_rel or keyword in title:
                sub = candidate
                break
        return f"04-复盘/{sub}"
    if section == "02-知识":
        parts = [p for p in dir_rel.split("/") if p]
        if len(parts) < 2:
            return "02-知识/编程"
        return dir_rel
    return dir_rel or "00-收件箱"


def _slug(text: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "", text or "").strip()
    cleaned = cleaned.replace(" ", "-")
    return cleaned[:40] or "未命名"


def suggest_filename(dir_rel: str, title: str, kind: str = "") -> str:
    section = _section_of(dir_rel)
    name = _slug(title)
    if section == "03-思考":
        return f"{today()}-{name}.md"
    if section == "04-复盘":
        sub = dir_rel.split("/")[-1]
        if sub == "项目复盘":
            return f"{today()}-{name}项目复盘.md"
        return f"{today()}-{sub}.md"
    if section == "01-项目":
        return f"{name}-项目笔记.md"
    if section == "02-知识":
        return f"{name}.md"
    return f"{today()}-{name}.md"


def ensure_frontmatter(content: str, tags: list[str]) -> str:
    stamp = today()
    if (content or "").lstrip().startswith("---"):
        return content
    tag_line = ", ".join(tags) if tags else "状态/待确认"
    header = f"---\ntags: [{tag_line}]\ncreated: {stamp}\nupdated: {stamp}\n---\n\n"
    return header + (content or "").lstrip()


def touch_updated(content: str) -> str:
    stamp = today()
    text = content or ""
    if not text.lstrip().startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    head, rest = text[: end + 4], text[end + 4 :]
    if re.search(r"^updated:", head, re.MULTILINE):
        head = re.sub(r"^updated:.*$", f"updated: {stamp}", head, count=1, flags=re.MULTILINE)
    else:
        head = head.rstrip("\n") + f"\nupdated: {stamp}\n---"
    return head + rest


def query_terms(query: str) -> list[str]:
    """中文没有空格，长句整段匹配会落空，所以再切一层二元词。"""
    chunks = [t for t in re.split(r"[\s,，、。；;?!？!「」（）()\[\]【】]+", query or "") if t]
    terms: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        candidates = [chunk.lower()]
        if len(chunk) > 2 and not re.fullmatch(r"[A-Za-z0-9_\-.]+", chunk):
            candidates.extend(chunk[i : i + 2].lower() for i in range(len(chunk) - 1))
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                terms.append(candidate)
    return terms


def is_self_reference(query: str) -> bool:
    """判断这句话是不是在问"我自己的东西"。"""
    text = (query or "").strip()
    if not text:
        return False
    return any(word in text for word in SELF_HINT_WORDS)


def make_excerpt(text: str, terms: list[str], width: int = 90) -> str:
    body = re.sub(r"^---.*?---", "", text or "", count=1, flags=re.DOTALL).strip()
    body = re.sub(r"\s+", " ", body)
    position = -1
    for term in terms:
        found = body.lower().find(term.lower())
        if found != -1 and (position == -1 or found < position):
            position = found
    if position == -1:
        return body[:width]
    start = max(0, position - width // 3)
    return ("…" if start else "") + body[start : start + width]


_MD = None


def render_markdown(text: str) -> str:
    """服务端渲染 Markdown，前端零依赖。"""
    global _MD
    if _MD is None:
        from markdown_it import MarkdownIt

        _MD = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(
            ["table", "strikethrough"]
        )

    def fence(renderer, tokens, idx, options, env):
        token = tokens[idx]
        info = (token.info or "").strip().split()[0] if token.info else ""
        code = token.content
        if info:
            try:
                from pygments import highlight
                from pygments.formatters import HtmlFormatter
                from pygments.lexers import get_lexer_by_name

                lexer = get_lexer_by_name(info, stripall=False)
                formatter = HtmlFormatter(nowrap=True)
                return (
                    '<div class="code-block"><div class="code-head"><span>'
                    + _escape(info)
                    + '</span><button class="copy-btn" type="button">复制</button></div>'
                    "<pre><code>"
                    + highlight(code, lexer, formatter)
                    + "</code></pre></div>"
                )
            except Exception:
                pass
        return (
            '<div class="code-block"><div class="code-head"><span>'
            + _escape(info or "text")
            + '</span><button class="copy-btn" type="button">复制</button></div>'
            "<pre><code>" + _escape(code) + "</code></pre></div>"
        )

    _MD.add_render_rule("fence", fence)
    return _MD.render(text or "")


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def extract_memo_writes(text: str) -> list[dict]:
    """从回答里挖出 memo-write 代码块（助手写入记忆库必须先经用户确认）。"""
    writes = []
    for match in re.finditer(r"```memo-write\s*\n(.*?)```", text or "", re.DOTALL):
        raw = match.group(1).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("title"):
            writes.append(payload)
    return writes


def strip_memo_writes(text: str) -> str:
    return re.sub(r"```memo-write\s*\n.*?```", "", text or "", flags=re.DOTALL).strip()


SCAFFOLD_DIRS = [
    "00-收件箱",
    "01-项目",
    "02-知识/编程",
    "03-思考",
    f"03-思考/{today()[:7]}",
    "04-复盘/日复盘",
    "04-复盘/周复盘",
    "04-复盘/月复盘",
    "04-复盘/项目复盘",
    "05-目标",
    "06-模板",
    "07-附件",
    "08-归档",
]

INBOX_README = """# 收件箱索引

所有尚未分类、待确认或待整理的内容先放在本目录。

## 索引

| 日期 | 文件 | 状态 | 下一步 |
|---|---|---|---|
| — | 当前为空 | — | — |
"""

BANK_AGENTS = """# 记忆库规则

## 目录含义

| 目录 | 放什么 |
|---|---|
| 00-收件箱 | 还没想清楚、待确认、待归类的内容 |
| 01-项目 | 正在做的东西，一个项目一篇 |
| 02-知识/<领域> | 以后可能用得上的知识点 |
| 03-思考/YYYY-MM | 想法与判断 |
| 04-复盘 | 日/周/月/项目复盘 |
| 05-目标 | 阶段性目标 |
| 06-模板 | 笔记模板 |
| 07-附件 | 图片、PDF 等附件（只读） |
| 08-归档 | 完成或过时的内容 |

## 写作规范

1. 每篇笔记开头必须有 YAML frontmatter，至少含 `tags` 和 `created`；改动过就补 `updated`。
2. 标签只能从固定体系取：类型/想法|知识|项目|复盘|目标；状态/进行中|已完成|待办|搁置|待确认；领域/编程|嵌入式|电子工程|书法|求职；优先级/高|中|低；来源/对话|阅读|实践|灵感。YAML 里不要写 `#`。
3. 命名：思考 `YYYY-MM-DD-标题.md`；复盘 `YYYY-MM-DD-日复盘.md`；知识 `领域-知识点.md`；项目 `项目名-笔记类型.md`。
4. 双链 `[[笔记名]]` 必须指向真实存在的文件。
5. 不写入密码、API Key、Token、Cookie、身份信息、私人联系方式。
"""

BANK_OVERVIEW = """---
tags: [类型/知识, 状态/进行中, 来源/对话]
created: {date}
updated: {date}
---

# 记忆库总览

这是记忆库的首页。新增或修改笔记后，回来补一行。

## 项目

| 项目 | 状态 | 领域 | 最近进展 |
|---|---|---|---|
| — | — | — | — |

## 知识

| 笔记 | 主题 |
|---|---|
| — | — |

## 思考

| 笔记 | 一句话 |
|---|---|
| — | — |

## 复盘

| 笔记 | 覆盖范围 |
|---|---|
| — | — |
"""


def scaffold_memory_bank(root: str | Path) -> dict:
    """给新用户建一个空白记忆库，目录结构与本机规范一致。"""
    root = Path(root)
    try:
        for rel in SCAFFOLD_DIRS:
            (root / rel).mkdir(parents=True, exist_ok=True)
        files = {
            "00-收件箱/README.md": INBOX_README,
            "AGENTS.md": BANK_AGENTS,
            "记忆库总览.md": BANK_OVERVIEW.format(date=today()),
        }
        for rel, content in files.items():
            path = root / rel
            if not path.exists():
                path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"创建记忆库失败：{exc}"}
    return {"ok": True, "root": str(root)}


def check_memory_root(root: str | Path) -> dict:
    """判断一个目录能不能当记忆库用。"""
    root = Path(root or "")
    if not root.is_dir():
        return {"ok": False, "error": f"目录不存在：{root}"}
    try:
        notes = list(root.rglob("*.md"))
    except OSError as exc:
        return {"ok": False, "error": f"读取失败：{exc}"}
    return {
        "ok": True,
        "root": str(root),
        "count": len(notes),
        "empty": len(notes) == 0,
    }
