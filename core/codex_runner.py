"""程序员模式：调用本机 codex.exe 干活，解析它的 JSONL 事件流。

不重造 agent，直接用 Codex 本体，因此天然有真实文件权限、命令执行和沙箱。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

SANDBOX_MODES = {
    "read-only": "只读",
    "workspace-write": "可改工作目录",
    "danger-full-access": "完全访问",
}
MODE_LABELS = {"chat": "聊天", "plan": "计划", "prog": "天才程序员"}

PLAN_PREFIX = """你处在「计划模式」，这一轮只做调研和出方案：

1. 绝对不要修改任何文件，不要执行任何会改变系统状态的命令（写入、安装、删除、git 提交都不行）。沙箱是只读的，越界的写入会失败，不要尝试绕过。
2. 先把相关文件读清楚，再给方案。方案要能直接照着执行，包含：目标、涉及哪些文件、分步骤做什么、风险与回滚办法、怎么验证做完了。
3. 只把方案写在回答里，不要直接动手改。用户会说「按计划执行」再切到执行模式。
4. 方案要具体到文件名和命令，不要写「酌情处理」这类话。
"""

DEFAULT_SANDBOX = "workspace-write"
CREATE_NO_WINDOW = 0x08000000
OUTPUT_KEEP = 4000


def find_codex(configured: str = "") -> str:
    """找 codex.exe：先看用户指定的，再看 PATH，最后翻桌面端自带的位置。"""
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("codex")
    if found:
        return found
    base = Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin"
    if base.is_dir():
        for candidate in sorted(base.glob("*/codex.exe"), reverse=True):
            return str(candidate)
    return ""


def codex_version(path: str) -> str:
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
        return (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class CodexJob:
    """一次程序员模式的执行。run() 是阻塞的，由调用方放进线程。"""

    def __init__(
        self,
        codex_path: str,
        prompt: str,
        workspace: str,
        sandbox: str = DEFAULT_SANDBOX,
        plan_mode: bool = False,
        extra_dirs: list[str] | None = None,
        thread_id: str = "",
        on_event=None,
    ):
        self.codex_path = codex_path
        self.prompt = prompt
        self.workspace = str(workspace or "")
        self.plan_mode = bool(plan_mode)
        # 计划模式强制只读，不论界面上选的是什么档
        self.sandbox = "read-only" if self.plan_mode else (sandbox if sandbox in SANDBOX_MODES else DEFAULT_SANDBOX)
        self._raw_sandbox = sandbox
        if self.plan_mode:
            prompt = PLAN_PREFIX + "\n用户的任务：" + prompt
        self.prompt = prompt
        self.extra_dirs = [d for d in (extra_dirs or []) if d]
        self.thread_id = thread_id or ""
        self.on_event = on_event
        self.process: subprocess.Popen | None = None
        self._stopped = False
        self._lock = threading.Lock()
        self.events: list[dict] = []
        self.usage: dict = {}
        self.message = ""
        self.thread_out = self.thread_id
        self.noise: list[str] = []

    # ---------- 对外 ----------

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            process = self.process
        if process is None:
            return
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass

    def _emit(self, event: dict) -> None:
        self.events.append(event)
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def _build_command(self) -> list[str]:
        # 必须用 unelevated：elevated 模式下沙箱以另一个用户身份运行，
        # 它建出来的文件 ACL 里没有你本人，结果是「活干完了，你自己打不开」。
        sandbox_user = ["-c", 'windows.sandbox="unelevated"']
        if self.thread_id:
            command = [
                self.codex_path, "exec", "resume", self.thread_id,
                "--json", "--skip-git-repo-check",
                *sandbox_user,
                "-c", f'sandbox_mode="{self.sandbox}"',
            ]
        else:
            command = [
                self.codex_path, "exec", "--json", "--skip-git-repo-check",
                *sandbox_user,
                "-s", self.sandbox,
            ]
            if self.workspace and Path(self.workspace).is_dir():
                command += ["-C", self.workspace]
        for directory in self.extra_dirs:
            command += ["--add-dir", directory]
        command.append("-")  # 提示词走 stdin，避开 Windows 命令行长度和引号问题
        return command

    # ---------- 主流程 ----------

    def run(self) -> dict:
        if not self.codex_path:
            return {"ok": False, "error": "没找到 codex.exe，程序员模式用不了"}
        if not self.thread_id and not (self.workspace and Path(self.workspace).is_dir()):
            return {"ok": False, "error": "请先选一个存在的工作目录"}

        command = self._build_command()
        self._emit({"kind": "status", "text": "正在启动 Codex…（首次约 10~20 秒）"})
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.workspace or None,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            return {"ok": False, "error": f"启动 Codex 失败：{exc}"}

        try:
            if self.process.stdin:
                self.process.stdin.write(self.prompt)
                self.process.stdin.close()
        except OSError:
            pass

        if self.process.stdout:
            for line in self.process.stdout:
                if self._stopped:
                    break
                self._handle_line(line.rstrip("\n"))
        self.process.wait()
        code = self.process.returncode

        if self._stopped:
            self._emit({"kind": "error", "text": "已被你停止"})
            return {
                "ok": False,
                "stopped": True,
                "error": "已停止",
                "thread_id": self.thread_out,
                "events": self.events,
            }
        if code != 0:
            tail = "\n".join(self.noise[-6:])[-600:]
            self._emit({"kind": "error", "text": f"Codex 退出码 {code}。{tail}"})
            return {
                "ok": False,
                "error": f"Codex 退出码 {code}",
                "thread_id": self.thread_out,
                "events": self.events,
            }
        return {
            "ok": True,
            "message": self.message,
            "thread_id": self.thread_out,
            "usage": self.usage,
            "events": self.events,
        }

    def _handle_line(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            return
        if not stripped.startswith("{"):
            # 启动期的 WARN / 日志噪音，只留最后几条给报错用
            if "WARN" not in stripped and "INFO" not in stripped:
                self.noise.append(stripped)
            return
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            return
        kind = event.get("type")
        if kind == "thread.started":
            self.thread_out = event.get("thread_id") or self.thread_out
            self._emit({"kind": "status", "text": "正在思考…"})
        elif kind == "item.started":
            self._on_item(event.get("item") or {}, False)
        elif kind == "item.completed":
            self._on_item(event.get("item") or {}, True)
        elif kind == "turn.completed":
            self.usage = event.get("usage") or {}
            self._emit({"kind": "usage", "usage": self.usage})
        elif kind == "error":
            self._emit({"kind": "error", "text": str(event.get("message") or event)[:400]})

    def _on_item(self, item: dict, completed: bool) -> None:
        item_type = item.get("type")
        if item_type == "command_execution":
            if not completed:
                # 开始事件不单独渲染，避免同一条命令出现两份；执行中的提示走状态卡
                return
            output = item.get("aggregated_output") or ""
            self._emit(
                {
                    "kind": "command",
                    "id": item.get("id", ""),
                    "command": (item.get("command") or "")[:2000],
                    "output": output[-OUTPUT_KEEP:],
                    "exit_code": item.get("exit_code"),
                    "status": item.get("status") or ("completed" if completed else "in_progress"),
                }
            )
        elif item_type == "agent_message":
            self.message = item.get("text") or self.message
            if completed:
                self._emit({"kind": "message", "text": self.message})
        elif item_type in ("file_change", "patch_apply", "reasoning"):
            text = item.get("text") or json.dumps(item, ensure_ascii=False)
            self._emit({"kind": "note", "label": item_type, "text": text[:1500]})
