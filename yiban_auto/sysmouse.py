# -*- coding: utf-8 -*-
"""系统级真实鼠标点击（Windows SendInput）。

用途：某些验证码/风控会检查鼠标事件是否来自真实输入设备。
CDP 的 Input.dispatchMouseEvent 虽然也是“可信事件”，但个别页面仍会拒绝；
这里提供一条兜底：把浏览器窗口对应的屏幕坐标算出来，移动真实鼠标并按下。

坐标换算：
    视口 CSS 坐标 (x, y)  ->  窗口客户区像素  ->  屏幕像素
    客户区宽度 / innerWidth = 缩放比（高 DPI 时 1.25 / 1.5 / 2.0）
    客户区高度 - innerHeight*缩放比 = 浏览器工具栏高度（地址栏那一条）

只做“点击”，不做拖动（滑块仍走 CDP 轨迹）。任何一步不满足条件都返回 False，
由调用方退回 CDP 点击，绝不乱点。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Optional, Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
SW_RESTORE = 9


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def available() -> bool:
    return True if hasattr(ctypes, "windll") else False


def _windows_for_pid(pid: int):
    handles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            handles.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    return handles


def find_window(pid: int) -> Optional[int]:
    """找指定进程的顶层窗口；优先返回有标题的那个。"""
    best = None
    best_area = 0
    for hwnd in _windows_for_pid(pid):
        rect = RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            continue
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 200 or height < 200:
            continue
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
        area = width * height
        if title and area > best_area:
            best, best_area = hwnd, area
    return best


def is_foreground(hwnd: int) -> bool:
    try:
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def bring_foreground(hwnd: int) -> None:
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
    except Exception:
        pass


def client_origin(hwnd: int) -> Tuple[int, int, int, int]:
    """返回客户区左上角的屏幕坐标与客户区尺寸（物理像素）。"""
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetClientRect 失败")
    point = POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
        raise OSError("ClientToScreen 失败")
    return (
        int(point.x),
        int(point.y),
        int(rect.right - rect.left),
        int(rect.bottom - rect.top),
    )


def _move(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def _click_here() -> None:
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.04)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def click_client_point(hwnd: int, x: float, y: float, viewport_w: float, viewport_h: float,
                       human: bool = True) -> bool:
    """在窗口客户区的视口坐标 (x, y) 上做一次真实鼠标点击。

    x/y 是页面视口 CSS 坐标；viewport_w/h 来自 window.innerWidth/innerHeight。
    """
    if viewport_w <= 0 or viewport_h <= 0:
        return False
    try:
        origin_x, origin_y, client_w, client_h = client_origin(hwnd)
    except Exception:
        return False
    # 宽度比例就是“CSS 像素 -> 客户区像素”的缩放比；
    # 高度不能直接用 client_h/inner_h，因为客户区还包含工具栏。
    scale = client_w / float(viewport_w)
    toolbar = max(0.0, client_h - viewport_h * scale)
    screen_x = origin_x + x * scale
    screen_y = origin_y + toolbar + y * scale
    if not (origin_x <= screen_x <= origin_x + client_w and origin_y <= screen_y <= origin_y + client_h):
        return False

    bring_foreground(hwnd)
    if not is_foreground(hwnd):
        return False

    current = POINT()
    user32.GetCursorPos(ctypes.byref(current))
    start_x, start_y = float(current.x), float(current.y)
    steps = 8 if human else 1
    for index in range(1, steps + 1):
        ratio = index / float(steps)
        _move(
            int(start_x + (screen_x - start_x) * ratio),
            int(start_y + (screen_y - start_y) * ratio),
        )
        time.sleep(0.012)
    time.sleep(0.05)
    _click_here()
    return True
