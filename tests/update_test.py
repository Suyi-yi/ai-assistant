"""更新功能自检：用本地 HTTP 服务模拟更新源，验证检测、下载与安装脚本生成。

不会真的覆盖安装。用法：python tests/update_test.py
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import updater  # noqa: E402

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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    print("1. 版本比较")
    check("0.3.0 > 0.2.0", updater.is_newer("0.3.0", "0.2.0"))
    check("同版本不算更新", not updater.is_newer("0.2.0", "0.2.0"))
    check("带 v 前缀也能比", updater.is_newer("v1.0.0", "0.9.9"))
    check("两位版本能补零", updater.is_newer("0.2.1", "0.2"))

    print("2. 本地模拟更新源")
    folder = Path(tempfile.mkdtemp(prefix="ai-assistant-update-test-"))
    payload = b"x" * 5000
    (folder / "update.zip").write_bytes(payload)
    port = free_port()
    manifest = {
        "version": "9.9.9",
        "url": f"http://127.0.0.1:{port}/update.zip",
        "notes": "## 测试版本\n- 加了点什么",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(folder),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    time.sleep(1.5)
    try:
        source = f"http://127.0.0.1:{port}/manifest.json"
        result = updater.check_update("0.2.0", source)
        check("能读到更新源", result.get("ok"), str(result))
        check("识别出新版本", result.get("has_update") is True, str(result))
        check("版本号解析正确", result.get("version") == "9.9.9")
        check("带回了更新说明", "测试版本" in (result.get("notes") or ""))

        same = updater.check_update("9.9.9", source)
        check("同版本不会提示更新", same.get("has_update") is False)

        downloaded = updater.download_zip(manifest["url"])
        check("能下载更新包", downloaded.get("ok"), str(downloaded))
        check("下载内容完整", downloaded.get("bytes") == len(payload), str(downloaded))
        if downloaded.get("ok"):
            check("落盘文件大小一致", Path(downloaded["path"]).stat().st_size == len(payload))

        bad = updater.check_update("0.2.0", f"http://127.0.0.1:{port}/nope.json")
        check("更新源 404 时报错不崩", bad.get("ok") is False, str(bad))
        broken = folder / "broken.json"
        broken.write_text("这不是 json", encoding="utf-8")
        bad2 = updater.check_update("0.2.0", f"http://127.0.0.1:{port}/broken.json")
        check("更新源不是 JSON 时报错", bad2.get("ok") is False, str(bad2))
    finally:
        server.terminate()

    print("3. 安装脚本")
    script = updater.UPDATER_SCRIPT
    check("会等待主程序退出", "Wait-Process -Id $WaitPid" in script)
    check("会解压覆盖", "Expand-Archive" in script and "-Force" in script)
    check("会重新打开程序", "Start-Process -FilePath $ExePath" in script)
    apply_missing = updater.apply_update(str(folder / "没有这个.zip"), str(folder), str(folder / "x.exe"), 1)
    check("更新包缺失时报错", apply_missing.get("ok") is False, str(apply_missing))
    apply_no_exe = updater.apply_update(str(folder / "update.zip"), str(folder), str(folder / "没有.exe"), 1)
    check("主程序缺失时报错", apply_no_exe.get("ok") is False, str(apply_no_exe))

    print(f"\n通过 {passed} 项，失败 {failed} 项")
    shutil.rmtree(folder, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
