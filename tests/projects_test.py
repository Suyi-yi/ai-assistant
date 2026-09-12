"""项目功能自检：项目清单来自记忆库 01-项目/，工作目录写在项目笔记里。

用法：python tests/projects_test.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import projects, store  # noqa: E402
from core.memory import scaffold_memory_bank  # noqa: E402

sandbox = Path(tempfile.mkdtemp(prefix="ai-assistant-projects-"))
store.DATA_DIR = sandbox
store.SESSIONS_DIR = sandbox / "sessions"
store.BACKUPS_DIR = sandbox / "backups"
store.TRASH_DIR = sandbox / "backups" / "trash"

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


def main() -> None:
    bank = sandbox / "记忆库"
    scaffold_memory_bank(bank)
    print(f"记忆库：{bank}")

    check("初始没有项目", projects.list_projects(bank) == [])

    workspace = sandbox / "我的网站"
    created = projects.create_project(bank, "我的网站", str(workspace), create_dir=True)
    check("能建项目", created["ok"], str(created))
    check("工作目录被创建", workspace.is_dir())
    note = bank / created["note"]
    check("项目笔记落在 01-项目/", note.parent.name == "01-项目", str(note))
    text = note.read_text(encoding="utf-8")
    check("frontmatter 里记了路径", f"path: {workspace}" in text, text.splitlines()[5])
    check("标签合法（不带 #）", "#" not in text.split("---")[1])

    listing = projects.list_projects(bank)
    check("列表能读出来", len(listing) == 1, str(listing))
    check("名字去掉了后缀", listing[0]["name"] == "我的网站", listing[0]["name"])
    check("路径可读且存在", listing[0]["path"] == str(workspace) and listing[0]["exists"])

    dup = projects.create_project(bank, "我的网站", "", create_dir=False)
    check("重名被拒", dup["ok"] is False, str(dup))

    missing = projects.create_project(bank, "不存在的目录项目", str(sandbox / "没有这里"), create_dir=False)
    check("目录不存在且不自动创建时被拒", missing["ok"] is False, str(missing))

    moved = sandbox / "换过目录"
    moved.mkdir()
    updated = projects.set_project_path(bank, created["note"], str(moved))
    check("能改工作目录", updated["ok"])
    listing = projects.list_projects(bank)
    check("改完读回新路径", listing[0]["path"] == str(moved), listing[0]["path"])

    # 手工写的项目笔记（没有 path 字段）也要能列出来
    manual = bank / "01-项目" / "老项目-项目笔记.md"
    manual.write_text("---\ntags: [类型/项目]\ncreated: 2026-01-01\n---\n\n# 老项目\n", encoding="utf-8")
    listing = projects.list_projects(bank)
    old = [item for item in listing if item["name"] == "老项目"]
    check("没有 path 的老笔记也能列出", len(old) == 1 and old[0]["has_path"] is False)

    print(f"\n通过 {passed} 项，失败 {failed} 项")
    shutil.rmtree(sandbox, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
