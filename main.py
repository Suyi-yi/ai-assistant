"""AI 小助理 —— 桌面窗口入口。"""

from __future__ import annotations

import json
import sys
import threading
import time
import webbrowser
import datetime
from pathlib import Path

import webview

from core import store
from core import rules as rules_module
from core import skills as skills_module
from core import updater
from core.codex_runner import DEFAULT_SANDBOX, CodexJob, codex_version, find_codex
from core.llm import (
    EMOTIONS,
    PERSONA_PRESETS,
    LLMError,
    build_system_prompt,
    list_models,
    resolve_provider,
    split_emotion,
    stream_chat,
    test_provider,
)
from core.memory import (
    MemoryBank,
    check_memory_root,
    extract_memo_writes,
    render_markdown,
    scaffold_memory_bank,
    strip_memo_writes,
)
from core.winui import apply_dark_titlebar

VERSION = "0.2.0"
HISTORY_LIMIT = 20
CONFIG = store.load_config()


def ui_dir() -> Path:
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", ".")) / "ui"
        if bundled.is_dir():
            return bundled
        return Path(sys.executable).resolve().parent / "ui"
    return Path(__file__).resolve().parent / "ui"


def build_html() -> str:
    """把 index.html + css + js 读进内存拼成一份页面。

    不能直接把文件路径丢给 WebView2：它会缓存本地文件，改了界面看到的还是旧版本。
    """
    base = ui_dir()
    html = (base / "index.html").read_text(encoding="utf-8")
    replacements = [
        ('<link rel="stylesheet" href="style.css">',
         lambda: "<style>\n" + (base / "style.css").read_text(encoding="utf-8") + "\n</style>"),
        ('<script src="app.js"></script>',
         lambda: "<script>\n" + (base / "app.js").read_text(encoding="utf-8") + "\n</script>"),
        ('<script src="app2.js"></script>',
         lambda: "<script>\n" + (base / "app2.js").read_text(encoding="utf-8") + "\n</script>"),
    ]
    for marker, loader in replacements:
        if marker not in html:
            raise SystemExit(f"界面文件里找不到这段标记，无法内联：{marker}")
        html = html.replace(marker, loader())
    return html


