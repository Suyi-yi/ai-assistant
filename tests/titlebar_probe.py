"""诊断标题栏颜色：读 DWM 属性 + 只截本窗口顶部一条。

用法：python tests/titlebar_probe.py
输出：控制台数值 + %TEMP%\\titlebar-strip.png
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
gdi32 = ctypes.windll.gdi32

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find_window(pid: int) -> int:
    found = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback(visit), 0)
    return found[0] if found else 0


def main() -> None:
    exe = Path(__file__).resolve().parent.parent / "dist" / "AI小助理" / "AI小助理.exe"
    if not exe.exists():
        print(f"没找到 exe：{exe}")
        return
    process = subprocess.Popen([str(exe)])
    print(f"已启动，pid={process.pid}")

    # 先盯启动阶段：如果一开始是浅色、过几秒才变深，就是启动闪烁
    for wait in (1.5, 2.0, 2.0, 4.0):
        time.sleep(wait)
        handle = find_window(process.pid)
        if not handle:
            print(f"  t≈{wait}s：还没有窗口")
            continue
        value = ctypes.c_int(0)
        code = dwmapi.DwmGetWindowAttribute(handle, 20, ctypes.byref(value), ctypes.sizeof(value))
        print(f"  t≈{wait}s：深色模式 返回码={code} 值={value.value}")

    hwnd = find_window(process.pid)
    if not hwnd:
        print("没找到窗口句柄")
        process.terminate()
        return
    print(f"窗口句柄：{hwnd}")

    for attr, label in ((20, "深色模式"), (19, "深色模式(旧属性号)")):
        value = ctypes.c_int(0)
        code = dwmapi.DwmGetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))
        print(f"  {label}：返回码={code} 值={value.value}")

    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    print(f"  窗口大小：{width}x{height}")

    user32.SetForegroundWindow(hwnd)
    time.sleep(1.0)

    def grab(tag: str) -> None:
        hdc_ = user32.GetWindowDC(hwnd)
        mem_ = gdi32.CreateCompatibleDC(hdc_)
        bmp_ = gdi32.CreateCompatibleBitmap(hdc_, width, height)
        gdi32.SelectObject(mem_, bmp_)
        user32.PrintWindow(hwnd, mem_, 2)
        info = BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.biWidth = width
        info.biHeight = -height
        info.biPlanes = 1
        info.biBitCount = 32
        buf = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(mem_, bmp_, 0, height, buf, ctypes.byref(info), 0)
        img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).convert("RGB")
        path = Path(subprocess.os.environ.get("TEMP", ".")) / f"titlebar-{tag}.png"
        img.crop((0, 0, width, min(44, height))).save(path)
        print(f"  [{tag}] 左上角 {img.getpixel((3, 3))} 标题中部 {img.getpixel((width // 2, 8))} 右上 {img.getpixel((width - 140, 15))}")
        gdi32.DeleteObject(bmp_)
        gdi32.DeleteDC(mem_)
        user32.ReleaseDC(hwnd, hdc_)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
        ]

    print("  窗口在前台：")
    grab("active")

    # 把焦点让给别人，看失焦时会不会变浅
    shell = user32.FindWindowW("Shell_TrayWnd", None)
    if shell:
        user32.SetForegroundWindow(shell)
        time.sleep(1.2)
        print("  窗口失去焦点：")
        grab("inactive")
        user32.SetForegroundWindow(hwnd)

    process.terminate()
    time.sleep(0.6)
    try:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    except OSError:
        pass
    return

    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem, bitmap)
    ok = user32.PrintWindow(hwnd, mem, 2)
    print(f"  PrintWindow 结果：{ok}")

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem, bitmap, 0, height, buffer, ctypes.byref(header), 0)
    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).convert("RGB")

    strip = image.crop((0, 0, width, min(44, height)))
    out = Path(subprocess.os.environ.get("TEMP", ".")) / "titlebar-strip.png"
    strip.save(out)
    print(f"  标题栏条已保存：{out}")
    print(f"  左上角像素：{strip.getpixel((3, 3))} 中间像素：{strip.getpixel((width // 2, 8))}")

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    process.terminate()
    time.sleep(0.6)
    try:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    except OSError:
        pass


if __name__ == "__main__":
    main()
