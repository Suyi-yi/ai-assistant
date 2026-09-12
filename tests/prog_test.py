"""程序员模式真实演练：验证权限档真的拦得住、也真的干得了活。

会真实调用 codex.exe，两轮合计约几万 token。用法：python tests/prog_test.py --real
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.codex_runner import CodexJob, find_codex  # noqa: E402

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


def run(prompt: str, workspace: str, sandbox: str, thread_id: str = "") -> dict:
    job = CodexJob(
        codex_path=find_codex(),
        prompt=prompt,
        workspace=workspace,
        sandbox=sandbox,
        thread_id=thread_id,
        on_event=lambda event: print("     ·", event.get("kind"), str(event.get("command", event.get("text", "")))[:70]),
    )
    started = time.time()
    result = job.run()
    result["seconds"] = round(time.time() - started, 1)
    return result


def main() -> None:
    if "--real" not in sys.argv:
        print("这是真实演练，会消耗 token。确认要跑就加 --real：python tests/prog_test.py --real")
        return
    codex = find_codex()
    if not codex:
        print("本机没找到 codex.exe，跳过")
        return

    workspace = tempfile.mkdtemp(prefix="prog-test-")
    (Path(workspace) / "说明.txt").write_text("这个目录用来测试。", encoding="utf-8")
    print(f"工作目录：{workspace}\n")

    print("0. 命令拼装（不花钱）")
    job = CodexJob(codex_path=codex, prompt="x", workspace=workspace, sandbox="workspace-write")
    command = job._build_command()
    check("带 --json", "--json" in command)
    check("带工作目录", "-C" in command)
    check("带权限档", "-s" in command and "workspace-write" in command)
    check("强制 unelevated 沙箱用户", any("unelevated" in part for part in command))
    check("提示词走 stdin", command[-1] == "-")
    resume_job = CodexJob(codex_path=codex, prompt="x", workspace=workspace, sandbox="read-only", thread_id="abc")
    resume_command = resume_job._build_command()
    check("续接用 resume + 会话号", "resume" in resume_command and "abc" in resume_command)
    check("续接也带权限档", any("read-only" in part for part in resume_command))
    check("续接也强制 unelevated", any("unelevated" in part for part in resume_command))

    if "--quick" in sys.argv:
        print(f"\n通过 {passed} 项，失败 {failed} 项")
        shutil.rmtree(workspace, ignore_errors=True)
        sys.exit(1 if failed else 0)

    print("1. 只读档：让它建文件，应该建不出来")
    result = run("在当前目录创建一个文件 rw-test.txt，内容写 hello", workspace, "read-only")
    print(f"   结果：ok={result.get('ok')} 耗时={result.get('seconds')}s usage={result.get('usage')}")
    commands = [e for e in result.get("events", []) if e.get("kind") == "command"]
    check("拿到了命令行事件", len(commands) > 0, f"commands={len(commands)}")
    check("有退出码", any(e.get("exit_code") is not None for e in commands))
    check("只读档没有真的写出文件", not (Path(workspace) / "rw-test.txt").exists())
    check("拿到了 token 用量", (result.get("usage") or {}).get("input_tokens", 0) > 0, str(result.get("usage")))
    thread_id = result.get("thread_id", "")
    check("拿到了 thread_id", bool(thread_id), thread_id)

    print("\n2. 可改目录档：让它建同一个文件，应该建得出来")
    result2 = run("在当前目录创建一个文件 rw-test.txt，内容写 hello", workspace, "workspace-write", thread_id)
    print(f"   结果：ok={result2.get('ok')} 耗时={result2.get('seconds')}s")
    target = Path(workspace) / "rw-test.txt"
    check("可改档真的写出了文件", target.exists(), str(target))
    if target.exists():
        check("文件内容靠谱", "hello" in target.read_text(encoding="utf-8", errors="replace").lower())
    check("全程没崩", result2.get("ok") is True, str(result2.get("error")))

    print(f"\n通过 {passed} 项，失败 {failed} 项")
    shutil.rmtree(workspace, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
