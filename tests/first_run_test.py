"""模拟一台陌生电脑首次启动：没有 Key、没有记忆库，验证向导流程与密钥不外泄。

全程写进临时目录，不碰你本机的真实配置。
用法：python tests/first_run_test.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import store  # noqa: E402

sandbox = Path(tempfile.mkdtemp(prefix="ai-assistant-fresh-"))
store.DATA_DIR = sandbox
store.SESSIONS_DIR = sandbox / "sessions"
store.BACKUPS_DIR = sandbox / "backups"
store.TRASH_DIR = sandbox / "backups" / "trash"
store.import_key_from_codex = lambda: ""  # 模拟没装 Codex 的电脑

import main as app  # noqa: E402

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
    print("陌生电脑首次启动")
    api = app.Api()

    state = api.setup_state()
    check("检测到还没配 Key", not state["has_key"])
    check("会弹出首次设置向导", state["needs_setup"] is True)
    check("数据目录指向沙箱", state["data_dir"] == str(sandbox), state["data_dir"])

    bank_dir = sandbox / "别人的记忆库"
    result = api.setup_finish(
        {
            "assistant_name": "阿助",
            "api_key": "sk-fake-key-for-test-1234567890",
            "memory_root": str(bank_dir),
            "model": "deepseek-chat",
            "create_memory": True,
        }
    )
    check("向导能走完", result.get("ok"), str(result))
    check("空白记忆库被建好", (bank_dir / "04-复盘" / "日复盘").is_dir())
    check("总览文件已生成", (bank_dir / "记忆库总览.md").exists())
    check("名字生效", result["config"]["assistant_name"] == "阿助")

    state2 = api.setup_state()
    check("再次启动不再弹向导", state2["needs_setup"] is False, str(state2))

    raw = (sandbox / "config.json").read_text(encoding="utf-8")
    check("Key 存在本地配置文件", "sk-fake-key" in raw)
    public = api.settings_get()["config"]
    check("接口不返回明文 Key", "api_key" not in public, str(public.keys()))
    check("只返回掩码", public["api_key_masked"].startswith("sk-fak"), public["api_key_masked"])

    leaked = [
        p
        for p in bank_dir.rglob("*")
        if p.is_file() and "sk-fake-key" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    check("密钥没有写进记忆库", not leaked, str(leaked))

    session = api.session_new()
    check("能建会话", bool(session.get("id")))
    listed = api.session_list()
    check("会话能列出来", len(listed) == 1)
    check("删除会话", api.session_delete(session["id"])["ok"])

    tree = api.memo_tree()
    check("新记忆库的目录树可读", tree.get("ok"))
    check("总览出现在树里", any(n.get("name") == "记忆库总览.md" for n in flatten(tree.get("tree", []))))

    app_config = json.loads(raw)
    check("配置文件是合法 JSON", isinstance(app_config, dict))

    print(f"\n通过 {passed} 项，失败 {failed} 项")
    shutil.rmtree(sandbox, ignore_errors=True)
    sys.exit(1 if failed else 0)


def flatten(nodes):
    for node in nodes:
        yield node
        if node.get("type") == "dir":
            yield from flatten(node.get("children", []))


if __name__ == "__main__":
    main()
