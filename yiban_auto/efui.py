# -*- coding: utf-8 -*-
"""界面基础部件。

配色、字号、切角与间距集中在这里，改一处即可整体换肤。
风格约定：
  * 纸色打底、白色面板、1px 细线分区，不使用圆角与投影；
  * 只有一个强调色（signal），只做填充，含义是“当前位置 / 可点 / 要看一眼”；
  * 切掉一个角的块才是可以按的，展示用的容器保持直角；
  * 英文小字大写拉宽在上，中文粗体在下；数字用等宽数字排版。
"""
from __future__ import annotations

import ctypes
import os
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Dict, Optional

# --------------------------------------------------------------------- tokens
PAPER = "#F4F4F2"
PANEL = "#FFFFFF"
INK = "#161616"
INK2 = "#5C5C5C"
RULE = "#D9D9D6"
CHIP = "#E4E4E0"
SIGNAL = "#FFFA00"
ON_SIGNAL = "#161616"
TAG_BG = "#161616"
TAG_FG = "#FFFFFF"
DANGER = "#B3261E"
BAR = "#161616"          # 控制台命令栏：两套主题里都是深色
BAR_TEXT = "#EDEDED"
BAR_DIM = "#A3A3A3"
TRI = ("#FF00F0", SIGNAL, "#00FFA2")
LOG_BG = "#191919"
LOG_FG = "#EDEDED"

CUT = 9                   # 按钮切角
UI_FONT = "Microsoft YaHei UI"
WIDE_FONT = "Microsoft YaHei UI"

_registered = False
_saira = False


def register_fonts() -> bool:
    """把随包字体注册到当前进程；注册成功返回 True。"""
    global _registered, _saira
    if _registered:
        return _saira
    _registered = True
    candidates = []
    try:
        from .util import resource_path

        candidates.append(str(resource_path("assets/fonts/Saira.ttf")))
    except Exception:
        pass
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts", "Saira.ttf"))
    for path in candidates:
        try:
            if os.path.exists(path) and ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0):
                _saira = True
                break
        except Exception:
            continue
    return _saira


def wide(size: int = 9, weight: str = "bold") -> tuple:
    """宽体大写拉丁字（眉标、编号、版本号）。"""
    family = "Saira" if _saira else "Segoe UI"
    return (family, size, weight)


def ui(size: int = 9, weight: str = "normal") -> tuple:
    return (UI_FONT, size, weight)


def mono(size: int = 9, weight: str = "normal") -> tuple:
    return ("Consolas", size, weight)


def track(text: str, gap: str = "\u2009") -> str:
    """用细空格拉开拉丁大写字母的间距（Tk 没有 letter-spacing）。"""
    return gap.join(list(text))


