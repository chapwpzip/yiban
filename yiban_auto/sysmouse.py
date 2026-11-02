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


BROWSER_CLASSES = ("Chrome_WidgetWin_1", "Chrome_WidgetWin_0")


def _window_title(hwnd: int) -> str:
    try:
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()
    except Exception:
        return ""


def _window_class(hwnd: int) -> str:
    try:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value
    except Exception:
        return ""


def is_valid(hwnd: int) -> bool:
    """窗口句柄是否还有效（没被关掉）。"""
    try:
        return bool(hwnd) and bool(user32.IsWindow(int(hwnd)))
    except Exception:
        return False


def has_render_child(hwnd: int) -> bool:
    for class_name in ("Chrome_RenderWidgetHostHWND", "Internet Explorer_Server"):
        try:
            if user32.FindWindowExW(hwnd, 0, class_name, None):
                return True
        except Exception:
            pass
    return False


def locate_browser_window(title_hint: str = "", pid_hint: int = 0) -> Optional[int]:
    """在所有浏览器窗口里挑出“真的装着网页的那个主窗口”。

    为什么不能只看进程号：Edge 的兼容层会把浏览器重启到新进程，
    我们 Popen 的那个 pid 可能已经不是真正的窗口进程；另外同进程还会有
    “同步确认”之类的小弹窗。这里按 标题匹配 > 进程匹配 > 前台 > 面积 打分。
    """
    best = None
    best_score = -1
    hint = (title_hint or "").strip()
    hint_head = hint[:12]
    for hwnd in _all_top_windows():
        try:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                continue
            if _window_class(hwnd) not in BROWSER_CLASSES:
                continue
            if not has_render_child(hwnd):
                continue
            rect = RECT()
            if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
                continue
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width < 300 or height < 200:
                continue
            title = _window_title(hwnd)
            score = 0
            if hint and title and title == hint:
                score += 100
            elif hint_head and title and hint_head in title:
                score += 60
            elif hint and title and title[:12] and title[:12] in hint:
                score += 40
            if pid_hint and _pid_of(hwnd) == pid_hint:
                score += 30
            if user32.GetForegroundWindow() == hwnd:
                score += 10
            if width * height >= 800 * 600:
                score += 8
            if title:
                score += 5
            if score > best_score:
                best, best_score = hwnd, score
        except Exception:
            continue
    return best


def _all_top_windows():
    handles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        handles.append(hwnd)
        return True

    user32.EnumWindows(_callback, 0)
    return handles


def capture_under_cursor() -> int:
    """返回鼠标当前位置所在的浏览器主窗口（用户点哪里就认哪里）。"""
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return 0
    hwnd = _root(window_at(point.x, point.y))
    if not hwnd:
        return 0
    if _window_class(hwnd) in BROWSER_CLASSES and has_render_child(hwnd):
        return hwnd
    # 点在标签栏/地址栏时，往上找父窗口
    parent = hwnd
    for _ in range(4):
        parent = int(user32.GetParent(parent) or 0)
        if not parent:
            break
        if _window_class(parent) in BROWSER_CLASSES and has_render_child(parent):
            return parent
    return 0


def find_window(pid: int) -> Optional[int]:
    """找指定进程的浏览器窗口（兼容 Edge 重启到新进程的情况）。"""
    hwnd = locate_browser_window("", pid)
    if hwnd:
        return hwnd
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
        area = width * height
        if _window_title(hwnd) and area > best_area:
            best, best_area = hwnd, area
    return best


def _pid_of(hwnd: int) -> int:
    try:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        return int(owner.value)
    except Exception:
        return 0


def _root(hwnd: int) -> int:
    try:
        return int(user32.GetAncestor(hwnd, 2) or hwnd)  # GA_ROOT
    except Exception:
        return int(hwnd or 0)


def window_at(x: float, y: float) -> int:
    """屏幕坐标 (x, y) 处最上层的窗口（忽略“点击穿透”的窗口）。"""
    try:
        point = POINT(int(x), int(y))
        return int(user32.WindowFromPoint(point) or 0)
    except Exception:
        return 0


def describe(hwnd: int) -> str:
    """给日志用：窗口类名 + 标题。"""
    try:
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1) if length > 0 else None
        if title is not None:
            user32.GetWindowTextW(hwnd, title, length + 1)
        return "%s[%s]" % (title.value if title is not None else "", cls.value)
    except Exception:
        return "hwnd=" + str(hwnd)


def is_foreground(hwnd: int) -> bool:
    """前台窗口是否就是浏览器主窗口（或其子窗口）。"""
    try:
        foreground = user32.GetForegroundWindow()
        if not foreground:
            return False
        if foreground == hwnd:
            return True
        return _root(foreground) == hwnd
    except Exception:
        return False


