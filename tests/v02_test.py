"""v0.2 自检：助手规则、模型供应商、记忆库切换、会话模式。全程沙箱，不碰真实配置。

用法：python tests/v02_test.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import store  # noqa: E402

sandbox = Path(tempfile.mkdtemp(prefix="ai-assistant-v02-"))
store.DATA_DIR = sandbox
store.SESSIONS_DIR = sandbox / "sessions"
store.BACKUPS_DIR = sandbox / "backups"
store.TRASH_DIR = sandbox / "backups" / "trash"

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


def test_rules(api) -> None:
    print("1. 助手规则")
    rules = api.rules_get()
    check("首次自动生成模板", len(rules["content"]) > 100)
    check("规则文件在 data 目录", str(sandbox) in rules["path"])
    check("能保存规则", api.rules_save("# 我的规则\n- 说话直接点")["ok"])
    check("保存后读回一致", "说话直接点" in api.rules_get()["content"])
    reset = api.rules_reset()
    check("能恢复默认模板", reset["ok"] and "说话方式" in reset["content"])


def test_providers(api) -> None:
    print("2. 模型供应商")
    listing = api.provider_list()
    check("默认有一条供应商", len(listing["providers"]) == 1, str(len(listing["providers"])))
    check("接口不返回明文 Key", "api_key" not in listing["providers"][0])
    check("返回 Key 掩码", bool(listing["providers"][0]["api_key_masked"]) or not listing["providers"][0]["has_key"])
    created = api.provider_save(
        {
            "name": "我的中转站",
            "base_url": "https://relay.example.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "sk-relay-test-1234567890",
        }
    )
    check("能新增供应商", created["ok"])
    new_id = created["id"]
    check("新增后两条", len(api.provider_list()["providers"]) == 2)
    activated = api.provider_activate(new_id)
    check("能切换当前供应商", activated["ok"] and activated["provider"]["name"] == "我的中转站")
    check("顶层模型跟着变", activated["config"]["model"] == "gpt-4o-mini", activated["config"]["model"])
    check("顶层没有明文 Key", "api_key" not in activated["config"])
    api.provider_save(
        {"id": new_id, "name": "改个名", "base_url": "https://relay.example.com/v1", "model": "gpt-4o-mini"}
    )
    still = [p for p in api.provider_list()["providers"] if p["id"] == new_id][0]
    check("改名不会清掉 Key", still["has_key"])
    check("能删除供应商", api.provider_delete(new_id)["ok"])
    check("删掉当前会自动切回", api.provider_list()["active"] != new_id)
    check("预设列表可用", len(api.provider_presets()["presets"]) >= 5)


def test_memory(api) -> None:
    print("3. 记忆库切换")
    check("能读到当前记忆库", bool(api.memory_recent()["current"]))

    empty = sandbox / "空目录"
    empty.mkdir(parents=True, exist_ok=True)
    denied = api.memory_switch({"root": str(empty)})
    check("空目录默认拒绝并说明原因", denied["ok"] is False and denied.get("empty") is True, str(denied))
    check("不存在的目录被拒", api.memory_switch({"root": str(sandbox / "不存在")})["ok"] is False)
    check("空路径被拒", api.memory_switch({"root": ""})["ok"] is False)

    bank = sandbox / "新记忆库"
    ok = api.memory_switch({"root": str(bank), "create": True})
    check("能一键初始化并切过去", ok["ok"], str(ok))
    check("目录结构已建好", (bank / "04-复盘" / "日复盘").is_dir())
    check("统计已刷新", ok["memory"]["count"] >= 2, str(ok["memory"]))
    recent = api.memory_recent()
    check("记进最近列表", str(bank) in recent["recent"])
    check("当前库已更新", recent["current"] == str(bank))
    tree = api.memo_tree()
    check("目录树跟着换库", tree["ok"] and any(n["name"] == "记忆库总览.md" for n in tree["tree"]))


def test_sessions(api) -> None:
    print("4. 会话模式与程序员配置")
    chat = api.session_new("chat")
    prog = api.session_new("prog", str(sandbox), "read-only")
    check("聊天会话 mode=chat", chat["mode"] == "chat")
    check("程序员会话 mode=prog", prog["mode"] == "prog")
    check("记住工作目录", prog["workspace"] == str(sandbox))
    check("记住权限档", prog["sandbox"] == "read-only")
    check("列表带 mode 字段", all("mode" in item for item in api.session_list()))
    configured = api.prog_configure({"session_id": prog["id"], "sandbox": "workspace-write"})
    check("能改权限档", configured["ok"] and configured["session"]["sandbox"] == "workspace-write")
    check("非法权限档被拒", api.prog_configure({"session_id": prog["id"], "sandbox": "乱写"})["ok"] is False)
    check("不存在的目录被拒", api.prog_configure({"session_id": prog["id"], "workspace": "Z:\\没有"})["ok"] is False)


def test_prog(api) -> None:
    print("5. 程序员模式可用性")
    avail = api.prog_available()
    check("检测到 codex", avail["ok"], avail.get("error", ""))
    check("有版本号", "codex" in (avail["version"] or ""), avail["version"])
    check("三档权限齐全", len(avail["sandboxes"]) == 3)
    check("能列出最近工作目录", isinstance(avail["recent_workspaces"], list))


def main() -> None:
    api = app.Api()
    test_rules(api)
    test_providers(api)
    test_memory(api)
    test_sessions(api)
    test_prog(api)
    print(f"\n通过 {passed} 项，失败 {failed} 项")
    shutil.rmtree(sandbox, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