# ------------------------------------------------------------------- widgets
class CutButton(tk.Canvas):
    """切角按钮：静止是浅灰或墨底，悬停时强调色从左擦入，按下即整块强调色。"""

    def __init__(
        self,
        master,
        text: str,
        command: Optional[Callable[[], None]] = None,
        kind: str = "ghost",
        width: Optional[int] = None,
        height: int = 34,
        pad: int = 18,
        font_size: int = 10,
    ):
        self.kind = kind
        self._text = text
        self._command = command
        self._height = height
        self._pad = pad
        self._font = ui(font_size, "bold")
        self._state = "normal"
        self._hover = False
        self._sweep = 0.0
        self._after: Optional[str] = None
        self._enabled = True
        bg = PANEL if kind != "ghost" else PAPER
        super().__init__(
            master,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=bg,
            cursor="hand2",
            takefocus=1,
        )
        self._text_id = self.create_text(0, 0, text=self._text, font=self._font, anchor="w")
        self._fit(width)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Key-space>", lambda e: self._invoke())
        self.bind("<Return>", lambda e: self._invoke())

    # ---- geometry
    def _measure(self) -> int:
        f = tkfont.Font(font=self._font)
        return f.measure(self._text)

    def _fit(self, width: Optional[int] = None) -> None:
        w = width or (self._measure() + self._pad * 2 + CUT)
        tk.Canvas.configure(self, width=w)
        self._redraw()

    # ---- painting
    def _colors(self) -> Dict[str, str]:
        if not self._enabled:
            return {"bg": CHIP, "fg": "#9B9B96", "sweep": CHIP}
        if self.kind == "primary":
            base = {"bg": INK, "fg": "#FFFFFF", "sweep": SIGNAL}
        elif self.kind == "danger":
            base = {"bg": CHIP, "fg": DANGER, "sweep": SIGNAL}
        else:
            base = {"bg": CHIP, "fg": INK, "sweep": SIGNAL}
        return base

    def _redraw(self) -> None:
        w = max(int(self.winfo_width()), self._measure() + self._pad * 2 + CUT)
        h = self._height
        self.delete("plate")
        col = self._colors()
        cut = CUT
        pts = [0, 0, w, 0, w, h - cut, w - cut, h, 0, h]
        outline = RULE if (self._enabled and self.kind == "ghost") else ""
        self.create_polygon(pts, fill=col["bg"], outline=outline, width=1, tags="plate")
        if self._sweep > 0 and self._enabled:
            sw = int(w * self._sweep)
            if sw > 0:
                fill = col["sweep"]
                if sw >= w - cut:
                    # 整块已铺满：直接按切角形状铺，避免把切口填成方块
                    self.create_polygon(pts, fill=fill, outline="", tags="plate")
                else:
                    self.create_rectangle(0, 0, sw, h, fill=fill, outline="", tags="plate")
        fg = col["fg"]
        if self._sweep >= 0.5:
            fg = ON_SIGNAL if self._enabled else col["fg"]
        self.itemconfigure(self._text_id, fill=fg)
        self.coords(self._text_id, self._pad, h // 2 + 1)
        self.tag_raise(self._text_id)

    def _animate(self, target: float) -> None:
        if self._after:
            try:
                self.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        step = 0.34 if target > self._sweep else 0.5

        def tick() -> None:
            if target > self._sweep:
                self._sweep = min(target, self._sweep + step)
            else:
                self._sweep = max(target, self._sweep - step)
            self._redraw()
            if abs(self._sweep - target) > 0.001:
                self._after = self.after(28, tick)
            else:
                self._after = None

        tick()

    # ---- events
    def _on_enter(self, _event=None) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._animate(1.0)

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        if self._enabled:
            self._animate(0.0)

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self._redraw()

    def _on_release(self, event=None) -> None:
        if not self._enabled:
            return
        inside = 0 <= getattr(event, "x", 0) <= self.winfo_width() and 0 <= getattr(event, "y", 0) <= self._height
        if inside:
            self._invoke()
        if self._hover:
            self._animate(1.0)
        else:
            self._animate(0.0)

    def _invoke(self) -> None:
        if not self._enabled or self._command is None:
            return
        try:
            self._command()
        except Exception:
            pass

    # ---- ttk 风格接口
    def configure(self, **kwargs):
        if "text" in kwargs:
            self._text = str(kwargs.pop("text"))
            self.itemconfigure(self._text_id, text=self._text)
            self._fit()
        if "state" in kwargs:
            state = str(kwargs.pop("state"))
            self._enabled = state != "disabled"
            self._state = state
            self.configure_cursor = "hand2" if self._enabled else "arrow"
            try:
                super().configure(cursor="hand2" if self._enabled else "arrow")
            except Exception:
                pass
            if not self._enabled:
                self._sweep = 0.0
            self._redraw()
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "width" in kwargs:
            w = int(kwargs.pop("width"))
            tk.Canvas.configure(self, width=w)
            self._redraw()
        if kwargs:
            return super().configure(**kwargs)
        return None

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)

    def invoke(self) -> None:
        self._invoke()


class Panel(tk.Frame):
    """白底细线面板：眉标 + 中文标题 + 细线，下面留给内容。"""

    def __init__(self, master, latin: str, title: str, number: str = "", dark: bool = False, pad: int = 10):
        bg = PANEL if not dark else LOG_BG
        super().__init__(master, bg=bg, highlightbackground=RULE, highlightthickness=1, bd=0)
        self.columnconfigure(0, weight=1)
        head = tk.Frame(self, bg=bg)
        head.grid(row=0, column=0, sticky="ew", padx=(pad, pad), pady=(6, 4))
        head.columnconfigure(1, weight=1)
        label = (number + " " + latin).strip()
        tk.Label(
            head,
            text=track(label.upper()),
            font=wide(8, "bold"),
            fg=BAR_DIM if dark else INK2,
            bg=bg,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            head,
            text=title,
            font=ui(11, "bold"),
            fg=LOG_FG if dark else INK,
            bg=bg,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))
        rule = tk.Frame(self, bg="#353535" if dark else RULE, height=1)
        rule.grid(row=1, column=0, sticky="ew")
        self.body = tk.Frame(self, bg=bg)
        self.body.grid(row=2, column=0, sticky="nsew", padx=pad, pady=(6, pad - 2))
        self.rowconfigure(2, weight=1)
        self.body.columnconfigure(0, weight=1)  # 调用方可在 body 里再排子网格
        self.bg = bg