def bring_foreground(hwnd: int) -> None:
    """把浏览器窗口拉到前台。

    Windows 有前台锁：后台程序调用 SetForegroundWindow 常常被忽略，
    这里用 AttachThreadInput 附加到当前前台线程再抢，成功率明显更高。
    """
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.15)
        if is_foreground(hwnd):
            return
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        if is_foreground(hwnd):
            return
        foreground = user32.GetForegroundWindow()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        current_thread = user32.GetCurrentThreadId()
        attached = False
        try:
            if foreground_thread and foreground_thread != current_thread:
                attached = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
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


def viewport_rect(hwnd: int):
    """找出网页内容区在屏幕上的精确矩形，返回 (x, y, w, h) 或 None。

    Chrome/Edge 的网页内容在一个子窗口 Chrome_RenderWidgetHostHWND 里，
    它的客户区就是视口本身——不包含标签栏、地址栏、书签栏、侧边栏，
    比“窗口客户区减去工具栏”可靠得多（换机器、换缩放、开侧边栏都不会错位）。
    """
    for class_name in ("Chrome_RenderWidgetHostHWND", "Internet Explorer_Server"):
        try:
            child = user32.FindWindowExW(hwnd, 0, class_name, None)
        except Exception:
            child = None
        if not child:
            continue
        rect = RECT()
        if not user32.GetClientRect(child, ctypes.byref(rect)):
            continue
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 100 or height < 100:
            continue
        point = POINT(0, 0)
        if not user32.ClientToScreen(child, ctypes.byref(point)):
            continue
        return int(point.x), int(point.y), int(width), int(height)
    return None


def click_client_point(hwnd: int, x: float, y: float, viewport_w: float, viewport_h: float,
                       human: bool = True, detail: Optional[dict] = None) -> bool:
    """在窗口的视口坐标 (x, y) 上做一次真实鼠标点击。

    x/y 是页面视口 CSS 坐标；viewport_w/h 来自 window.innerWidth/innerHeight。
    """
    if viewport_w <= 0 or viewport_h <= 0:
        return False
    rect = viewport_rect(hwnd)
    if rect:
        origin_x, origin_y, view_w, view_h = rect
        # 比例用高度算：纵向没有滚动条干扰，横向的竖向滚动条不会影响这个比值。
        scale = view_h / float(viewport_h) if viewport_h else 1.0
        screen_x = origin_x + x * scale
        screen_y = origin_y + y * scale
        if detail is not None:
            detail.update({"mode": "render-widget", "rect": rect, "scale": round(scale, 4)})
    else:
        try:
            origin_x, origin_y, client_w, client_h = client_origin(hwnd)
        except Exception:
            return False
        scale = client_w / float(viewport_w)
        toolbar = max(0.0, client_h - viewport_h * scale)
        screen_x = origin_x + x * scale
        screen_y = origin_y + toolbar + y * scale
        if detail is not None:
            detail.update({"mode": "client-rect", "rect": (origin_x, origin_y, client_w, client_h),
                           "scale": round(scale, 4)})
    if not (origin_x <= screen_x <= origin_x + 100000 and origin_y <= screen_y <= origin_y + 100000):
        return False
    if detail is not None:
        detail.update({"screen": (round(screen_x, 1), round(screen_y, 1))})

    # 目标点必须真的属于这个浏览器窗口：浏览器自带弹窗（同步确认/欢迎页等）
    # 是独立顶层窗口，浮在页面上方时会把点击吃掉，表现为“鼠标点了没反应”。
    covered = _root(window_at(screen_x, screen_y))
    if covered != hwnd:
        bring_foreground(hwnd)
        covered = _root(window_at(screen_x, screen_y))
        if covered != hwnd:
            if detail is not None:
                detail["error"] = "目标点被其它窗口遮挡：" + describe(covered) if covered else "目标点不在窗口内"
            return False

    bring_foreground(hwnd)
    if not is_foreground(hwnd):
        if detail is not None:
            detail["error"] = "浏览器不在前台"
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
    # 确认鼠标真的落到了目标点（有的机器被第三方软件抢鼠标/缩放没生效会偏）
    landed = POINT()
    user32.GetCursorPos(ctypes.byref(landed))
    if abs(landed.x - int(screen_x)) > 4 or abs(landed.y - int(screen_y)) > 4:
        if detail is not None:
            detail["error"] = "鼠标未落到目标点"
            detail["cursor"] = (int(landed.x), int(landed.y))
        return False
    _click_here()
    return True