class Api:
    """暴露给前端的接口。

    注意：所有状态必须用下划线开头的私有属性。pywebview 会递归遍历公开属性来生成
    桥接函数，公开的窗口对象会让它走进 WinForms 无障碍树并无限递归，直接把界面线程卡死。
    """

    def __init__(self):
        self._window: webview.Window | None = None
        self._config = dict(CONFIG)
        self._bank = MemoryBank(self._config.get("memory_root", ""))
        self._lock = threading.Lock()
        self._prog_jobs: dict[str, CodexJob] = {}
        self._prog_lock = threading.Lock()
        self._codex_info: dict | None = None

    # ---------- 通用 ----------

    def _emit(self, name: str, payload: dict) -> None:
        if self._window is None:
            return
        try:
            data = json.dumps(payload, ensure_ascii=False)
            # 必须让脚本返回原始值：返回 Promise 会让 pythonnet 转换失败并把界面线程拖死
            self._window.evaluate_js(
                "(function(){ window.__aiAssistant && window.__aiAssistant."
                f"{name}({data}); return 'ok'; }})()"
            )
        except Exception:
            pass

    def app_info(self) -> dict:
        # 启动路径只做轻活：codex 版本号和技能扫描都延后到真正要用的时候
        path = find_codex(self._config.get("codex_path", ""))
        provider = resolve_provider(self._config)
        return {
            "version": VERSION,
            "ui_dir": str(ui_dir()),
            "data_dir": str(store.DATA_DIR),
            "config": public_config(self._config),
            "memory": self._bank.stats(),
            "tag_system": tag_system(),
            "emotions": EMOTIONS,
            "persona_presets": PERSONA_PRESETS,
            "provider": {
                "id": provider.get("id", ""),
                "name": provider.get("name", ""),
                "model": provider.get("model", ""),
            },
            "prog": {
                "ok": bool(path),
                "version": "",
                "sandboxes": DEFAULT_SANDBOXES(),
            },
            "skills_count": skills_module.cached_count(),
        }

    def setup_state(self) -> dict:
        memory = check_memory_root(self._config.get("memory_root", ""))
        has_key = bool((self._config.get("api_key") or "").strip())
        return {
            "ok": True,
            "needs_setup": (not has_key) or (not memory.get("ok")),
            "has_key": has_key,
            "memory": memory,
            "data_dir": str(store.DATA_DIR),
            "assistant_name": self._config.get("assistant_name", "小助理"),
        }

    def setup_finish(self, payload: dict) -> dict:
        payload = dict(payload or {})
        memory_root = (payload.get("memory_root") or "").strip()
        if not memory_root:
            return {"ok": False, "error": "请先选一个记忆库目录"}
        if payload.get("create_memory"):
            created = scaffold_memory_bank(memory_root)
            if not created.get("ok"):
                return created
        check = check_memory_root(memory_root)
        if not check.get("ok"):
            return check
        patch = {
            "memory_root": memory_root,
            "api_key": (payload.get("api_key") or "").strip(),
            "assistant_name": (payload.get("assistant_name") or "小助理").strip(),
            "model": (payload.get("model") or "deepseek-chat").strip(),
        }
        result = self.settings_set(patch)
        return {**result, "setup_done": True}

    def pick_folder(self, initial: str = "") -> dict:
        if self._window is None:
            return {"ok": False, "error": "窗口还没准备好"}
        try:
            picked = self._window.create_file_dialog(
                webview.FOLDER_DIALOG, directory=(initial or "").strip()
            )
        except Exception as exc:  # 某些环境不支持原生选择框，退回手填
            return {"ok": False, "error": f"打不开文件夹选择框：{exc}"}
        if not picked:
            return {"ok": False, "error": "cancelled"}
        path = picked[0] if isinstance(picked, (list, tuple)) else picked
        return {"ok": True, "path": str(path)}

    def memo_scaffold(self, path: str) -> dict:
        return scaffold_memory_bank(path or "")

    def memo_check(self, path: str) -> dict:
        return check_memory_root(path or "")

    def open_external(self, url: str) -> dict:
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)
            return {"ok": True}
        return {"ok": False}

    def open_path(self, path: str) -> dict:
        """在资源管理器里打开任意一个存在的本地路径（技能目录等）。"""
        target = Path((path or "").strip().strip('"'))
        if not target.exists():
            return {"ok": False, "error": f"路径不存在：{target}"}
        import subprocess

        if target.is_dir():
            subprocess.Popen(["explorer", str(target)])
        else:
            subprocess.Popen(["explorer", "/select,", str(target)])
        return {"ok": True}

    def render_markdown(self, text: str) -> str:
        return render_markdown(text)

    # ---------- 设置 ----------

    def settings_get(self) -> dict:
        return {"ok": True, "config": public_config(self._config)}

    def settings_set(self, patch: dict) -> dict:
        patch = dict(patch or {})
        if "temperature" in patch:
            try:
                patch["temperature"] = max(0.0, min(2.0, float(patch["temperature"])))
            except (TypeError, ValueError):
                patch.pop("temperature", None)
        for key in ("assistant_name", "persona", "memory_root", "codex_path"):
            if key in patch and isinstance(patch[key], str):
                patch[key] = patch[key].strip()
        if not patch.get("memory_root"):
            patch.pop("memory_root", None)
        # api_key / base_url / model 现在属于供应商，写进当前生效的那条
        provider_patch = {}
        for key in ("api_key", "base_url", "model"):
            if key in patch:
                provider_patch[key] = patch.pop(key)
        if provider_patch:
            providers = [dict(p) for p in (self._config.get("providers") or [])]
            active = self._config.get("active_provider")
            for item in providers:
                if item.get("id") == active:
                    for key, value in provider_patch.items():
                        if key == "api_key" and not (value or "").strip():
                            continue
                        item[key] = value.strip() if isinstance(value, str) else value
                    break
            patch["providers"] = providers
        self._config = store.save_config({**self._config, **patch})
        self._config = store.save_config(store.migrate_providers(self._config))
        if "codex_path" in patch:
            self._codex_info = None
        self._bank = MemoryBank(self._config.get("memory_root", ""))
        return {"ok": True, "config": public_config(self._config), "memory": self._bank.stats()}

    def import_key_from_codex(self) -> dict:
        key = store.import_key_from_codex()
        if not key:
            return {"ok": False, "error": "没在 ~/.codex/config.toml 里找到可用的 key"}
        return {"ok": True, "api_key": key}

    # ---------- 会话 ----------

    def session_list(self) -> list[dict]:
        return store.list_sessions()

    def session_new(self, mode: str = "chat", workspace: str = "", sandbox: str = "") -> dict:
        return store.new_session(
            mode="prog" if mode == "prog" else "chat",
            workspace=workspace or "",
            sandbox=sandbox or self._config.get("default_sandbox", DEFAULT_SANDBOX),
        )

    def session_load(self, session_id: str) -> dict:
        session = store.load_session(session_id)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        return {"ok": True, "session": session}

    def session_rename(self, session_id: str, title: str) -> dict:
        session = store.rename_session(session_id, title or "")
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        return {"ok": True, "session": session}

    def session_delete(self, session_id: str) -> dict:
        return {"ok": store.delete_session(session_id)}

    def session_set_mode(self, payload: dict) -> dict:
        """在同一个会话里切聊天 / 天才程序员，不再新建会话。"""
        payload = dict(payload or {})
        session_id = payload.get("session_id") or ""
        if not store.load_session(session_id):
            return {"ok": False, "error": "会话不存在"}
        mode = payload.get("mode")
        if mode not in ("chat", "plan", "prog"):
            mode = "chat"
        fields: dict = {"mode": mode}
        if mode in ("plan", "prog"):
            workspace = (payload.get("workspace") or "").strip()
            if not workspace:
                workspace = self._config.get("memory_root") or str(store.DATA_DIR)
            if not Path(workspace).is_dir():
                return {"ok": False, "error": f"目录不存在：{workspace}"}
            sandbox = payload.get("sandbox") or self._config.get("default_sandbox", DEFAULT_SANDBOX)
            if sandbox not in SANDBOX_KEYS():
                return {"ok": False, "error": "权限档位不合法"}
            fields["workspace"] = workspace
            fields["sandbox"] = sandbox
            recent = [workspace] + [
                item for item in (self._config.get("recent_workspaces") or []) if item != workspace
            ]
            self._config = store.save_config({**self._config, "recent_workspaces": recent[:5]})
        session = store.update_session(session_id, **fields)
        return {"ok": True, "session": session}

    def project_list(self) -> dict:
        from core import projects as projects_module

        return {"ok": True, "projects": projects_module.list_projects(self._config.get("memory_root", ""))}

    def project_create(self, payload: dict) -> dict:
        from core import projects as projects_module

        payload = dict(payload or {})
        result = projects_module.create_project(
            self._config.get("memory_root", ""),
            payload.get("name", ""),
            payload.get("path", ""),
            bool(payload.get("create_dir")),
        )
        if result.get("ok"):
            self._bank = MemoryBank(self._config.get("memory_root", ""))
        return result

    def project_set_path(self, payload: dict) -> dict:
        from core import projects as projects_module

        payload = dict(payload or {})
        return projects_module.set_project_path(
            self._config.get("memory_root", ""), payload.get("note", ""), payload.get("path", "")
        )

    # ---------- 记忆库 ----------

    def memo_tree(self) -> dict:
        return self._bank.tree()

    def memo_read(self, rel_path: str) -> dict:
        return self._bank.read_note(rel_path)

    def memo_save(self, rel_path: str, content: str) -> dict:
        return self._bank.save_note(rel_path, content)

    def memo_create(self, payload: dict) -> dict:
        payload = dict(payload or {})
        return self._bank.create_note(
            payload.get("dir", "00-收件箱"),
            payload.get("title", ""),
            payload.get("tags") or [],
            payload.get("content", ""),
            payload.get("kind", ""),
        )

    def memo_delete(self, rel_path: str) -> dict:
        return self._bank.delete_note(rel_path)

    def memo_search(self, query: str, limit: int = 30) -> list[dict]:
        return self._bank.search(query or "", int(limit or 30))

    def memo_stats(self) -> dict:
        return self._bank.stats()

    def memo_open(self, rel_path: str = "") -> dict:
        return self._bank.open_in_explorer(rel_path or "")

    def memo_overview_stale(self) -> dict:
        return {"ok": True, "path": "记忆库总览.md"}

    def usage_stats(self) -> dict:
        return {"ok": True, "total": normalize_usage_total(self._config.get("usage_total"))}

    # ---------- 更新 ----------

    def update_check(self, force: bool = False) -> dict:
        source = (self._config.get("update_source") or "").strip()
        self._config = store.save_config({**self._config, "update_checked_at": time.time()})
        if not source:
            return {"ok": True, "configured": False, "current": VERSION}
        result = updater.check_update(VERSION, source)
        result["configured"] = True
        result["current"] = VERSION
        return result

    def update_download(self) -> dict:
        source = (self._config.get("update_source") or "").strip()
        if not source:
            return {"ok": False, "error": "还没填更新源"}
        manifest = updater.check_update(VERSION, source)
        if not manifest.get("ok"):
            return manifest
        if not manifest.get("has_update"):
            return {"ok": False, "error": "已经是最新版本了"}
        downloaded = updater.download_zip(manifest["url"])
        if not downloaded.get("ok"):
            return downloaded
        return {
            "ok": True,
            "zip": downloaded["path"],
            "version": manifest["version"],
            "bytes": downloaded["bytes"],
        }

    def update_apply(self, zip_path: str) -> dict:
        import os

        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else store.BASE_DIR
        return updater.apply_update(
            zip_path, str(base), str(base / "AI小助理.exe"), os.getpid()
        )

    def close_app(self) -> dict:
        """让界面调用：销毁窗口，好让更新脚本接手覆盖安装。"""
        threading.Timer(0.8, self._destroy_window).start()
        return {"ok": True}

    def _destroy_window(self) -> None:
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass

    def record_usage(self, usage: dict) -> dict:
        """归一化这一轮的用量、算出花费、累加进全局统计，并把结果返回给调用方存进消息。"""
        normalized = normalize_usage(usage)
        if not any(normalized.values()):
            return {}
        normalized["cost_usd"] = estimate_cost_usd(normalized)
        total = normalize_usage_total(self._config.get("usage_total"))
        total["input"] += normalized["input"]
        total["output"] += normalized["output"]
        total["cached"] += normalized["cached"]
        total["turns"] += 1
        total["cost_usd"] = round(total.get("cost_usd", 0.0) + normalized["cost_usd"], 8)
        self._config = store.save_config({**self._config, "usage_total": total})
        return normalized

    def memory_recent(self) -> dict:
        return {
            "ok": True,
            "current": self._config.get("memory_root", ""),
            "recent": list(self._config.get("recent_memory_roots") or []),
            "memory": self._bank.stats(),
        }

    def memory_switch(self, payload: dict) -> dict:
        """切换记忆库：校验 → 可选初始化 → 保存 → 重建索引。"""
        payload = dict(payload or {})
        root = (payload.get("root") or "").strip().strip('"')
        if not root:
            return {"ok": False, "error": "路径不能为空"}
        if payload.get("create"):
            created = scaffold_memory_bank(root)
            if not created.get("ok"):
                return created
        check = check_memory_root(root)
        if not check.get("ok"):
            return {"ok": False, "error": check.get("error", "这个目录用不了")}
        if check.get("empty") and not payload.get("create"):
            return {
                "ok": False,
                "error": "这个目录里没有笔记。确认要切过去的话，勾选「在这里建一个空白记忆库」。",
                "empty": True,
            }
        recent = [root] + [r for r in (self._config.get("recent_memory_roots") or []) if r != root]
        self._config = store.save_config(
            {**self._config, "memory_root": root, "recent_memory_roots": recent[:5]}
        )
        self._bank = MemoryBank(root)
        return {
            "ok": True,
            "config": public_config(self._config),
            "memory": self._bank.stats(),
            "root": root,
        }

    # ---------- 供应商 ----------

    def provider_presets(self) -> dict:
        return {"ok": True, "presets": store.PROVIDER_PRESETS}

    def provider_list(self) -> dict:
        return {
            "ok": True,
            "active": self._config.get("active_provider", ""),
            "providers": public_config(self._config).get("providers", []),
        }

    def provider_save(self, payload: dict) -> dict:
        payload = dict(payload or {})
        providers = [dict(p) for p in (self._config.get("providers") or [])]
        provider_id = (payload.get("id") or "").strip()
        target = None
        if provider_id:
            for item in providers:
                if item.get("id") == provider_id:
                    target = item
                    break
        if target is None:
            target = {
                "id": f"p{int(time.time() * 1000) % 1000000}",
                "name": "",
                "base_url": "",
                "api_key": "",
                "model": "",
            }
            providers.append(target)
        target["name"] = (payload.get("name") or target.get("name") or "未命名供应商").strip()
        target["base_url"] = (payload.get("base_url") or "").strip()
        target["model"] = (payload.get("model") or "").strip()
        new_key = (payload.get("api_key") or "").strip()
        if new_key:
            target["api_key"] = new_key
        self._config = store.save_config({**self._config, "providers": providers})
        self._config = store.save_config(store.set_active_provider(self._config, self._config["active_provider"]))
        return {"ok": True, "config": public_config(self._config), "id": target["id"]}

    def provider_delete(self, provider_id: str) -> dict:
        providers = [p for p in (self._config.get("providers") or []) if p.get("id") != provider_id]
        if not providers:
            return {"ok": False, "error": "至少要留一个供应商"}
        config = {**self._config, "providers": providers}
        if config.get("active_provider") == provider_id:
            config["active_provider"] = providers[0]["id"]
        self._config = store.save_config(store.set_active_provider(config, config["active_provider"]))
        return {"ok": True, "config": public_config(self._config)}

    def provider_activate(self, provider_id: str) -> dict:
        self._config = store.save_config(store.set_active_provider(self._config, provider_id))
        provider = resolve_provider(self._config)
        return {
            "ok": True,
            "config": public_config(self._config),
            "provider": {
                "id": provider.get("id"),
                "name": provider.get("name"),
                "model": provider.get("model"),
                "has_key": bool((provider.get("api_key") or "").strip()),
            },
        }

    def provider_models(self, provider_id: str) -> dict:
        provider = self._provider_by_id(provider_id)
        if provider is None:
            return {"ok": False, "error": "供应商不存在"}
        return list_models(provider)

    def provider_test(self, provider_id: str) -> dict:
        provider = self._provider_by_id(provider_id)
        if provider is None:
            return {"ok": False, "error": "供应商不存在"}
        return test_provider(provider)

    def _provider_by_id(self, provider_id: str) -> dict | None:
        for item in self._config.get("providers") or []:
            if item.get("id") == provider_id:
                return item
        return None

    # ---------- 规则 ----------

    def rules_get(self) -> dict:
        return {
            "ok": True,
            "content": rules_module.load_rules(),
            "path": str(rules_module.rules_path()),
            "bank_rules": rules_module.bank_rules(self._config.get("memory_root", "")),
            "template": rules_module.DEFAULT_TEMPLATE,
        }

    def rules_save(self, text: str) -> dict:
        return rules_module.save_rules(text or "")

    def rules_reset(self) -> dict:
        return rules_module.reset_rules()

    # ---------- 技能 ----------

    def skills_list(self, force: bool = False) -> dict:
        items = skills_module.list_skills(force=bool(force))
        return {"ok": True, "count": len(items), "skills": items}

    def skills_read(self, name: str) -> dict:
        return skills_module.read_skill(name)

    # ---------- 程序员模式 ----------

    def prog_available(self) -> dict:
        if self._codex_info is None:
            path = find_codex(self._config.get("codex_path", ""))
            self._codex_info = {
                "path": path,
                "version": codex_version(path) if path else "",
            }
        info = self._codex_info
        return {
            "ok": bool(info["path"]),
            "path": info["path"],
            "version": info["version"],
            "sandboxes": DEFAULT_SANDBOXES(),
            "default_sandbox": self._config.get("default_sandbox", DEFAULT_SANDBOX),
            "recent_workspaces": list(self._config.get("recent_workspaces") or []),
            "notice_shown": bool(self._config.get("prog_notice_shown")),
            "error": "" if info["path"] else "本机没找到 codex.exe，程序员模式用不了（聊天模式不受影响）",
        }

    def prog_notice_seen(self) -> dict:
        self._config = store.save_config({**self._config, "prog_notice_shown": True})
        return {"ok": True}

    def prog_pick_workspace(self) -> dict:
        return self.pick_folder("")

    def prog_configure(self, payload: dict) -> dict:
        payload = dict(payload or {})
        session_id = payload.get("session_id") or ""
        fields = {}
        if "workspace" in payload:
            workspace = (payload.get("workspace") or "").strip()
            if workspace and not Path(workspace).is_dir():
                return {"ok": False, "error": f"目录不存在：{workspace}"}
            fields["workspace"] = workspace
            if workspace:
                recent = [workspace] + [
                    w for w in (self._config.get("recent_workspaces") or []) if w != workspace
                ]
                self._config = store.save_config({**self._config, "recent_workspaces": recent[:5]})
        if "sandbox" in payload:
            sandbox = payload.get("sandbox") or DEFAULT_SANDBOX
            if sandbox not in SANDBOX_KEYS():
                return {"ok": False, "error": "权限档位不合法"}
            fields["sandbox"] = sandbox
            self._config = store.save_config({**self._config, "default_sandbox": sandbox})
        if "extra_dirs" in payload:
            fields["extra_dirs"] = [d for d in (payload.get("extra_dirs") or []) if d]
        session = store.update_session(session_id, **fields) if session_id else None
        return {
            "ok": True,
            "session": session,
            "config": public_config(self._config),
            "recent_workspaces": list(self._config.get("recent_workspaces") or []),
        }

    def prog_start(self, payload: dict) -> dict:
        payload = dict(payload or {})
        text = (payload.get("text") or "").strip()
        session_id = payload.get("session_id") or ""
        if not text:
            return {"ok": False, "error": "消息为空"}
        session = store.load_session(session_id)
        if session is None:
            return {"ok": False, "error": "会话不存在"}
        path = find_codex(self._config.get("codex_path", ""))
        if not path:
            return {"ok": False, "error": "本机没找到 codex.exe"}
        workspace = (payload.get("workspace") or session.get("workspace") or "").strip()
        if not workspace:
            return {"ok": False, "error": "先选一个工作目录"}
        with self._prog_lock:
            if session_id in self._prog_jobs:
                return {"ok": False, "error": "这个会话还在跑，先停止或等它结束"}
            sandbox = (
                payload.get("sandbox")
                or session.get("sandbox")
                or self._config.get("default_sandbox", DEFAULT_SANDBOX)
            )
            extra_dirs = payload.get("extra_dirs") or session.get("extra_dirs") or []
            mode = session.get("mode") or "prog"
            if mode not in ("plan", "prog"):
                mode = "prog"
            job = CodexJob(
                codex_path=path,
                prompt=text,
                workspace=workspace,
                sandbox=sandbox,
                plan_mode=(mode == "plan"),
                extra_dirs=extra_dirs,
                thread_id=session.get("thread_id", ""),
                on_event=lambda event: self._emit(
                    "onProgEvent", {"session_id": session_id, "event": event}
                ),
            )
            self._prog_jobs[session_id] = job
        threading.Thread(target=self._run_prog, args=(session_id, text, job), daemon=True).start()
        return {"ok": True}

    def prog_stop(self, session_id: str) -> dict:
        with self._prog_lock:
            job = self._prog_jobs.get(session_id)
        if job is None:
            return {"ok": False, "error": "这个会话没有在跑的任务"}
        job.stop()
        return {"ok": True}

    def _run_prog(self, session_id: str, text: str, job: CodexJob) -> None:
        try:
            run_mode = "plan" if job.plan_mode else "prog"
            store.append_message(session_id, "user", text, mode=run_mode)
            self._emit("onSession", {"session": store.load_session(session_id), "list": store.list_sessions()})
            result = job.run()
            events = result.get("events") or []
            usage = result.get("usage") or {}
            if result.get("ok"):
                stored_usage = self.record_usage(usage)
                store.append_message(
                    session_id,
                    "assistant",
                    result.get("message", ""),
                    events=events,
                    usage=stored_usage or None,
                    mode=run_mode,
                )
                store.update_session(session_id, thread_id=result.get("thread_id", ""))
                self._emit(
                    "onProgDone",
                    {
                        "session_id": session_id,
                        "message": result.get("message", ""),
                        "usage": stored_usage,
                        "total": normalize_usage_total(self._config.get("usage_total")),
                        "session": store.load_session(session_id),
                        "list": store.list_sessions(),
                    },
                )
            else:
                stored_usage = self.record_usage(usage)
                store.append_message(
                    session_id,
                    "assistant",
                    result.get("error", "执行失败"),
                    events=events,
                    usage=stored_usage or None,
                    mode=run_mode,
                )
                if result.get("thread_id"):
                    store.update_session(session_id, thread_id=result["thread_id"])
                self._emit(
                    "onProgError",
                    {
                        "session_id": session_id,
                        "message": result.get("error", "执行失败"),
                        "stopped": bool(result.get("stopped")),
                        "session": store.load_session(session_id),
                        "list": store.list_sessions(),
                    },
                )
        except Exception as exc:
            self._emit("onProgError", {"session_id": session_id, "message": f"出错了：{exc}"})
        finally:
            with self._prog_lock:
                self._prog_jobs.pop(session_id, None)

    # ---------- 对话 ----------

    def chat_send(self, payload: dict) -> dict:
        payload = dict(payload or {})
        text = (payload.get("text") or "").strip()
        session_id = payload.get("session_id") or ""
        if not text:
            return {"ok": False, "error": "消息为空"}
        if not session_id:
            return {"ok": False, "error": "没有选中的会话"}
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "上一条还在回答中，先等它说完"}
        thread = threading.Thread(
            target=self._run_chat,
            args=(
                text,
                session_id,
                payload.get("refs") or [],
                bool(payload.get("force_retrieve")),
                payload.get("skills") or [],
            ),
            daemon=True,
        )
        thread.start()
        return {"ok": True}

    def _run_chat(
        self,
        text: str,
        session_id: str,
        explicit_refs: list[str],
        force_retrieve: bool = False,
        skill_names: list[str] | None = None,
    ) -> None:
        try:
            session = store.append_message(session_id, "user", text, refs=explicit_refs, mode="chat")
            if session is None:
                raise LLMError("会话不存在，可能被删掉了")
            self._emit(
                "onSession",
                {"session": session, "list": store.list_sessions()},
            )

            refs = self._bank.read_many(explicit_refs)
            if not refs:
                refs = self._bank.retrieve(
                    text,
                    k=int(self._config.get("max_context_notes", 4) or 4),
                    force=force_retrieve,
                )
            self._emit("onRefs", {"refs": [{"path": r["path"], "title": r["title"]} for r in refs]})

            system_prompt = build_system_prompt(
                self._config,
                self._bank,
                refs,
                rules=rules_module.compose_rules(self._config),
                skills_block=skills_module.skill_prompt_block(skill_names or []),
            )
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in session["messages"][-HISTORY_LIMIT:]
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
            messages = [{"role": "system", "content": system_prompt}, *history]

            self._emit("onStart", {"session_id": session_id})

            def on_delta(piece: str) -> None:
                self._emit("onDelta", {"text": piece})

            captured: dict = {}

            def on_usage(usage: dict) -> None:
                captured.update(usage)

            answer = stream_chat(self._config, messages, on_delta, on_usage=on_usage)
            stored_usage = self.record_usage(captured)
            store.append_message(
                session_id, "assistant", answer, usage=stored_usage or None, mode="chat"
            )
            emotion, without_emotion = split_emotion(answer)
            self._emit(
                "onDone",
                {
                    "content": answer,
                    "emotion": emotion,
                    "clean": strip_memo_writes(without_emotion),
                    "writes": extract_memo_writes(answer),
                    "usage": stored_usage,
                    "total": normalize_usage_total(self._config.get("usage_total")),
                    "session": store.load_session(session_id),
                    "list": store.list_sessions(),
                },
            )
        except LLMError as exc:
            self._emit("onError", {"message": str(exc)})
        except Exception as exc:  # 兜底，不让窗口白屏
            self._emit("onError", {"message": f"出错了：{exc}"})
        finally:
            try:
                self._lock.release()
            except RuntimeError:
                pass


