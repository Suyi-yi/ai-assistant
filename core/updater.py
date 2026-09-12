"""检测更新并就地覆盖安装。

更新源支持两种写法：
  1) 一个 JSON 地址（自己放 Gitee / 网盘 / 任意静态空间都行）：
     {"version": "0.3.0", "url": "https://.../AI小助理-v0.3.0.zip", "notes": "改了什么"}
  2) GitHub 仓库 owner/repo（读 Releases 最新版）
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

CREATE_NO_WINDOW = 0x08000000


def parse_version(text: str) -> tuple:
    parts = []
    for chunk in str(text or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def fetch_manifest(source: str, timeout: int = 20, retries: int = 3) -> dict:
    """国内访问 raw.githubusercontent.com 偶尔会抽风，失败就退避重试几次。"""
    last_error = ""
    for attempt in range(max(1, retries)):
        result = _fetch_once(source, timeout)
        if result.get("ok"):
            return result
        last_error = result.get("error", "")
        # 只有网络类错误才重试；404、内容不合法这类重试也没用
        if "连不上" not in last_error:
            return result
        if attempt + 1 < retries:
            time.sleep(1.5 * (attempt + 1))
    return {"ok": False, "error": last_error}


def _fetch_once(source: str, timeout: int = 20) -> dict:
    source = (source or "").strip()
    if not source:
        return {"ok": False, "error": "还没填更新源"}
    try:
        if "/" in source and not source.lower().startswith("http"):
            owner, _, repo = source.partition("/")
            response = requests.get(
                f"https://api.github.com/repos/{owner.strip()}/{repo.strip()}/releases/latest",
                timeout=timeout,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "ai-assistant-updater"},
            )
            if response.status_code == 404:
                return {"ok": False, "error": "找不到这个仓库或还没有 Release"}
            if response.status_code != 200:
                return {"ok": False, "error": f"GitHub 返回 {response.status_code}"}
            data = response.json()
            assets = data.get("assets") or []
            zip_url = ""
            for asset in assets:
                name = (asset.get("name") or "").lower()
                if name.endswith(".zip"):
                    zip_url = asset.get("browser_download_url") or ""
                    break
            if not zip_url:
                return {"ok": False, "error": "最新 Release 里没有 zip 附件"}
            return {
                "ok": True,
                "version": str(data.get("tag_name") or data.get("name") or "").lstrip("vV"),
                "url": zip_url,
                "notes": (data.get("body") or "")[:2000],
                "page": data.get("html_url") or "",
            }
        response = requests.get(source, timeout=timeout, headers={"User-Agent": "ai-assistant-updater"})
        if response.status_code != 200:
            return {"ok": False, "error": f"更新源返回 {response.status_code}"}
        data = response.json()
        if not data.get("version") or not data.get("url"):
            return {"ok": False, "error": "更新源 JSON 里缺少 version 或 url"}
        return {
            "ok": True,
            "version": str(data["version"]),
            "url": str(data["url"]),
            "notes": str(data.get("notes") or "")[:2000],
            "page": str(data.get("page") or ""),
        }
    except requests.RequestException as exc:
        return {"ok": False, "error": f"连不上更新源：{exc}"}
    except ValueError:
        return {"ok": False, "error": "更新源返回的不是合法 JSON"}


def expand_sources(source: str) -> list[str]:
    """把用户填的更新源展开成多个可尝试地址。

    GitHub 的 raw 地址在国内偶尔连不上，这里自动补两个 jsDelivr 镜像做兜底
    （jsDelivr 会把公开仓库的内容镜像一份，不需要额外注册、不用改仓库）。
    用户也可以自己填多个地址，用换行、逗号或空格分隔。
    """
    raw = (source or "").strip()
    items = [item for item in re.split(r"[\s,;]+", raw) if item]
    out: list[str] = []
    for item in items:
        out.append(item)
        if item.lower().startswith("http"):
            match = re.search(r"raw\.githubusercontent\.com/([\w.\-]+)/([\w.\-]+)/([\w.\-]+)/(.+)", item)
            if match:
                owner, repo, branch, path = match.groups()
                out.append(f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}")
                out.append(f"https://fastly.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}")
        else:
            match = re.fullmatch(r"([\w.\-]+)/([\w.\-]+)", item)
            if match:
                owner, repo = match.groups()
                out.append(f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@master/releases/manifest.json")
                out.append(f"https://fastly.jsdelivr.net/gh/{owner}/{repo}@master/releases/manifest.json")
    seen: set[str] = set()
    unique: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def check_update(current: str, source: str) -> dict:
    """多个更新源并行试，谁先成功用谁。

    顺序试会很慢（一个源超时要等十几秒才轮到下一个），并行之后即使是抽风的
    镜像也不会拖慢整体检测。
    """
    candidates = expand_sources(source)
    if not candidates:
        return {"ok": False, "error": "还没填更新源"}
    errors: list[str] = []
    # 这里不能用 with：那会在退出时等所有任务跑完，等于退化成"按最慢的那个算"。
    # 谁先成功就立刻取消剩下的，直接返回。
    pool = ThreadPoolExecutor(max_workers=min(4, len(candidates)))
    try:
        # 有多个源兜底，就不必在单条上死等：超时 6 秒、只试一次
        futures = {pool.submit(fetch_manifest, item, 6, 1): item for item in candidates}
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                manifest = future.result()
            except Exception as exc:  # 线程里出任何问题都当这条源失败
                errors.append(f"{candidate}: {exc}")
                continue
            if manifest.get("ok"):
                manifest["current"] = current
                manifest["has_update"] = is_newer(manifest["version"], current)
                manifest["source_used"] = candidate
                pool.shutdown(wait=False, cancel_futures=True)
                return manifest
            errors.append(f"{candidate}: {manifest.get('error', '')}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return {"ok": False, "error": errors[0] if errors else "所有更新源都不可用", "tried": candidates}


def download_zip(url: str, timeout: int = 300) -> dict:
    """把新版本 zip 下到临时目录（放在系统盘外，避免占 C 盘）。"""
    try:
        folder = Path(tempfile.mkdtemp(prefix="ai-assistant-update-"))
        target = folder / "update.zip"
        with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": "ai-assistant-updater"}) as response:
            if response.status_code != 200:
                return {"ok": False, "error": f"下载失败：HTTP {response.status_code}"}
            total = 0
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=262144):
                    if chunk:
                        handle.write(chunk)
                        total += len(chunk)
        if total < 1024:
            return {"ok": False, "error": "下载下来的文件太小，可能不是安装包"}
        return {"ok": True, "path": str(target), "bytes": total}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"下载失败：{exc}"}
    except OSError as exc:
        return {"ok": False, "error": f"写临时文件失败：{exc}"}


UPDATER_SCRIPT = """param(
  [int]$WaitPid,
  [string]$Zip,
  [string]$TargetDir,
  [string]$ExePath
)
$ErrorActionPreference = 'Stop'
try { Wait-Process -Id $WaitPid -Timeout 180 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 1200
Expand-Archive -LiteralPath $Zip -DestinationPath $TargetDir -Force
Start-Sleep -Milliseconds 500
Start-Process -FilePath $ExePath
"""


def apply_update(zip_path: str, target_dir: str, exe_path: str, wait_pid: int) -> dict:
    """写一个一次性脚本：等本程序退出 → 解压覆盖 → 重新打开。

    只覆盖程序文件；data/ 不在更新包里，所以设置、密钥、聊天记录、备份都不会被动。
    """
    zip_file = Path(zip_path)
    target = Path(target_dir)
    exe = Path(exe_path)
    if not zip_file.is_file():
        return {"ok": False, "error": "更新包不见了，请重新下载"}
    if not target.is_dir():
        return {"ok": False, "error": f"安装目录不存在：{target}"}
    if not exe.is_file():
        return {"ok": False, "error": f"找不到主程序：{exe}"}
    script = Path(tempfile.mkdtemp(prefix="ai-assistant-apply-")) / "apply.ps1"
    script.write_text(UPDATER_SCRIPT, encoding="utf-8-sig")
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-WaitPid",
                str(wait_pid),
                "-Zip",
                str(zip_file),
                "-TargetDir",
                str(target),
                "-ExePath",
                str(exe),
            ],
            creationflags=CREATE_NO_WINDOW,
            close_fds=True,
        )
    except OSError as exc:
        return {"ok": False, "error": f"启动更新脚本失败：{exc}"}
    return {"ok": True, "script": str(script)}
