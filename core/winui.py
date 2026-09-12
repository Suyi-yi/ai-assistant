"""Windows 窗口外观微调：让系统标题栏跟深色主题一致。

默认标题栏是浅色的，和深紫界面撞在一起很突兀。这里用 DWM 接口把它染成主题色，
标题文字转成浅色，1px 边框也一起改掉（Win11 才支持边框和标题色）。
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes

DWMWA_USE_IMMERSIVE_DARK_MODE = 20  # Win10 2004+ / Win11
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # Win10 1809 ~ 1909
DWMWA_BORDER_COLOR = 34  # Win11
DWMWA_CAPTION_COLOR = 35  # Win11
DWMWA_TEXT_COLOR = 36  # 标题文字颜色


def _colorref(hex_color: str) -> int:
    """DWM 用的是 0x00BBGGRR。"""
    value = (hex_color or "").lstrip("#")
    if len(value) != 6:
        value = "150E2A"
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return (blue << 16) | (green << 8) | red


def _bind():
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    dwmapi.DwmSetWindowAttribute.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
    return user32, dwmapi


def _find_own_window(user32, expected_title: str = "") -> int:
    """按进程号找自己的顶层窗口。

    不用 FindWindow 按标题找：标题是中文，容易受命令行编码影响匹配不上。
    """
    pid = os.getpid()
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        # 只认有标题的顶层窗口，排除 WebView2 的子窗口
        if user32.GetWindowTextLengthW(hwnd) > 0:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else 0


def _paint(dwmapi, hwnd, caption: str, text: str) -> None:
    dark = ctypes.c_int(1)
    for attribute in (DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(dark), ctypes.sizeof(dark))
    for attribute, color in (
        (DWMWA_CAPTION_COLOR, caption),
        (DWMWA_BORDER_COLOR, caption),
        (DWMWA_TEXT_COLOR, text),
    ):
        value = ctypes.c_int(_colorref(color))
        dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))


def apply_dark_titlebar(
    title: str,
    caption: str = "#150E2A",
    text: str = "#ECE7FF",
    attempts: int = 20,
    gap: float = 0.3,
    repaint_times: int = 3,
) -> bool:
    """按窗口标题找到顶层窗口并染色。

    窗口创建和 DWM 重绘有先后关系，所以找到之后再重复刷几次，避免被系统改回去。
    """
    if not title:
        return False
    try:
        user32, dwmapi = _bind()
    except (AttributeError, OSError):
        return False

    hwnd = 0
    for _ in range(attempts):
        hwnd = _find_own_window(user32, title)
        if hwnd:
            break
        time.sleep(gap)
    if not hwnd:
        return False

    for index in range(max(1, repaint_times)):
        _paint(dwmapi, hwnd, caption, text)
        if index + 1 < repaint_times:
            time.sleep(0.8)
    return True