class TriStrip(tk.Canvas):
    """三色条：只出现在屏幕开始处。"""

    def __init__(self, master, height: int = 3):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=PAPER)
        self._h = height
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None) -> None:
        w = max(1, self.winfo_width())
        self.delete("all")
        seg = w / 3.0
        for i, color in enumerate(TRI):
            self.create_rectangle(i * seg, 0, (i + 1) * seg, self._h, fill=color, outline="")


class Field(tk.Frame):
    """字段：宽体小标签 + 竖线 + 等宽数字。"""

    def __init__(self, master, label: str, value: str = "", bg: str = BAR, fg: str = BAR_TEXT, dim: str = BAR_DIM):
        super().__init__(master, bg=bg)
        tk.Label(self, text=track(label.upper()), font=wide(8, "bold"), fg=dim, bg=bg).grid(row=0, column=0, sticky="w")
        tk.Label(self, text="│", font=mono(9), fg=dim, bg=bg).grid(row=0, column=1, sticky="w", padx=6)
        self.value = tk.Label(self, text=value, font=mono(9, "bold"), fg=fg, bg=bg)
        self.value.grid(row=0, column=2, sticky="w")

    def set(self, text: str) -> None:
        self.value.configure(text=text)


def hairline(master, bg: str = RULE) -> tk.Frame:
    line = tk.Frame(master, bg=bg, height=1)
    return line


_CHECK_IMAGES: Dict[str, tk.PhotoImage] = {}


def _check_image(master, on: bool) -> tk.PhotoImage:
    """勾选指示器：未选是白底细框，选中是墨底加一道强调色对勾。"""
    key = "on" if on else "off"
    if key in _CHECK_IMAGES:
        return _CHECK_IMAGES[key]
    size = 14
    img = tk.PhotoImage(master=master, width=size, height=size)
    img.put(RULE, to=(0, 0, size, size))
    if on:
        img.put(INK, to=(1, 1, size - 1, size - 1))
        for x, y in ((3, 7), (4, 8), (5, 9), (6, 8), (7, 7), (8, 6), (9, 5), (10, 4)):
            img.put(SIGNAL, to=(x, y, x + 2, y + 2))
    else:
        img.put(PANEL, to=(1, 1, size - 1, size - 1))
    _CHECK_IMAGES[key] = img
    return img


def install_check_style(style, master) -> None:
    """把 ttk 复选框的指示器换成上面那张图（clam 默认会画成 ✕）。"""
    off = _check_image(master, False)
    on = _check_image(master, True)
    try:
        style.element_create("EfCheck.indicator", "image", off, ("selected", on), border=0, sticky="")
        style.layout(
            "TCheckbutton",
            [
                (
                    "Checkbutton.padding",
                    {
                        "sticky": "nswe",
                        "children": [
                            ("EfCheck.indicator", {"side": "left", "sticky": ""}),
                            (
                                "Checkbutton.focus",
                                {
                                    "side": "left",
                                    "sticky": "w",
                                    "children": [("Checkbutton.label", {"sticky": "nswe"})],
                                },
                            ),
                        ],
                    },
                )
            ],
        )
    except tk.TclError:
        pass


def hatch_band(master, height: int = 8, bg: str = PAPER) -> tk.Canvas:
    """45° 斜线纹带：只用于分隔，不承载信息。"""
    c = tk.Canvas(master, height=height, highlightthickness=0, bd=0, bg=bg)

    def draw(_event=None):
        w = max(1, c.winfo_width())
        c.delete("all")
        for x in range(-height, w + height, 9):
            c.create_line(x, height, x + height, 0, fill="#C9C9C5")

    c.bind("<Configure>", draw)
    return c
