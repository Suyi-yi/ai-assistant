"""本地持久化：配置与会话历史。

数据全部放在项目（或 exe）同级的 data/ 目录，便于自己检查和备份。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()


def _data_dir() -> Path:
    """优先把数据放在程序旁边；程序目录不可写时退到用户目录。

    这样解压到桌面能用，装在只读目录（例如 Program Files）也不会崩。
    """
    candidate = BASE_DIR / "data"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return candidate
    except OSError:
        fallback = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AI小助理"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


DATA_DIR = _data_dir()
SESSIONS_DIR = DATA_DIR / "sessions"
BACKUPS_DIR = DATA_DIR / "backups"
TRASH_DIR = BACKUPS_DIR / "trash"

DEFAULT_CONFIG = {
    "assistant_name": "小助理",
    "persona": (
        "温暖、有情绪、有分寸。会为你的进展高兴，会在你熬太晚时念叨两句，"
        "会用「诶」「呀」「～」这类口语词；先说结论再补细节，不说客套话、不无脑恭维。"
        "像认识很久的朋友，不像客服。"
    ),
    "persona_preset": "贴心",
    "memory_root": "D:\\CodexWENJIAN\\Codex\\记忆库",
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-chat",
    "temperature": 0.7,
    "max_context_notes": 4,
    "providers": [],
    "active_provider": "",
    "codex_path": "",
    "default_sandbox": "workspace-write",
    "prog_notice_shown": False,
    "recent_workspaces": [],
    "recent_memory_roots": [],
    "usage_total": {"input": 0, "output": 0, "cached": 0, "turns": 0},
    "update_source": "",
    "update_checked_at": 0,
}

DEFAULT_PROVIDER = {
    "id": "deepseek",
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
}

# 预设只是帮用户少打字，每一项都能改
PROVIDER_PRESETS = [
    {"name": "DeepSeek 官方", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    {"name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    {"name": "月之暗面 Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    {"name": "阿里通义", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat"},
    {"name": "本地 Ollama", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b"},
    {"name": "中转站 / 自建", "base_url": "", "model": ""},
]


def ensure_dirs() -> None:
    for d in (DATA_DIR, SESSIONS_DIR, BACKUPS_DIR, TRASH_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _config_path() -> Path:
    return DATA_DIR / "config.json"


def load_config() -> dict:
    ensure_dirs()
    cfg = dict(DEFAULT_CONFIG)
    path = _config_path()
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
    if not cfg.get("api_key"):
        imported = import_key_from_codex()
        if imported:
            cfg["api_key"] = imported
            save_config(cfg)
    migrated = migrate_providers(cfg)
    if migrated != cfg:
        save_config(migrated)
    return migrated


def migrate_providers(cfg: dict) -> dict:
    """老配置只有一个 base_url/key/model，升级成一条供应商记录。"""
    out = dict(cfg)
    providers = [dict(p) for p in (out.get("providers") or []) if isinstance(p, dict)]
    if not providers:
        providers = [
            {
                **DEFAULT_PROVIDER,
                "base_url": out.get("base_url") or DEFAULT_PROVIDER["base_url"],
                "api_key": out.get("api_key") or "",
                "model": out.get("model") or DEFAULT_PROVIDER["model"],
            }
        ]
    for index, provider in enumerate(providers):
        provider.setdefault("id", f"p{index + 1}")
        provider.setdefault("name", f"供应商 {index + 1}")
        provider.setdefault("base_url", DEFAULT_PROVIDER["base_url"])
        provider.setdefault("api_key", "")
        provider.setdefault("model", DEFAULT_PROVIDER["model"])
    out["providers"] = providers
    active = out.get("active_provider") or providers[0]["id"]
    if active not in [p["id"] for p in providers]:
        active = providers[0]["id"]
    out["active_provider"] = active
    current = next(p for p in providers if p["id"] == active)
    out["api_key"] = current["api_key"]
    out["base_url"] = current["base_url"]
    out["model"] = current["model"]
    return out


def active_provider(cfg: dict) -> dict:
    providers = cfg.get("providers") or []
    for provider in providers:
        if provider.get("id") == cfg.get("active_provider"):
            return provider
    return providers[0] if providers else dict(DEFAULT_PROVIDER)


def set_active_provider(cfg: dict, provider_id: str) -> dict:
    """切换供应商，并把它的连接信息同步到顶层字段。"""
    out = dict(cfg)
    if provider_id not in [p.get("id") for p in (out.get("providers") or [])]:
        return out
    out["active_provider"] = provider_id
    current = active_provider(out)
    out["api_key"] = current["api_key"]
    out["base_url"] = current["base_url"]
    out["model"] = current["model"]
    return out


def save_config(cfg: dict) -> dict:
    ensure_dirs()
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_CONFIG})
    _config_path().write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return merged


def import_key_from_codex() -> str:
    """从 Codex 的 config.toml 里把自定义供应商的 key 读出来，省去手填。"""
    path = Path.home() / ".codex" / "config.toml"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        key = line.split("#", 1)[0].strip()
        if key.startswith("experimental_bearer_token"):
            _, _, value = key.partition("=")
            return value.strip().strip('"').strip("'")
    return ""


def _session_path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    if not safe:
        raise ValueError("会话 ID 非法")
    return SESSIONS_DIR / f"{safe}.json"


def list_sessions() -> list[dict]:
    ensure_dirs()
    items = []
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items.append(
            {
                "id": data.get("id", path.stem),
                "title": data.get("title") or "新对话",
                "mode": data.get("mode", "chat"),
                "updated": data.get("updated", 0),
                "count": len(data.get("messages", [])),
            }
        )
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def new_session(
    title: str = "新对话",
    mode: str = "chat",
    workspace: str = "",
    sandbox: str = "",
    extra_dirs: list | None = None,
) -> dict:
    ensure_dirs()
    now = time.time()
    session = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "mode": mode,
        "workspace": workspace,
        "sandbox": sandbox,
        "extra_dirs": list(extra_dirs or []),
        "thread_id": "",
        "created": now,
        "updated": now,
        "messages": [],
    }
    write_session(session)
    return session


def update_session(session_id: str, **fields) -> dict | None:
    session = load_session(session_id)
    if session is None:
        return None
    session.update(fields)
    session["updated"] = time.time()
    write_session(session)
    return session


def write_session(session: dict) -> None:
    ensure_dirs()
    path = _session_path(session["id"])
    path.write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_session(session_id: str) -> dict | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def rename_session(session_id: str, title: str) -> dict | None:
    session = load_session(session_id)
    if session is None:
        return None
    session["title"] = title.strip() or "新对话"
    session["updated"] = time.time()
    write_session(session)
    return session


def delete_session(session_id: str) -> bool:
    path = _session_path(session_id)
    if path.exists():
        path.unlink()
        return True
    return False


def append_message(
    session_id: str,
    role: str,
    content: str,
    refs=None,
    events=None,
    usage=None,
    mode=None,
) -> dict | None:
    session = load_session(session_id)
    if session is None:
        return None
    message = {"role": role, "content": content, "ts": time.time()}
    if refs:
        message["refs"] = list(refs)
    if mode:
        message["mode"] = mode
    if events:
        message["events"] = events
    if usage:
        message["usage"] = usage
    session["messages"].append(message)
    session["updated"] = time.time()
    if role == "user" and session.get("title", "新对话") == "新对话":
        session["title"] = content.strip().replace("\n", " ")[:24] or "新对话"
    write_session(session)
    return session


def set_message_content(session_id: str, index: int, content: str) -> dict | None:
    session = load_session(session_id)
    if session is None or not (0 <= index < len(session["messages"])):
        return None
    session["messages"][index]["content"] = content
    session["updated"] = time.time()
    write_session(session)
    return session