def public_config(config: dict) -> dict:
    out = dict(config)
    out["has_key"] = bool((out.get("api_key") or "").strip())
    out["api_key_masked"] = mask_key(out.get("api_key", ""))
    out.pop("api_key", None)
    safe = []
    for provider in out.get("providers") or []:
        item = dict(provider)
        item["has_key"] = bool((item.get("api_key") or "").strip())
        item["api_key_masked"] = mask_key(item.get("api_key", ""))
        item.pop("api_key", None)
        safe.append(item)
    out["providers"] = safe
    return out


def mask_key(key: str) -> str:
    key = (key or "").strip()
    if len(key) < 10:
        return ""
    return f"{key[:6]}…{key[-4:]}"


def price_is_peak(when: "datetime.datetime | None" = None) -> bool:
    """DeepSeek 高峰时段：UTC 周一~周五 01:00-04:00 与 06:00-10:00。"""
    now = when or datetime.datetime.now(datetime.timezone.utc)
    if now.weekday() >= 5:
        return False
    return (1 <= now.hour < 4) or (6 <= now.hour < 10)


def estimate_cost_usd(usage: dict, when=None) -> float:
    """按 DeepSeek Flash 现价估算这一轮的美元花费（缓存命中价是未命中的 1/50）。"""
    cached = min(int(usage.get("cached") or 0), int(usage.get("input") or 0))
    miss = max(0, int(usage.get("input") or 0) - cached)
    output = int(usage.get("output") or 0)
    if price_is_peak(when):
        price_hit, price_miss, price_out = 0.006, 0.30, 1.2
    else:
        price_hit, price_miss, price_out = 0.003, 0.15, 0.6
    return miss / 1e6 * price_miss + cached / 1e6 * price_hit + output / 1e6 * price_out


def normalize_usage(raw: dict) -> dict:
    """把不同供应商的用量字段统一成 input / cached / output。"""
    raw = raw or {}
    return {
        "input": int(raw.get("input_tokens") or raw.get("prompt_tokens") or 0),
        "cached": int(raw.get("cached_input_tokens") or raw.get("prompt_cache_hit_tokens") or 0),
        "output": int(raw.get("output_tokens") or raw.get("completion_tokens") or 0),
    }


def normalize_usage_total(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "input": int(raw.get("input", 0)),
        "output": int(raw.get("output", 0)),
        "cached": int(raw.get("cached", 0)),
        "turns": int(raw.get("turns", 0)),
        "cost_usd": float(raw.get("cost_usd", 0) or 0),
    }


def tag_system() -> dict:
    from core.memory import TAG_SYSTEM

    return TAG_SYSTEM


def DEFAULT_SANDBOXES() -> list[dict]:
    from core.codex_runner import SANDBOX_MODES

    return [
        {"key": key, "label": label}
        for key, label in SANDBOX_MODES.items()
    ]


def SANDBOX_KEYS() -> tuple[str, ...]:
    from core.codex_runner import SANDBOX_MODES

    return tuple(SANDBOX_MODES)


def on_loaded(window: webview.Window) -> None:
    # 这里不要直接 evaluate_js：此刻 WebView2 控制器还没在 UI 线程就绪，
    # 抛出的异常会被 pywebview 的错误处理器放大成界面卡死。页面自己会调 onReady。
    api._window = window
    # 系统标题栏默认是浅色的，和深紫界面撞色，改成主题色
    threading.Thread(
        target=apply_dark_titlebar, args=(window.title,), daemon=True
    ).start()


def main() -> None:
    global api
    store.ensure_dirs()
    api = Api()
    index = ui_dir() / "index.html"
    if not index.exists():
        raise SystemExit(f"找不到界面文件：{index}")
    window = webview.create_window(
        api._config.get("assistant_name") or "AI 小助理",
        html=build_html(),
        js_api=api,
        width=1320,
        height=860,
        min_size=(1020, 660),
        background_color="#0F0A1E",
        text_select=True,
    )
    webview.start(on_loaded, window, debug=False, private_mode=False)


if __name__ == "__main__":
    main()
