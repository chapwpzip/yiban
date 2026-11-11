# -*- coding: utf-8 -*-
"""Tkinter 图形界面。"""
from __future__ import annotations

import copy
import os
import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __app_name__, __version__
from .browser import create_engine
from .browser.base import StopRequested
from .config import (
    ConfigError,
    TEMPLATE_CATEGORIES,
    load_settings,
    normalize_settings,
    resolve_settings_path,
    save_settings,
)
from . import efui
from . import secure_store
from . import sysmouse
from .captcha import CaptchaSolver
from .content import ContentError, generate_post, load_preset_file, save_preset_file, validate_post
from .flow import IMAGE_CAPTCHA_SELECTOR, SHUMEI_POPUP_SELECTOR, YibanFlow
from .util import (
    app_dir,
    data_dir,
    log_dir,
    now_text,
    resource_path,
    screenshot_dir,
    truncate,
)


ENGINE_LABELS = {
    "auto": "自动（Selenium→CDP）",
    "selenium": "Selenium / WebDriver",
    "cdp": "CDP（免驱动，推荐）",
}
ENGINE_KEYS = {v: k for k, v in ENGINE_LABELS.items()}

MODE_LABELS = {
    "template": "模板化生成",
    "llm": "LLM API",
    "preset": "手动预置内容",
}
MODE_KEYS = {v: k for k, v in MODE_LABELS.items()}

CAPTCHA_MODE_LABELS = {
    "auto": "自动尝试（默认）",
    "manual": "只人工接管",
    "http": "外接 HTTP 服务",
    "command": "外接程序（stdin/stdout）",
}
CAPTCHA_MODE_KEYS = {v: k for k, v in CAPTCHA_MODE_LABELS.items()}

LEVEL_TAGS = {"info": "info", "warn": "warn", "error": "error", "success": "success"}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._base_title = f"{__app_name__} v{__version__}"
        self.root.title(self._base_title)
        try:
            dpi_scale = max(1.0, min(2.0, float(self.root.winfo_fpixels("1i")) / 96.0))
        except Exception:
            dpi_scale = 1.0
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = int(min(1180 * dpi_scale, max(900, screen_w - 48)))
        height = int(min(820 * dpi_scale, max(640, screen_h - 80)))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height - 80) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(int(1080 * dpi_scale), int(680 * dpi_scale))
        try:
            self.root.state("zoomed")
        except tk.TclError:
            pass

        self.ui_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.manual_event = threading.Event()
        self.manual_focus_event = threading.Event()
        self._minimized_for_manual = False
        self.manual_result = False
        self._manual_failure_reason = ""
        self.running = False
        self.engine = None
        self.locked_hwnd = 0
        self.worker: Optional[threading.Thread] = None
        self._closing = False
        self._last_screenshot = ""
        self._log_lock = threading.Lock()
        self._log_path = log_dir() / ("app-" + datetime.now().strftime("%Y%m%d") + ".log")

        self.settings_path = resolve_settings_path()
        self._config_error = ""
        try:
            self.settings = load_settings(self.settings_path)
        except ConfigError as exc:
            self.settings = normalize_settings({})
            self._config_error = str(exc)
        except Exception as exc:
            self.settings = normalize_settings({})
            self._config_error = "配置读取失败：" + str(exc)

        self._create_vars()
        self._build_style()
        self._build_ui()
        self._load_settings_to_ui()
        self._apply_mode_state()
        self._apply_password_state()
        self._apply_key_state()
        self._apply_captcha_key_state()
        self._update_counts()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(120, self._poll_queue)
        self.root.after(350, self._startup_log)

    # ------------------------------------------------------------------ vars
    def _create_vars(self) -> None:
        self.account_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.show_password_var = tk.BooleanVar(value=False)

        self.mode_var = tk.StringVar(value=MODE_LABELS["template"])
        self.category_var = tk.StringVar(value="日常寒暄")
        self.topic_var = tk.StringVar()
        self.llm_base_var = tk.StringVar()
        self.llm_key_var = tk.StringVar()
        self.llm_model_var = tk.StringVar()
        self.llm_temp_var = tk.StringVar()
        self.show_key_var = tk.BooleanVar(value=False)

        self.captcha_mode_var = tk.StringVar(value=CAPTCHA_MODE_LABELS["auto"])
        self.captcha_http_var = tk.StringVar()
        self.captcha_key_var = tk.StringVar()
        self.captcha_command_var = tk.StringVar()
        self.captcha_timeout_var = tk.StringVar(value="60")
        self.captcha_attempts_var = tk.StringVar(value="2")
        self.captcha_retry_var = tk.StringVar(value="4")
        self.captcha_cv_var = tk.BooleanVar(value=True)
        self.captcha_sysmouse_var = tk.BooleanVar(value=True)
        self.captcha_slider_var = tk.BooleanVar(value=True)
        self.captcha_ocr_var = tk.BooleanVar(value=True)
        self.captcha_vision_var = tk.BooleanVar(value=True)
        self.captcha_vision_base_var = tk.StringVar()
        self.captcha_vision_key_var = tk.StringVar()
        self.captcha_vision_model_var = tk.StringVar()
        self.captcha_fallback_var = tk.BooleanVar(value=True)
        self.show_captcha_key_var = tk.BooleanVar(value=False)

        self.preset_items: list = []
        self.preset_pick_var = tk.StringVar(value="按顺序")
        self.remember_var = tk.BooleanVar(value=False)

        self.community_var = tk.StringVar()
        self.board_var = tk.StringVar()
        self.repeat_var = tk.StringVar(value="1")
        self.interval_var = tk.StringVar(value="30")
        self.retry_var = tk.StringVar(value="2")
        self.retry_interval_var = tk.StringVar(value="20")
        self.regenerate_var = tk.BooleanVar(value=True)
        self.verify_http_var = tk.BooleanVar(value=True)
        self.engine_var = tk.StringVar(value=ENGINE_LABELS["auto"])
        self.headless_var = tk.BooleanVar(value=False)
        self.keep_open_var = tk.BooleanVar(value=False)

        self.title_var = tk.StringVar()
        self.summary_var = tk.StringVar()
        self.title_count_var = tk.StringVar(value="0/30")
        self.body_count_var = tk.StringVar(value="0/10000")

        self.status_var = tk.StringVar(value="就绪")
        self.engine_label_var = tk.StringVar(value="引擎：未启动")
        self.result_var = tk.StringVar(value="就绪：填写账号密码，生成或修改内容后点击“开始发帖”。")
        self.manual_msg_var = tk.StringVar()

    # ----------------------------------------------------------------- style
    def _build_style(self) -> None:
        efui.register_fonts()
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.root.configure(bg=efui.PAPER)
        try:
            self.root.option_add("*Font", efui.ui(10))
            self.root.option_add("*TCombobox*Listbox.font", efui.ui(10))
            self.root.option_add("*TCombobox*Listbox.background", efui.PANEL)
            self.root.option_add("*TCombobox*Listbox.foreground", efui.INK)
            self.root.option_add("*TCombobox*Listbox.selectBackground", efui.SIGNAL)
            self.root.option_add("*TCombobox*Listbox.selectForeground", efui.ON_SIGNAL)
        except Exception:
            pass

        style.configure(".", font=efui.ui(9), background=efui.PAPER, foreground=efui.INK)
        style.configure("TFrame", background=efui.PANEL)
        style.configure("TLabel", background=efui.PANEL, foreground=efui.INK, font=efui.ui(9))
        style.configure("Field.TLabel", background=efui.PANEL, foreground=efui.INK, font=efui.ui(9))
        style.configure("Hint.TLabel", background=efui.PANEL, foreground=efui.INK2, font=efui.ui(8))
        style.configure(
            "TEntry",
            fieldbackground=efui.PANEL,
            foreground=efui.INK,
            insertcolor=efui.INK,
            bordercolor=efui.RULE,
            lightcolor=efui.RULE,
            darkcolor=efui.RULE,
            borderwidth=1,
            relief="flat",
            padding=5,
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", efui.INK)],
            lightcolor=[("focus", efui.INK)],
            darkcolor=[("focus", efui.INK)],
            fieldbackground=[("disabled", efui.CHIP)],
            foreground=[("disabled", "#9B9B96")],
        )
        style.configure(
            "TCombobox",
            fieldbackground=efui.PANEL,
            background=efui.CHIP,
            foreground=efui.INK,
            arrowcolor=efui.INK,
            bordercolor=efui.RULE,
            lightcolor=efui.RULE,
            darkcolor=efui.RULE,
            borderwidth=1,
            relief="flat",
            padding=4,
            arrowsize=12,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", efui.PANEL), ("disabled", efui.CHIP)],
            foreground=[("readonly", efui.INK), ("disabled", "#9B9B96")],
            background=[("active", efui.CHIP)],
            bordercolor=[("focus", efui.INK)],
            arrowcolor=[("disabled", "#9B9B96")],
        )
        style.configure(
            "TSpinbox",
            fieldbackground=efui.PANEL,
            background=efui.CHIP,
            foreground=efui.INK,
            arrowcolor=efui.INK,
            bordercolor=efui.RULE,
            lightcolor=efui.RULE,
            darkcolor=efui.RULE,
            borderwidth=1,
            relief="flat",
            padding=4,
            arrowsize=11,
        )
        style.map("TSpinbox", bordercolor=[("focus", efui.INK)], fieldbackground=[("disabled", efui.CHIP)])
        style.configure("TCheckbutton", background=efui.PANEL, foreground=efui.INK, font=efui.ui(9), focuscolor=efui.PANEL)
        style.map(
            "TCheckbutton",
            background=[("active", efui.PANEL)],
            indicatorcolor=[("selected", efui.INK), ("!selected", efui.PANEL)],
        )
        efui.install_check_style(style, self.root)
        style.configure(
            "Vertical.TScrollbar",
            background=efui.CHIP,
            troughcolor=efui.PAPER,
            bordercolor=efui.PAPER,
            arrowcolor=efui.INK2,
            borderwidth=0,
            relief="flat",
            width=10,
        )
        style.map("Vertical.TScrollbar", background=[("active", "#CFCFCA")])
        style.configure("TSeparator", background=efui.RULE)
        style.configure(
            "TButton",
            background=efui.CHIP,
            foreground=efui.INK,
            font=efui.ui(9, "bold"),
            borderwidth=0,
            relief="flat",
            padding=(12, 6),
        )
        style.map(
            "TButton",
            background=[("pressed", efui.SIGNAL), ("active", efui.SIGNAL), ("disabled", efui.CHIP)],
            foreground=[("disabled", "#9B9B96")],
        )
        style.configure(
            "TLabelframe",
            background=efui.PANEL,
            bordercolor=efui.RULE,
            lightcolor=efui.RULE,
            darkcolor=efui.RULE,
            borderwidth=1,
            relief="solid",
        )
        style.configure("TLabelframe.Label", background=efui.PANEL, foreground=efui.INK, font=efui.ui(9, "bold"))
        style.configure("TRadiobutton", background=efui.PANEL, foreground=efui.INK, font=efui.ui(9))
        style.map("TRadiobutton", background=[("active", efui.PANEL)])

    # -------------------------------------------------------------------- ui
    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(4, weight=3)
        self.root.rowconfigure(5, weight=2)

        self._build_header()
        self._build_settings()
        self._build_manual_panel()
        self._build_preview()
        self._build_log()
        self._build_bottom()

    # ----------------------------------------------------------------- parts
    def _label(self, parent, text: str, dim: bool = False, wrap: int = 0, bold: bool = False) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=efui.PANEL,
            fg=efui.INK2 if dim else efui.INK,
            font=efui.ui(8 if dim else 9, "bold" if bold else "normal"),
            anchor="w",
            justify="left",
            wraplength=wrap,
        )

    def _build_header(self) -> None:
        strip = efui.TriStrip(self.root, height=3)
        strip.grid(row=0, column=0, sticky="ew")

        header = tk.Frame(self.root, bg=efui.BAR)
        header.grid(row=1, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        mark = tk.Frame(header, bg=efui.SIGNAL, width=5)
        mark.grid(row=0, column=0, sticky="ns", padx=(16, 14), pady=9)

        title_box = tk.Frame(header, bg=efui.BAR)
        title_box.grid(row=0, column=1, sticky="w", pady=9)
        tk.Label(
            title_box,
            text=efui.track("// AUTO POSTER"),
            font=efui.wide(8, "bold"),
            fg=efui.BAR_DIM,
            bg=efui.BAR,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            title_box,
            text=__app_name__,
            font=efui.ui(17, "bold"),
            fg=efui.BAR_TEXT,
            bg=efui.BAR,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(1, 0))
        tk.Label(
            title_box,
            text="仅用于本人易班账号。验证码默认自动尝试，失败后按配置重试并回退人工。",
            font=efui.ui(8),
            fg=efui.BAR_DIM,
            bg=efui.BAR,
            anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=(3, 0))

        meta = tk.Frame(header, bg=efui.BAR)
        meta.grid(row=0, column=2, sticky="e", padx=16, pady=9)
        tk.Label(
            meta,
            text=efui.track("BUILD"),
            font=efui.wide(8, "bold"),
            fg=efui.BAR_DIM,
            bg=efui.BAR,
            anchor="e",
        ).grid(row=0, column=0, sticky="e")
        tk.Label(
            meta,
            text=f"v{__version__}",
            font=efui.mono(11, "bold"),
            fg=efui.BAR_TEXT,
            bg=efui.BAR,
            anchor="e",
        ).grid(row=1, column=0, sticky="e")
        tk.Label(
            meta,
            text="易班微社区",
            font=efui.ui(8),
            fg=efui.BAR_DIM,
            bg=efui.BAR,
            anchor="e",
        ).grid(row=2, column=0, sticky="e")

    def _build_settings(self) -> None:
        wrap = tk.Frame(self.root, bg=efui.PAPER)
        wrap.grid(row=2, column=0, sticky="ew", padx=16, pady=(12, 4))
        for col in range(3):
            wrap.columnconfigure(col, weight=1, uniform="settings")

        # 01 账号
        account_panel = efui.Panel(wrap, "ACCOUNT", "账号", "01")
        account_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        account = tk.Frame(account_panel.body, bg=efui.PANEL)
        account.grid(row=0, column=0, sticky="nsew")
        account.columnconfigure(0, weight=1)
        self._label(account, "易班账号（手机号 / 用户名）").grid(row=0, column=0, sticky="w")
        self.account_entry = ttk.Entry(account, textvariable=self.account_var)
        self.account_entry.grid(row=1, column=0, sticky="ew", pady=(3, 8))
        self._label(account, "密码").grid(row=2, column=0, sticky="w")
        self.password_entry = ttk.Entry(account, textvariable=self.password_var, show="*")
        self.password_entry.grid(row=3, column=0, sticky="ew", pady=(3, 6))
        self.show_password_check = ttk.Checkbutton(
            account, text="显示密码", variable=self.show_password_var, command=self._apply_password_state
        )
        self.show_password_check.grid(row=4, column=0, sticky="w")
        self.remember_check = ttk.Checkbutton(
            account, text="记住账号密码（本机加密保存）", variable=self.remember_var
        )
        self.remember_check.grid(row=5, column=0, sticky="w", pady=(2, 0))
        self._label(
            account,
            "只保存在内存；勾选后用 DPAPI 加密写入本机，换机器无法解密。",
            dim=True,
            wrap=330,
        ).grid(row=6, column=0, sticky="w", pady=(8, 0))

        # 02 内容生成
        content_panel = efui.Panel(wrap, "CONTENT", "内容生成", "02")
        content_panel.grid(row=0, column=1, sticky="nsew", padx=6)
        content = tk.Frame(content_panel.body, bg=efui.PANEL)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(1, weight=1)

        self._label(content, "生成模式").grid(row=0, column=0, sticky="w", pady=1, padx=(0, 8))
        self.mode_combo = ttk.Combobox(
            content, textvariable=self.mode_var, values=list(MODE_LABELS.values()), state="readonly", width=14
        )
        self.mode_combo.grid(row=0, column=1, sticky="ew", pady=1)
        self.mode_combo.bind("<<ComboboxSelected>>", lambda event: self._apply_mode_state())

        self._label(content, "模板分类").grid(row=1, column=0, sticky="w", pady=1, padx=(0, 8))
        self.category_combo = ttk.Combobox(
            content, textvariable=self.category_var, values=list(TEMPLATE_CATEGORIES), state="readonly", width=14
        )
        self.category_combo.grid(row=1, column=1, sticky="ew", pady=1)

        self._label(content, "话题 / 关键词").grid(row=2, column=0, sticky="w", pady=1, padx=(0, 8))
        self.topic_entry = ttk.Entry(content, textvariable=self.topic_var)
        self.topic_entry.grid(row=2, column=1, sticky="ew", pady=1)

        self._label(content, "可写“今天吃了什么”，不写具体地点。", dim=True, wrap=300).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(2, 0)
        )
        efui.hairline(content).grid(row=4, column=0, columnspan=2, sticky="ew", pady=4)

        self._label(content, "API 地址").grid(row=5, column=0, sticky="w", pady=1, padx=(0, 8))
        self.llm_base_entry = ttk.Entry(content, textvariable=self.llm_base_var)
        self.llm_base_entry.grid(row=5, column=1, sticky="ew", pady=1)
        self._label(content, "API Key").grid(row=6, column=0, sticky="w", pady=1, padx=(0, 8))
        self.llm_key_entry = ttk.Entry(content, textvariable=self.llm_key_var, show="*")
        self.llm_key_entry.grid(row=6, column=1, sticky="ew", pady=1)
        self._label(content, "模型").grid(row=7, column=0, sticky="w", pady=1, padx=(0, 8))
        self.llm_model_entry = ttk.Entry(content, textvariable=self.llm_model_var)
        self.llm_model_entry.grid(row=7, column=1, sticky="ew", pady=1)

        temp_row = tk.Frame(content, bg=efui.PANEL)
        temp_row.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        temp_row.columnconfigure(1, weight=1)
        self._label(temp_row, "温度").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.llm_temp_spin = ttk.Spinbox(
            temp_row, from_=0.0, to=2.0, increment=0.1, textvariable=self.llm_temp_var, width=6
        )
        self.llm_temp_spin.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self.show_key_check = ttk.Checkbutton(
            temp_row, text="显示 Key", variable=self.show_key_var, command=self._apply_key_state
        )
        self.show_key_check.grid(row=0, column=2, sticky="e")
        self._label(content, "LLM 模式必须填写 Key；Key 会保存到 settings.json。", dim=True, wrap=300).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )
        self.llm_widgets = [
            self.llm_base_entry,
            self.llm_key_entry,
            self.llm_model_entry,
            self.llm_temp_spin,
            self.show_key_check,
        ]

        preset_row = tk.Frame(content, bg=efui.PANEL)
        preset_row.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.preset_btn = efui.CutButton(preset_row, text="预置内容…", command=self.edit_preset, height=30, pad=14)
        self.preset_btn.grid(row=0, column=0, sticky="w")
        self.preset_pick_combo = ttk.Combobox(
            preset_row, textvariable=self.preset_pick_var, values=["按顺序", "随机"], state="readonly", width=6
        )
        self.preset_pick_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.preset_hint_label = self._label(content, "", dim=True, wrap=300)
        self.preset_hint_label.grid(row=11, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # 03 发帖设置
        posting_panel = efui.Panel(wrap, "POSTING", "发帖设置", "03")
        posting_panel.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        posting = tk.Frame(posting_panel.body, bg=efui.PANEL)
        posting.grid(row=0, column=0, sticky="nsew")
        posting.columnconfigure(1, weight=1)

        self._label(posting, "微社区").grid(row=0, column=0, sticky="w", pady=1, padx=(0, 8))
        self.community_entry = ttk.Entry(posting, textvariable=self.community_var)
        self.community_entry.grid(row=0, column=1, sticky="ew", pady=1)
        self._label(posting, "版块").grid(row=1, column=0, sticky="w", pady=1, padx=(0, 8))
        self.board_entry = ttk.Entry(posting, textvariable=self.board_var)
        self.board_entry.grid(row=1, column=1, sticky="ew", pady=1)
        self._label(posting, "留空用页面默认的微社区 / 版块。", dim=True, wrap=300).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )

        row = tk.Frame(posting, bg=efui.PANEL)
        row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=1)
        row.columnconfigure(1, weight=1)
        self._label(row, "间隔（秒）").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.interval_spin = ttk.Spinbox(row, from_=10, to=86400, increment=10, textvariable=self.interval_var, width=7)
        self.interval_spin.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self._label(row, "连续篇数").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.repeat_spin = ttk.Spinbox(row, from_=1, to=999, increment=1, textvariable=self.repeat_var, width=5)
        self.repeat_spin.grid(row=0, column=3, sticky="w")

        retry_row = tk.Frame(posting, bg=efui.PANEL)
        retry_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=1)
        retry_row.columnconfigure(1, weight=1)
        self._label(retry_row, "失败重试").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.retry_spin = ttk.Spinbox(retry_row, from_=0, to=10, increment=1, textvariable=self.retry_var, width=4)
        self.retry_spin.grid(row=0, column=1, sticky="w", padx=(0, 8))
        self._label(retry_row, "次，间隔").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.retry_interval_spin = ttk.Spinbox(
            retry_row, from_=5, to=600, increment=5, textvariable=self.retry_interval_var, width=5
        )
        self.retry_interval_spin.grid(row=0, column=3, sticky="w", padx=(0, 4))
        self._label(retry_row, "秒").grid(row=0, column=4, sticky="w")

        checks2 = tk.Frame(posting, bg=efui.PANEL)
        checks2.grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Checkbutton(checks2, text="重试时换内容", variable=self.regenerate_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(checks2, text="HTTP 复核结果", variable=self.verify_http_var).grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )

        self._label(posting, "浏览器引擎").grid(row=6, column=0, sticky="w", pady=1, padx=(0, 8))
        self.engine_combo = ttk.Combobox(
            posting, textvariable=self.engine_var, values=list(ENGINE_LABELS.values()), state="readonly", width=12
        )
        self.engine_combo.grid(row=6, column=1, sticky="ew", pady=1)

        checks = tk.Frame(posting, bg=efui.PANEL)
        checks.grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Checkbutton(checks, text="无界面", variable=self.headless_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(checks, text="发完保持浏览器", variable=self.keep_open_var).grid(
            row=0, column=1, sticky="w", padx=(10, 0)
        )
        self._label(posting, "间隔最低 10 秒；篇数 1-999；封面固定为“无图”。", dim=True, wrap=300).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

    def _build_manual_panel(self) -> None:
        self.manual_frame = tk.Frame(self.root, bg=efui.PANEL, highlightbackground=efui.RULE, highlightthickness=1)
        self.manual_frame.grid(row=3, column=0, sticky="ew", padx=16, pady=(4, 4))
        self.manual_frame.columnconfigure(1, weight=1)

        flag = tk.Frame(self.manual_frame, bg=efui.SIGNAL, width=4)
        flag.grid(row=0, column=0, sticky="ns", padx=(12, 10), pady=10)
        self.manual_label = tk.Label(
            self.manual_frame,
            textvariable=self.manual_msg_var,
            anchor="w",
            justify="left",
            wraplength=1020,
            bg=efui.PANEL,
            fg=efui.INK,
            padx=0,
            pady=0,
            font=efui.ui(10),
        )
        self.manual_label.grid(row=0, column=1, sticky="ew", pady=(12, 4))

        buttons = tk.Frame(self.manual_frame, bg=efui.PANEL)
        buttons.grid(row=1, column=1, sticky="e", pady=(4, 10), padx=(0, 12))
        self.manual_focus_btn = efui.CutButton(buttons, text="打开浏览器", command=self.focus_browser, height=30, pad=14)
        self.manual_focus_btn.grid(row=0, column=0, padx=(0, 6))
        self.manual_ok_btn = efui.CutButton(
            buttons, text="已完成，继续", command=self.complete_manual, kind="primary", height=30, pad=14
        )
        self.manual_ok_btn.grid(row=0, column=1, padx=(0, 6))
        self.manual_cancel_btn = efui.CutButton(
            buttons, text="取消本次", command=self.cancel_manual, height=30, pad=14
        )
        self.manual_cancel_btn.grid(row=0, column=2)
        self.manual_frame.grid_remove()

    def _build_preview(self) -> None:
        panel = efui.Panel(self.root, "PREVIEW", "帖子预览（可直接修改，发布时使用这里的文字）", "04")
        panel.grid(row=4, column=0, sticky="nsew", padx=16, pady=4)
        preview = panel.body
        preview.columnconfigure(0, weight=0)
        preview.columnconfigure(1, weight=1)
        preview.rowconfigure(2, weight=1)

        self._label(preview, "标题").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.title_entry = ttk.Entry(preview, textvariable=self.title_var)
        self.title_entry.grid(row=0, column=1, sticky="ew")
        self.title_count_label = tk.Label(
            preview, textvariable=self.title_count_var, font=efui.mono(9, "bold"), fg=efui.INK2, bg=efui.PANEL, width=8
        )
        self.title_count_label.grid(row=0, column=2, sticky="e", padx=(8, 0))

        self._label(preview, "摘要").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        self.summary_entry = ttk.Entry(preview, textvariable=self.summary_var)
        self.summary_entry.grid(row=1, column=1, sticky="ew", pady=(4, 0))
        self._label(preview, "可留空，由页面自动截取。", dim=True).grid(
            row=1, column=2, sticky="e", padx=(8, 0), pady=(4, 0)
        )

        self._label(preview, "正文").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=(6, 0))
        body_wrap = tk.Frame(preview, bg=efui.PANEL)
        body_wrap.grid(row=2, column=1, columnspan=2, sticky="nsew", pady=(6, 0))
        body_wrap.columnconfigure(0, weight=1)
        body_wrap.rowconfigure(0, weight=1)
        self.body_text = tk.Text(
            body_wrap,
            height=4,
            wrap="word",
            undo=True,
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=efui.RULE,
            highlightcolor=efui.INK,
            bg=efui.PANEL,
            fg=efui.INK,
            insertbackground=efui.INK,
            selectbackground=efui.SIGNAL,
            selectforeground=efui.ON_SIGNAL,
            font=efui.ui(10),
            padx=10,
            pady=8,
            spacing1=2,
            spacing3=2,
        )
        self.body_text.grid(row=0, column=0, sticky="nsew")
        body_scroll = ttk.Scrollbar(body_wrap, orient="vertical", command=self.body_text.yview)
        body_scroll.grid(row=0, column=1, sticky="ns")
        self.body_text.configure(yscrollcommand=body_scroll.set)
        self.body_count_label = tk.Label(
            preview, textvariable=self.body_count_var, font=efui.mono(9, "bold"), fg=efui.INK2, bg=efui.PANEL
        )
        self.body_count_label.grid(row=3, column=2, sticky="e", pady=(4, 0))

        self.title_var.trace_add("write", lambda *args: self._update_counts())
        for seq in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<<Undo>>"):
            self.body_text.bind(seq, lambda event: self.root.after(10, self._update_counts))

    def _build_log(self) -> None:
        panel = efui.Panel(self.root, "LOG", "运行日志", "05", dark=True, pad=10)
        panel.grid(row=5, column=0, sticky="nsew", padx=16, pady=4)
        log_frame = panel.body
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=4,
            wrap="word",
            state="disabled",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            bg=efui.LOG_BG,
            fg=efui.LOG_FG,
            insertbackground=efui.LOG_FG,
            selectbackground=efui.SIGNAL,
            selectforeground=efui.ON_SIGNAL,
            font=efui.mono(9),
            padx=6,
            pady=4,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.tag_configure("info", foreground="#C9C9C2")
        self.log_text.tag_configure("warn", foreground=efui.SIGNAL)
        self.log_text.tag_configure("error", foreground=efui.SIGNAL)
        self.log_text.tag_configure("success", foreground="#FFFFFF")

    def _build_bottom(self) -> None:
        bottom = tk.Frame(self.root, bg=efui.PAPER)
        bottom.grid(row=6, column=0, sticky="ew", padx=16, pady=(2, 8))
        bottom.columnconfigure(0, weight=1)

        efui.hairline(bottom).grid(row=0, column=0, sticky="ew", pady=(0, 8))

        result_wrap = tk.Frame(bottom, bg=efui.PANEL, highlightbackground=efui.RULE, highlightthickness=1)
        result_wrap.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        result_wrap.columnconfigure(1, weight=1)
        self.result_flag = tk.Frame(result_wrap, bg=efui.SIGNAL, width=4)
        self.result_flag.grid(row=0, column=0, sticky="ns", padx=(10, 0), pady=8)
        self.result_label = tk.Label(
            result_wrap,
            textvariable=self.result_var,
            anchor="w",
            justify="left",
            bg=efui.PANEL,
            fg=efui.INK,
            padx=10,
            pady=6,
            font=efui.ui(10),
        )
        self.result_label.grid(row=0, column=1, sticky="ew")

        actions = tk.Frame(bottom, bg=efui.PAPER)
        actions.grid(row=2, column=0, sticky="ew")
        self.generate_btn = efui.CutButton(actions, text="生成预览", command=self.generate_preview, height=34)
        self.generate_btn.grid(row=0, column=0, padx=(0, 8))
        self.start_btn = efui.CutButton(
            actions, text="开始发帖", command=self.start_posting, kind="primary", height=34
        )
        self.start_btn.grid(row=0, column=1, padx=(0, 8))
        self.stop_btn = efui.CutButton(actions, text="停止", command=self.stop_posting, height=34)
        self.stop_btn.configure(state="disabled")
        self.stop_btn.grid(row=0, column=2, padx=(0, 8))
        self.save_btn = efui.CutButton(actions, text="保存设置", command=self.save_settings_clicked, height=34)
        self.save_btn.grid(row=0, column=3, padx=(0, 8))
        self.captcha_btn = efui.CutButton(actions, text="验证码", command=self.captcha_settings_clicked, height=34)
        self.captcha_btn.grid(row=0, column=4, padx=(0, 8))
        efui.CutButton(actions, text="打开截图目录", command=self.open_screenshot_dir, height=34).grid(
            row=0, column=5, padx=(0, 8)
        )
        efui.CutButton(actions, text="清空日志", command=self.clear_log, height=34).grid(row=0, column=6)

        meta = tk.Frame(actions, bg=efui.PAPER)
        meta.grid(row=0, column=7, sticky="e")
        actions.columnconfigure(7, weight=1)
        self.status_label = tk.Label(
            meta,
            textvariable=self.status_var,
            bg=efui.PAPER,
            fg=efui.INK,
            font=efui.ui(10, "bold"),
            anchor="e",
        )
        self.status_label.grid(row=0, column=0, sticky="e")
        tk.Label(meta, text="│", bg=efui.PAPER, fg=efui.INK2, font=efui.mono(9)).grid(
            row=0, column=1, sticky="e", padx=8
        )
        self.engine_label = tk.Label(
            meta,
            textvariable=self.engine_label_var,
            bg=efui.PAPER,
            fg=efui.INK2,
            font=efui.mono(9),
            anchor="e",
        )
        self.engine_label.grid(row=0, column=2, sticky="e")

    # ----------------------------------------------------------------- state
    def _mode_key(self) -> str:
        return MODE_KEYS.get(self.mode_var.get().strip(), "template")

    def _engine_key(self) -> str:
        return ENGINE_KEYS.get(self.engine_var.get().strip(), "auto")

    def _apply_password_state(self) -> None:
        try:
            self.password_entry.configure(show="" if self.show_password_var.get() else "*")
        except Exception:
            pass

    def _apply_key_state(self) -> None:
        try:
            self.llm_key_entry.configure(show="" if self.show_key_var.get() else "*")
        except Exception:
            pass

    def _apply_captcha_key_state(self) -> None:
        entry = getattr(self, "captcha_key_entry", None)
        if entry is None:
            return
        try:
            entry.configure(show="" if self.show_captcha_key_var.get() else "*")
        except Exception:
            pass

    def _apply_mode_state(self) -> None:
        mode = self._mode_key()
        is_llm = mode == "llm"
        state = "normal" if is_llm else "disabled"
        for widget in getattr(self, "llm_widgets", []):
            try:
                widget.configure(state=state)
            except Exception:
                pass
        try:
            self.category_combo.configure(state="readonly" if mode == "template" else "disabled")
        except Exception:
            pass
        try:
            self.preset_pick_combo.configure(state="readonly" if mode == "preset" else "disabled")
        except Exception:
            pass
        try:
            self.preset_hint_label.configure(
                text=("已保存 " + str(len(self.preset_items)) + " 条预置内容") if self.preset_items else ""
            )
        except Exception:
            pass

    def _load_settings_to_ui(self) -> None:
        cfg = self.settings
        browser = cfg.get("browser") or {}
        posting = cfg.get("posting") or {}
        content = cfg.get("content") or {}
        llm = content.get("llm") or {}
        captcha = cfg.get("captcha") or {}

        self.engine_var.set(ENGINE_LABELS.get(browser.get("engine", "auto"), ENGINE_LABELS["auto"]))
        self.headless_var.set(bool(browser.get("headless", False)))
        self.keep_open_var.set(bool(browser.get("keep_browser_open", False)))

        self.mode_var.set(MODE_LABELS.get(content.get("mode", "template"), MODE_LABELS["template"]))
        self.category_var.set(str(content.get("template_category") or "日常寒暄"))
        self.llm_base_var.set(str(llm.get("base_url") or ""))
        self.llm_key_var.set(str(llm.get("api_key") or ""))
        self.llm_model_var.set(str(llm.get("model") or ""))
        self.llm_temp_var.set(str(llm.get("temperature", 0.8)))

        captcha_mode = str(captcha.get("mode") or "auto")
        self.captcha_mode_var.set(CAPTCHA_MODE_LABELS.get(captcha_mode, CAPTCHA_MODE_LABELS["auto"]))
        self.captcha_http_var.set(str(captcha.get("http_url") or ""))
        self.captcha_key_var.set(str(captcha.get("http_key") or ""))
        self.captcha_command_var.set(str(captcha.get("command") or ""))
        self.captcha_timeout_var.set(str(captcha.get("timeout_seconds") or 60))
        self.captcha_attempts_var.set(str(captcha.get("attempts", 2)))
        self.captcha_retry_var.set(str(captcha.get("retry_interval_seconds", 4)))
        self.captcha_cv_var.set(bool(captcha.get("auto_cv", True)))
        self.captcha_sysmouse_var.set(str(captcha.get("click_mode") or "system") != "browser")
        self.captcha_slider_var.set(bool(captcha.get("auto_slider", True)))
        self.captcha_ocr_var.set(bool(captcha.get("auto_ocr", True)))
        vision = captcha.get("vision") if isinstance(captcha.get("vision"), dict) else {}
        self.captcha_vision_var.set(bool(vision.get("enabled", True)))
        self.captcha_vision_base_var.set(str(vision.get("base_url") or ""))
        self.captcha_vision_key_var.set(str(vision.get("api_key") or ""))
        self.captcha_vision_model_var.set(str(vision.get("model") or ""))
        self.captcha_fallback_var.set(bool(captcha.get("fallback_manual", True)))

        self.community_var.set(str(posting.get("community") or ""))
        self.board_var.set(str(posting.get("board") or ""))
        self.repeat_var.set(str(posting.get("repeat_count", 1)))
        self.interval_var.set(str(posting.get("interval_seconds", 30)))
        self.retry_var.set(str(posting.get("retry_count", 2)))
        self.retry_interval_var.set(str(posting.get("retry_interval_seconds", 20)))
        self.regenerate_var.set(bool(posting.get("regenerate_on_retry", True)))
        self.verify_http_var.set(bool(posting.get("verify_via_http", True)))

        self.preset_items = [dict(item) for item in (content.get("preset_items") or []) if isinstance(item, dict)]
        pick = str(content.get("preset_pick") or "sequential")
        self.preset_pick_var.set("随机" if pick == "random" else "按顺序")

        self.remember_var.set(bool((cfg.get("security") or {}).get("remember_credentials", False)))
        self.account_var.set("")
        self.password_var.set("")
        self._apply_mode_state()
        self._load_saved_credentials()

    # -------------------------------------------------------------- preset
    def _load_saved_credentials(self) -> None:
        if not self.remember_var.get():
            return
        pair = secure_store.safe_load()
        if not pair:
            self._emit_log("warn", "已勾选记住账号密码，但本机没有可用凭据（可能更换过 Windows 用户）。")
            return
        account, password = pair
        self.account_var.set(account)
        self.password_var.set(password)
        self._emit_log("info", "已从本机加密凭据载入账号：" + account[:3] + "***")

    def edit_preset(self) -> None:
        if self.running:
            messagebox.showinfo("正在运行", "任务运行期间不能修改预置内容。")
            return
        win = tk.Toplevel(self.root)
        win.title("预置内容（每条一个标题 + 一篇正文）")
        win.configure(bg=efui.PANEL)
        win.transient(self.root)
        win.geometry("940x600")
        win.minsize(760, 480)
        items = [dict(item) for item in self.preset_items]
        state = {"index": -1}

        left = ttk.Frame(win, padding=(10, 10, 6, 10))
        left.pack(side="left", fill="y")
        ttk.Label(left, text="内容列表").pack(anchor="w")
        # 先把底部两排按钮 pack 进底部，再让列表占剩余空间——否则窗口一矮按钮就被挤没
        io_btns = ttk.Frame(left)
        io_btns.pack(side="bottom", fill="x", pady=(4, 0))
        btns = ttk.Frame(left)
        btns.pack(side="bottom", fill="x")
        list_wrap = ttk.Frame(left)
        list_wrap.pack(fill="both", expand=True, pady=(4, 6))
        lb = tk.Listbox(list_wrap, width=30, height=14, exportselection=False)
        scroll = ttk.Scrollbar(list_wrap, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=scroll.set)
        lb.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        right = ttk.Frame(win, padding=(6, 10, 10, 10))
        right.pack(side="left", fill="both", expand=True)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        ttk.Label(right, text="标题（5-30 字，留空则自动截取正文首行）").grid(row=0, column=0, sticky="w")
        title_var = tk.StringVar()
        ttk.Entry(right, textvariable=title_var).grid(row=1, column=0, sticky="ew", pady=(2, 8))
        ttk.Label(right, text="正文").grid(row=2, column=0, sticky="w")
        body_wrap = ttk.Frame(right)
        body_wrap.grid(row=3, column=0, sticky="nsew", pady=(2, 8))
        body_wrap.columnconfigure(0, weight=1)
        body_wrap.rowconfigure(0, weight=1)
        body_text = tk.Text(body_wrap, wrap="word", height=14, font=("Microsoft YaHei UI", 10))
        body_scroll = ttk.Scrollbar(body_wrap, orient="vertical", command=body_text.yview)
        body_text.configure(yscrollcommand=body_scroll.set)
        body_text.grid(row=0, column=0, sticky="nsew")
        body_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Label(
            right,
            text="发布时按列表顺序取用；“随机”模式下每次随机挑一条。",
            style="Hint.TLabel",
        ).grid(row=4, column=0, sticky="w")
        actions = ttk.Frame(right)
        actions.grid(row=5, column=0, sticky="e", pady=(8, 0))

        def load(index: int) -> None:
            title_var.set(str(items[index].get("title") or ""))
            body_text.delete("1.0", "end")
            body_text.insert("1.0", str(items[index].get("body") or ""))

        def commit() -> None:
            index = state["index"]
            if 0 <= index < len(items):
                items[index] = {
                    "title": title_var.get().strip(),
                    "body": body_text.get("1.0", "end-1c").strip(),
                }

        def refresh(select: Optional[int] = None) -> None:
            lb.delete(0, "end")
            for position, item in enumerate(items):
                text = str(item.get("title") or "").strip()[:20] or "（无标题）"
                lb.insert("end", str(position + 1) + ". " + text)
            if not items:
                state["index"] = -1
                title_var.set("")
                body_text.delete("1.0", "end")
                return
            target = 0 if select is None else max(0, min(int(select), len(items) - 1))
            lb.selection_clear(0, "end")
            lb.selection_set(target)
            lb.activate(target)
            state["index"] = target
            load(target)

        def on_select(_event=None) -> None:
            selection = lb.curselection()
            if not selection:
                return
            target = int(selection[0])
            if target == state["index"]:
                return
            commit()
            state["index"] = target
            load(target)

        def add_item() -> None:
            commit()
            items.append({"title": "", "body": ""})
            refresh(len(items) - 1)

        def remove_item() -> None:
            index = state["index"]
            if index < 0:
                return
            items.pop(index)
            refresh(min(index, max(0, len(items) - 1)))

        def move_item(delta: int) -> None:
            index = state["index"]
            target = index + delta
            if index < 0 or not (0 <= target < len(items)):
                return
            commit()
            items[index], items[target] = items[target], items[index]
            refresh(target)

        def save_preset() -> None:
            commit()
            cleaned = [
                item
                for item in items
                if str(item.get("body") or "").strip() or str(item.get("title") or "").strip()
            ]
            for item in cleaned:
                if not str(item.get("body") or "").strip():
                    messagebox.showwarning("正文为空", "每条预置内容都必须填写正文。", parent=win)
                    return
            if not cleaned:
                messagebox.showwarning("内容为空", "请至少添加一条预置内容。", parent=win)
                return
            self.preset_items = cleaned
            self._apply_mode_state()
            self._emit_log(
                "info", "预置内容已更新，共 " + str(len(cleaned)) + " 条；点“保存设置”可写入配置文件。"
            )
            win.destroy()

        ttk.Button(btns, text="新增", width=6, command=add_item).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="删除", width=6, command=remove_item).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="上移", width=6, command=lambda: move_item(-1)).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="下移", width=6, command=lambda: move_item(1)).pack(side="left")

        def preset_dir() -> str:
            """默认打开 exe 同目录的 presets 文件夹；没有就用打包内置的那份。"""
            for candidate in (app_dir() / "presets", resource_path("presets"), data_dir() / "presets"):
                try:
                    if candidate.is_dir():
                        return str(candidate)
                except OSError:
                    continue
            return str(app_dir())

        def import_file() -> None:
            commit()
            path = filedialog.askopenfilename(
                parent=win,
                title="导入预置内容（.json / .txt）",
                initialdir=preset_dir(),
                filetypes=[
                    ("预置内容", "*.json *.txt"),
                    ("JSON 文件", "*.json"),
                    ("文本文件", "*.txt"),
                    ("所有文件", "*.*"),
                ],
            )
            if not path:
                return
            try:
                loaded = load_preset_file(path)
            except ContentError as exc:
                messagebox.showwarning("导入失败", str(exc), parent=win)
                return
            except OSError as exc:
                messagebox.showwarning("读取失败", str(exc), parent=win)
                return
            append = messagebox.askyesno(
                "导入方式",
                "共读到 %d 条预置内容。\n\n是：追加到现有 %d 条后面\n否：清空并替换现有内容"
                % (len(loaded), len(items)),
                parent=win,
            )
            if append:
                items.extend(loaded)
                refresh(len(items) - len(loaded))
            else:
                items[:] = loaded
                refresh(0)
            self._emit_log("info", "已导入 %d 条预置内容：%s" % (len(loaded), path))

        def export_file() -> None:
            commit()
            if not items:
                messagebox.showinfo("没有内容", "当前没有任何预置内容。", parent=win)
                return
            path = filedialog.asksaveasfilename(
                parent=win,
                title="导出预置内容",
                initialdir=preset_dir(),
                defaultextension=".json",
                initialfile="预置内容.json",
                filetypes=[("JSON 文件", "*.json"), ("文本文件", "*.txt")],
            )
            if not path:
                return
            try:
                count = save_preset_file(items, path)
            except OSError as exc:
                messagebox.showwarning("导出失败", str(exc), parent=win)
                return
            messagebox.showinfo("已导出", "已导出 %d 条到：\n%s" % (count, path), parent=win)

        ttk.Button(io_btns, text="导入…", width=8, command=import_file).pack(side="left", padx=(0, 4))
        ttk.Button(io_btns, text="导出…", width=8, command=export_file).pack(side="left")

        lb.bind("<<ListboxSelect>>", on_select)
        ttk.Button(actions, text="保存", command=save_preset).pack(side="left", padx=4)
        ttk.Button(actions, text="取消", command=win.destroy).pack(side="left")

        refresh()
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.grab_set()
        win.focus_force()

    def _startup_log(self) -> None:
        self._emit_log("info", "程序启动，版本 " + __version__)
        self._emit_log("info", "配置文件：" + str(self.settings_path))
        self._emit_log("info", "数据目录：" + str(data_dir()))
        self._emit_log("info", "账号密码默认只保存在内存（可在“账号”里勾选加密保存）；LLM API Key 保存在配置文件中。")
        if self._config_error:
            self._emit_log("error", self._config_error)
            messagebox.showwarning("配置读取失败", self._config_error + "\n\n本次将使用默认配置。")
        if self._mode_key() == "template":
            try:
                post = generate_post("template", self.category_var.get(), self.topic_var.get())
                self._apply_preview(post)
                self._emit_log("info", "已自动生成一篇模板内容，可直接修改后发布。")
            except Exception as exc:
                self._emit_log("warn", "自动生成预览失败：" + str(exc))

    # ------------------------------------------------------------------ log
    def _write_file_log(self, level: str, line: str) -> None:
        try:
            with self._log_lock:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except Exception:
            pass

    def _emit_log(self, level: str, message: str) -> None:
        level = level if level in LEVEL_TAGS else "info"
        line = now_text() + "  [" + level.upper() + "] " + str(message)
        self._write_file_log(level, line)
        self.ui_queue.put(("log", level, line))

    def _append_log_line(self, level: str, line: str) -> None:
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n", LEVEL_TAGS.get(level, "info"))
            total = int(self.log_text.index("end-1c").split(".")[0])
            if total > 2600:
                self.log_text.delete("1.0", "800.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def clear_log(self) -> None:
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    # -------------------------------------------------------------- preview
    def _update_counts(self) -> None:
        try:
            title_len = len(self.title_var.get())
            body_len = len(self.body_text.get("1.0", "end-1c"))
            self.title_count_var.set(f"{title_len} / 30")
            self.body_count_var.set(f"{body_len} / 10000")
            title_bad = bool(title_len) and not (5 <= title_len <= 30)
            body_bad = body_len > 10000
            # 超出范围用强调色填充标出（不用红色：红色只留给删除）
            self.title_count_label.configure(
                foreground=efui.ON_SIGNAL if title_bad else efui.INK2,
                bg=efui.SIGNAL if title_bad else efui.PANEL,
            )
            self.body_count_label.configure(
                foreground=efui.ON_SIGNAL if body_bad else efui.INK2,
                bg=efui.SIGNAL if body_bad else efui.PANEL,
            )
        except Exception:
            pass

    def _apply_preview(self, post) -> None:
        self._set_preview_text(post.title, post.body)

    def _set_preview_text(self, title: str, body: str) -> None:
        self.title_var.set(title)
        self.body_text.delete("1.0", "end")
        self.body_text.insert("1.0", body)
        self._update_counts()

    def generate_preview(self) -> None:
        if self.running:
            messagebox.showinfo("正在运行", "任务运行期间不能重新生成预览，请先停止。")
            return
        mode = self._mode_key()
        if mode == "llm" and not self.llm_key_var.get().strip():
            messagebox.showwarning("缺少 API Key", "LLM 模式需要在“内容生成”中填写 API Key。")
            return
        try:
            cfg = self.collect_settings()
        except Exception as exc:
            messagebox.showerror("设置错误", str(exc))
            return
        mode = cfg["content"]["mode"]
        category = cfg["content"]["template_category"]
        topic = self.topic_var.get().strip()
        llm = copy.deepcopy(cfg["content"]["llm"])
        preset = {"items": copy.deepcopy(cfg["content"]["preset_items"]), "pick": cfg["content"]["preset_pick"]}
        label = {"llm": "LLM API", "preset": "手动预置内容"}.get(mode, "模板：" + category)
        self.generate_btn.configure(state="disabled")
        self.status_var.set("正在生成内容...")
        self._emit_log("info", "正在生成内容（" + label + "）")

        def worker() -> None:
            try:
                post = generate_post(mode, category, topic, llm, preset, 0)
                self.ui_queue.put(("preview_ready", post.title, post.body, post.category, True))
            except Exception as exc:
                self.ui_queue.put(("preview_error", str(exc)))

        threading.Thread(target=worker, daemon=True, name="preview-worker").start()

    # --------------------------------------------------------------- actions
    def collect_settings(self) -> Dict[str, Any]:
        cfg = copy.deepcopy(self.settings)
        cfg.setdefault("browser", {})["engine"] = self._engine_key()
        cfg["browser"]["headless"] = bool(self.headless_var.get())
        cfg["browser"]["keep_browser_open"] = bool(self.keep_open_var.get())

        cfg.setdefault("posting", {})["community"] = self.community_var.get().strip()
        cfg["posting"]["board"] = self.board_var.get().strip()
        cfg["posting"]["repeat_count"] = self._int_value(self.repeat_var, 1)
        cfg["posting"]["interval_seconds"] = self._int_value(self.interval_var, 30)
        cfg["posting"]["retry_count"] = self._int_value(self.retry_var, 2)
        cfg["posting"]["retry_interval_seconds"] = self._int_value(self.retry_interval_var, 20)
        cfg["posting"]["regenerate_on_retry"] = bool(self.regenerate_var.get())
        cfg["posting"]["verify_via_http"] = bool(self.verify_http_var.get())
        cfg["posting"]["cover_mode"] = 1

        cfg.setdefault("content", {})["mode"] = self._mode_key()
        cfg["content"]["template_category"] = self.category_var.get().strip() or "日常寒暄"
        cfg["content"]["preset_items"] = [dict(item) for item in self.preset_items if isinstance(item, dict)]
        cfg["content"]["preset_pick"] = "random" if self.preset_pick_var.get() == "随机" else "sequential"
        llm = cfg["content"].setdefault("llm", {})
        llm["base_url"] = self.llm_base_var.get().strip()
        llm["api_key"] = self.llm_key_var.get().strip()
        llm["model"] = self.llm_model_var.get().strip()
        try:
            llm["temperature"] = float(self.llm_temp_var.get().strip())
        except (TypeError, ValueError):
            llm["temperature"] = 0.8

        captcha = cfg.setdefault("captcha", {})
        captcha["mode"] = CAPTCHA_MODE_KEYS.get(self.captcha_mode_var.get().strip(), "auto")
        captcha["http_url"] = self.captcha_http_var.get().strip()
        captcha["http_key"] = self.captcha_key_var.get().strip()
        captcha["command"] = self.captcha_command_var.get().strip()
        captcha["timeout_seconds"] = self._int_value(self.captcha_timeout_var, 60)
        captcha["attempts"] = self._int_value(self.captcha_attempts_var, 2)
        captcha["retry_interval_seconds"] = self._int_value(self.captcha_retry_var, 4)
        captcha["auto_cv"] = bool(self.captcha_cv_var.get())
        captcha["click_mode"] = "system" if self.captcha_sysmouse_var.get() else "browser"
        captcha["auto_slider"] = bool(self.captcha_slider_var.get())
        captcha["auto_ocr"] = bool(self.captcha_ocr_var.get())
        vision = captcha.setdefault("vision", {})
        vision["enabled"] = bool(self.captcha_vision_var.get())
        vision["base_url"] = self.captcha_vision_base_var.get().strip()
        vision["api_key"] = self.captcha_vision_key_var.get().strip()
        vision["model"] = self.captcha_vision_model_var.get().strip()
        captcha["fallback_manual"] = bool(self.captcha_fallback_var.get())

        cfg.setdefault("security", {})["remember_credentials"] = bool(self.remember_var.get())
        return normalize_settings(cfg)

    @staticmethod
    def _int_value(var: tk.StringVar, default: int) -> int:
        try:
            return int(float(str(var.get()).strip()))
        except (TypeError, ValueError):
            return default

    def save_settings_clicked(self) -> None:
        if self.running:
            messagebox.showinfo("正在运行", "任务运行期间不能修改并保存设置。")
            return
        try:
            cfg = self.collect_settings()
            self.settings_path = save_settings(cfg, self.settings_path)
            self.settings = cfg
            self.interval_var.set(str(cfg["posting"]["interval_seconds"]))
            self.repeat_var.set(str(cfg["posting"]["repeat_count"]))
            self._emit_log("success", "设置已保存：" + str(self.settings_path))
            messagebox.showinfo("保存成功", "设置已保存（不包含账号密码）：\n" + str(self.settings_path))
        except Exception as exc:
            self._emit_log("error", "保存设置失败：" + str(exc))
            messagebox.showerror("保存失败", str(exc))

    def captcha_settings_clicked(self) -> None:
        if self.running:
            messagebox.showinfo("正在运行", "任务运行期间不能修改验证码设置。")
            return
        win = tk.Toplevel(self.root)
        win.title("验证码外接设置")
        win.configure(bg=efui.PANEL)
        win.transient(self.root)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=(
                "默认“自动尝试”：先模拟滑块拖动，再尝试图片验证码 OCR（需 ddddocr），"
                "配置了外接服务则优先调用；全部失败后按“转为人工接管”处理。\n"
                "短信验证、人脸识别、身份验证无法自动完成，会直接转人工。"
            ),
            style="Hint.TLabel",
            wraplength=620,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        mode_var = tk.StringVar(value=self.captcha_mode_var.get())
        url_var = tk.StringVar(value=self.captcha_http_var.get())
        key_var = tk.StringVar(value=self.captcha_key_var.get())
        command_var = tk.StringVar(value=self.captcha_command_var.get())
        timeout_var = tk.StringVar(value=self.captcha_timeout_var.get())
        fallback_var = tk.BooleanVar(value=self.captcha_fallback_var.get())
        show_key_var = tk.BooleanVar(value=self.show_captcha_key_var.get())

        ttk.Label(frame, text="验证方式").grid(row=1, column=0, sticky="w", pady=3)
        mode_combo = ttk.Combobox(
            frame, textvariable=mode_var, values=list(CAPTCHA_MODE_LABELS.values()), state="readonly", width=26
        )
        mode_combo.grid(row=1, column=1, columnspan=2, sticky="w", pady=3)

        ttk.Label(frame, text="HTTP 地址").grid(row=2, column=0, sticky="w", pady=3)
        url_entry = ttk.Entry(frame, textvariable=url_var)
        url_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=3)

        ttk.Label(frame, text="HTTP Key").grid(row=3, column=0, sticky="w", pady=3)
        key_entry = ttk.Entry(frame, textvariable=key_var, show="*")
        key_entry.grid(row=3, column=1, sticky="ew", pady=3)
        show_key_check = ttk.Checkbutton(
            frame,
            text="显示",
            variable=show_key_var,
            command=lambda: key_entry.configure(show="" if show_key_var.get() else "*"),
        )
        show_key_check.grid(row=3, column=2, sticky="w", padx=(6, 0), pady=3)

        ttk.Label(frame, text="外接程序").grid(row=4, column=0, sticky="w", pady=3)
        command_entry = ttk.Entry(frame, textvariable=command_var)
        command_entry.grid(row=4, column=1, sticky="ew", pady=3)
        browse_btn = ttk.Button(
            frame,
            text="浏览...",
            command=lambda: command_var.set(
                filedialog.askopenfilename(
                    parent=win,
                    title="选择外接验证程序",
                    filetypes=[("可执行程序", "*.exe *.bat *.cmd"), ("所有文件", "*.*")],
                )
                or command_var.get()
            ),
        )
        browse_btn.grid(row=4, column=2, sticky="w", padx=(6, 0), pady=3)

        attempts_var = tk.StringVar(value=self.captcha_attempts_var.get())
        retry_var = tk.StringVar(value=self.captcha_retry_var.get())
        cv_var = tk.BooleanVar(value=self.captcha_cv_var.get())
        sysmouse_var = tk.BooleanVar(value=self.captcha_sysmouse_var.get())
        slider_var = tk.BooleanVar(value=self.captcha_slider_var.get())
        ocr_var = tk.BooleanVar(value=self.captcha_ocr_var.get())

        ttk.Label(frame, text="超时（秒）").grid(row=5, column=0, sticky="w", pady=3)
        ttk.Spinbox(frame, from_=5, to=300, increment=5, textvariable=timeout_var, width=8).grid(
            row=5, column=1, sticky="w", pady=3
        )

        auto_row = ttk.Frame(frame)
        auto_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Label(auto_row, text="自动尝试").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(auto_row, from_=1, to=10, increment=1, textvariable=attempts_var, width=4).grid(
            row=0, column=1, sticky="w", padx=(6, 4)
        )
        ttk.Label(auto_row, text="次，间隔").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(auto_row, from_=1, to=120, increment=1, textvariable=retry_var, width=4).grid(
            row=0, column=3, sticky="w", padx=(6, 4)
        )
        ttk.Label(auto_row, text="秒").grid(row=0, column=4, sticky="w")

        auto_checks = ttk.Frame(frame)
        auto_checks.grid(row=7, column=0, columnspan=3, sticky="w", pady=(2, 0))
        ttk.Checkbutton(auto_checks, text="离线识图（颜色+形状）", variable=cv_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(auto_checks, text="真实鼠标点击", variable=sysmouse_var).grid(row=0, column=3, sticky="w", padx=(10, 0))
        ttk.Checkbutton(auto_checks, text="模拟滑块拖动", variable=slider_var).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Checkbutton(auto_checks, text="图片验证码 OCR（需 ddddocr）", variable=ocr_var).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )

        # ---- 浏览器窗口：真实鼠标点击的“抓手” ----
        capture_row = ttk.Frame(auto_checks)
        capture_row.grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        window_status = tk.StringVar(
            value=("已锁定 hwnd=%d" % int(getattr(self, "locked_hwnd", 0) or 0))
            if getattr(self, "locked_hwnd", 0)
            else "自动识别（按页面标题 + 进程找窗口）"
        )

        def capture_window() -> None:
            messagebox.showinfo(
                "捕捉浏览器窗口",
                "点“确定”后本窗口会最小化。\n"
                "请在 5 秒内把鼠标移到**浏览器窗口的网页内容区**上并停住，不要移动。\n"
                "5 秒后程序会读取鼠标位置所在的窗口并锁定它（真实鼠标点击会用它）。",
                parent=win,
            )
            try:
                self.root.iconify()
            except Exception:
                pass
            try:
                win.withdraw()
            except Exception:
                pass

            def finish() -> None:
                hwnd = 0
                try:
                    hwnd = int(sysmouse.capture_under_cursor() or 0)
                except Exception as exc:
                    self._emit_log("warn", "捕捉窗口失败：" + str(exc))
                try:
                    self.root.deiconify()
                    win.deiconify()
                    win.lift()
                except Exception:
                    pass
                if hwnd:
                    self.locked_hwnd = hwnd
                    title = sysmouse._window_title(hwnd)
                    window_status.set("已锁定：" + (title[:26] or ("hwnd=%d" % hwnd)))
                    self._emit_log("info", "已捕捉浏览器窗口 hwnd=%d，标题：%s" % (hwnd, title or "（无标题）"))
                else:
                    window_status.set("未捕捉到浏览器窗口，请重试")
                    self._emit_log(
                        "warn",
                        "捕捉失败：鼠标下不是浏览器窗口。请把鼠标放到浏览器网页内容区域（不是桌面/其它程序）再试一次。",
                    )

            self.root.after(5000, finish)

        def clear_window() -> None:
            self.locked_hwnd = 0
            window_status.set("自动识别（按页面标题 + 进程找窗口）")
            self._emit_log("info", "已取消窗口锁定，改为自动识别浏览器窗口。")

        ttk.Button(capture_row, text="捕捉浏览器窗口", command=capture_window).grid(row=0, column=0, sticky="w")
        ttk.Button(capture_row, text="取消锁定", command=clear_window).grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Label(capture_row, textvariable=window_status, style="Hint.TLabel").grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )

        vision_box = ttk.LabelFrame(frame, text="图形点选（按文字找图形 / 数美空间逻辑验证）", padding=8)
        vision_box.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        vision_box.columnconfigure(1, weight=1)
        vision_enabled_var = tk.BooleanVar(value=self.captcha_vision_var.get())
        vision_base_var = tk.StringVar(value=self.captcha_vision_base_var.get())
        vision_key_var = tk.StringVar(value=self.captcha_vision_key_var.get())
        vision_model_var = tk.StringVar(value=self.captcha_vision_model_var.get())
        ttk.Checkbutton(
            vision_box,
            text="用视觉模型识别（需要支持图片的模型，如 gpt-4o / qwen-vl-max / glm-4v）",
            variable=vision_enabled_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(vision_box, text="Base URL").grid(row=1, column=0, sticky="w", pady=1)
        ttk.Entry(vision_box, textvariable=vision_base_var).grid(row=1, column=1, sticky="ew", pady=1)
        ttk.Label(vision_box, text="API Key").grid(row=2, column=0, sticky="w", pady=1)
        ttk.Entry(vision_box, textvariable=vision_key_var, show="*").grid(row=2, column=1, sticky="ew", pady=1)
        ttk.Label(vision_box, text="模型名称").grid(row=3, column=0, sticky="w", pady=1)
        ttk.Entry(vision_box, textvariable=vision_model_var).grid(row=3, column=1, sticky="ew", pady=1)
        llm_cfg = (self.settings.get("content") or {}).get("llm") or {}
        inherit_base = self.captcha_vision_base_var.get().strip() or str(llm_cfg.get("base_url") or "")
        inherit_model = self.captcha_vision_model_var.get().strip() or str(llm_cfg.get("model") or "")
        has_key = bool(self.captcha_vision_key_var.get().strip() or llm_cfg.get("api_key"))
        inherit_text = (
            "留空 = 复用“02 CONTENT 内容生成”：地址 " + (inherit_base or "（未填）")
            + "，模型 " + (inherit_model or "（未填）")
            + "；Key " + ("已配置" if has_key else "未配置 —— 视觉模型会被自动跳过，不影响离线识图")
        )
        ttk.Label(
            vision_box,
            text=inherit_text,
            style="Hint.TLabel",
            wraplength=600,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))

        fallback_check = ttk.Checkbutton(
            frame,
            text="自动/外接失败后转为人工接管（关闭则直接报错停止）",
            variable=fallback_var,
        )
        fallback_check.grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 4))

        ttk.Label(
            frame,
            text="外接程序必须从 stdin 读取 JSON，向 stdout 输出 JSON，例如 {\"points\": [[120, 80]]}。",
            style="Hint.TLabel",
            wraplength=620,
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(4, 8))

        buttons = ttk.Frame(frame)
        buttons.grid(row=11, column=0, columnspan=3, sticky="e", pady=(4, 0))

        def update_state(*_args) -> None:
            mode = CAPTCHA_MODE_KEYS.get(mode_var.get().strip(), "auto")
            http_state = "normal" if mode in ("auto", "http") else "disabled"
            cmd_state = "normal" if mode in ("auto", "command") else "disabled"
            for widget in (url_entry, key_entry, show_key_check):
                try:
                    widget.configure(state=http_state)
                except Exception:
                    pass
            for widget in (command_entry, browse_btn):
                try:
                    widget.configure(state=cmd_state)
                except Exception:
                    pass

        def save() -> None:
            mode = CAPTCHA_MODE_KEYS.get(mode_var.get().strip(), "auto")
            url = url_var.get().strip()
            command = command_var.get().strip()
            if mode == "http" and not url.startswith(("http://", "https://")):
                messagebox.showwarning("地址无效", "外接 HTTP 地址必须以 http:// 或 https:// 开头。", parent=win)
                return
            if mode == "command" and not command:
                messagebox.showwarning("缺少程序", "请选择外接验证程序。", parent=win)
                return
            try:
                timeout = int(float(timeout_var.get().strip()))
            except (TypeError, ValueError):
                timeout = 60
            timeout = max(5, min(300, timeout))
            self.captcha_mode_var.set(CAPTCHA_MODE_LABELS[mode])
            self.captcha_http_var.set(url)
            self.captcha_key_var.set(key_var.get().strip())
            self.captcha_command_var.set(command)
            self.captcha_timeout_var.set(str(timeout))
            try:
                attempts = max(1, min(10, int(float(attempts_var.get().strip()))))
            except (TypeError, ValueError):
                attempts = 2
            try:
                retry = max(1, min(120, int(float(retry_var.get().strip()))))
            except (TypeError, ValueError):
                retry = 4
            self.captcha_attempts_var.set(str(attempts))
            self.captcha_retry_var.set(str(retry))
            self.captcha_cv_var.set(bool(cv_var.get()))
            self.captcha_sysmouse_var.set(bool(sysmouse_var.get()))
            self.captcha_slider_var.set(bool(slider_var.get()))
            self.captcha_ocr_var.set(bool(ocr_var.get()))
            self.captcha_vision_var.set(bool(vision_enabled_var.get()))
            self.captcha_vision_base_var.set(vision_base_var.get().strip())
            self.captcha_vision_key_var.set(vision_key_var.get().strip())
            self.captcha_vision_model_var.set(vision_model_var.get().strip())
            self.captcha_fallback_var.set(bool(fallback_var.get()))
            self.show_captcha_key_var.set(bool(show_key_var.get()))
            self._emit_log("info", "验证码设置已更新：" + CAPTCHA_MODE_LABELS[mode])
            win.destroy()

        def test_vision() -> None:
            clone = copy.deepcopy(self.settings)
            clone.setdefault("captcha", {})["vision"] = {
                "enabled": True,
                "base_url": vision_base_var.get().strip(),
                "api_key": vision_key_var.get().strip(),
                "model": vision_model_var.get().strip(),
            }
            clone.setdefault("content", {}).setdefault("llm", {})
            solver = CaptchaSolver(normalize_settings(clone), self._emit_log)
            if not solver.vision_key or not solver.vision_model:
                messagebox.showwarning(
                    "缺少配置",
                    "请填写视觉模型的 API Key 与模型名（或先在“02 CONTENT 内容生成”里配置 LLM）。",
                    parent=win,
                )
                return
            sample = resource_path("assets/captcha-sample.jpg")
            if not sample.exists():
                messagebox.showwarning("缺少示例图", "没有找到 assets/captcha-sample.jpg。", parent=win)
                return
            test_btn.configure(state="disabled", text="识别中...")

            def run() -> None:
                try:
                    points = solver._ask_vision(
                        sample.read_bytes(), "请依次点击：绿色圆柱、红色正方体", 600, 300
                    )
                    if points:
                        text = "返回 " + str(len(points)) + " 个点（归一化坐标）：\n" + "\n".join(
                            "(%0.3f, %0.3f)" % item for item in points
                        )
                    else:
                        text = "模型没有返回可用坐标。请确认该模型支持图片输入（如 gpt-4o、qwen-vl-max、glm-4v）。"
                except Exception as exc:
                    text = "调用失败：" + str(exc)

                def show() -> None:
                    test_btn.configure(state="normal", text="测试识别")
                    messagebox.showinfo("视觉模型测试", text, parent=win)

                self.root.after(0, show)

            threading.Thread(target=run, daemon=True, name="vision-test").start()

        test_btn = ttk.Button(buttons, text="测试识别", command=test_vision)
        test_btn.grid(row=0, column=0, padx=4)
        ttk.Button(buttons, text="保存", command=save).grid(row=0, column=1, padx=4)
        ttk.Button(buttons, text="取消", command=win.destroy).grid(row=0, column=2, padx=4)
        mode_combo.bind("<<ComboboxSelected>>", update_state)
        update_state()
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.update_idletasks()
        try:
            x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - win.winfo_width()) // 2)
            y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - win.winfo_height()) // 2)
            win.geometry("+%d+%d" % (x, y))
        except Exception:
            pass
        win.grab_set()
        win.focus_force()

    def start_posting(self) -> None:
        if self.running:
            return
        account = self.account_var.get().strip()
        password = self.password_var.get()
        if not account or not password:
            messagebox.showwarning("缺少账号或密码", "请填写易班账号和密码。账号密码只保存在内存，不会写入配置文件。")
            return
        title = self.title_var.get().strip()
        body = self.body_text.get("1.0", "end-1c").strip()
        if not title or not body:
            messagebox.showwarning("内容为空", "请先点击“生成预览”，或手动填写标题和正文。")
            return
        try:
            validate_post(title, body)
        except ContentError as exc:
            messagebox.showwarning("内容不符合要求", str(exc))
            return

        try:
            cfg = self.collect_settings()
        except Exception as exc:
            messagebox.showerror("设置错误", str(exc))
            return
        for label, key in (("微社区", "community"), ("版块", "board")):
            value = str(cfg.get("posting", {}).get(key) or "").strip()
            if value and ("/" in value or "\\" in value or "%" in value or "http" in value.lower()):
                messagebox.showwarning(
                    "发帖范围填写有误",
                    "“" + label + "”请填写页面上的名称（例如：XX学院微社区），不要粘贴网址或编码后的路径。\n\n"
                    "留空表示使用页面默认的第一个微社区 / 版块。",
                )
                return
        if cfg["content"]["mode"] == "llm" and not cfg["content"]["llm"]["api_key"]:
            messagebox.showwarning("缺少 API Key", "LLM 模式需要 API Key；如果只想发当前预览，请切换到模板模式，或先填写 Key。")
            return
        captcha_cfg = cfg.get("captcha") or {}
        captcha_mode = str(captcha_cfg.get("mode") or "manual")
        if captcha_mode == "http" and not str(captcha_cfg.get("http_url") or "").strip():
            messagebox.showwarning("缺少外接验证地址", "验证码模式为“外接 HTTP 服务”，请先在“验证码”设置中填写服务地址。")
            return
        if captcha_mode == "command" and not str(captcha_cfg.get("command") or "").strip():
            messagebox.showwarning("缺少外接验证程序", "验证码模式为“外接程序”，请先在“验证码”设置中选择程序路径。")
            return
        try:
            self.settings_path = save_settings(cfg, self.settings_path)
            self.settings = cfg
        except Exception as exc:
            self._emit_log("warn", "启动前保存设置失败（不影响本次运行）：" + str(exc))

        if bool(cfg.get("security", {}).get("remember_credentials", False)):
            try:
                secure_store.save_credentials(account, password)
                self._emit_log("info", "已用 Windows DPAPI 加密保存账号密码（仅当前 Windows 用户可解密）。")
            except Exception as exc:
                self._emit_log("warn", "保存账号密码失败：" + str(exc))
        else:
            try:
                if secure_store.has_credentials():
                    secure_store.clear_credentials()
                    self._emit_log("info", "已清除本机以前保存的账号密码。")
            except Exception:
                pass

        self.interval_var.set(str(cfg["posting"]["interval_seconds"]))
        self.repeat_var.set(str(cfg["posting"]["repeat_count"]))
        summary = self.summary_var.get().strip()[:200]
        mode = cfg["content"]["mode"]
        category = cfg["content"]["template_category"]
        topic = self.topic_var.get().strip()
        repeat = int(cfg["posting"]["repeat_count"])
        interval = int(cfg["posting"]["interval_seconds"])
        llm = copy.deepcopy(cfg["content"]["llm"])
        preset = {
            "items": copy.deepcopy(cfg["content"].get("preset_items") or []),
            "pick": str(cfg["content"].get("preset_pick") or "sequential"),
        }
        if mode == "preset" and not preset["items"]:
            messagebox.showwarning("预置内容为空", "当前是“手动预置内容”模式，请先点“预置内容…”添加至少一条。")
            return

        # 上次勾选“发完保持浏览器”时可能留下一个仍在运行的浏览器；同一 profile 不能重复启动。
        if self.engine is not None:
            self._emit_log("info", "检测到上次保留的浏览器，先关闭以便重新启动。")
            try:
                self.engine.stop()
            except Exception as exc:
                self._emit_log("warn", "关闭遗留浏览器时出错：" + str(exc))
            self.engine = None

        self.stop_event.clear()
        self.manual_event.clear()
        self.manual_result = False
        self._last_screenshot = ""
        self.running = True
        self.generate_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.save_btn.configure(state="disabled")
        self.manual_frame.grid_remove()
        self.engine_label_var.set("引擎：启动中...")
        self._set_result("任务运行中：正在启动浏览器，请勿关闭窗口。", "info")
        self.status_var.set("正在启动浏览器...")
        self._emit_log("info", "-" * 48)
        self._emit_log(
            "info",
            "开始任务：模式="
            + {"llm": "LLM", "preset": "预置内容"}.get(mode, "模板/" + category)
            + "，篇数=" + str(repeat) + "，间隔=" + str(interval) + " 秒"
            + "，失败重试=" + str(cfg["posting"].get("retry_count", 0)) + " 次",
        )

        self.worker = threading.Thread(
            target=self._run_worker,
            args=(account, password, title, body, summary, mode, category, topic, repeat, interval, llm, preset, cfg),
            daemon=True,
            name="yiban-worker",
        )
        self.worker.start()

    def stop_posting(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.manual_result = False
        self.manual_event.set()
        self.stop_btn.configure(state="disabled")
        self.status_var.set("正在停止...")
        self._emit_log("warn", "已请求停止，正在等待当前步骤结束。")
        self._set_result("正在停止任务，请稍候...", "warn")

    # ---------------------------------------------------------------- worker
    def _run_worker(
        self,
        account: str,
        password: str,
        first_title: str,
        first_body: str,
        first_summary: str,
        mode: str,
        category: str,
        topic: str,
        repeat: int,
        interval: int,
        llm: Dict[str, Any],
        preset: Dict[str, Any],
        cfg: Dict[str, Any],
    ) -> None:
        engine = None
        keep_open = bool(cfg["browser"].get("keep_browser_open", False))
        try:
            engine = create_engine(cfg, self._emit_log)
            self.engine = engine
            engine.bind_stop(self.stop_event)
            self.ui_queue.put(("status", "正在启动浏览器..."))
            engine.start()
            engine_name = getattr(engine, "engine_name", None) or getattr(engine, "name", "unknown")
            self.ui_queue.put(("engine", str(engine_name)))
            locked = int(getattr(self, "locked_hwnd", 0) or 0)
            if locked:
                if engine.lock_window(locked):
                    self._emit_log("info", "使用手动锁定的浏览器窗口（hwnd=%d）。" % locked)
                else:
                    self._emit_log("warn", "锁定的浏览器窗口已失效（浏览器可能重启过），改为自动识别。")
                    self.locked_hwnd = 0
            flow = YibanFlow(engine, cfg, self._emit_log, self._manual_gate, self._thread_status)

            preset_cfg = preset if isinstance(preset, dict) else {"items": [], "pick": "sequential"}
            retry_count = int(cfg["posting"].get("retry_count", 2))
            retry_interval = int(cfg["posting"].get("retry_interval_seconds", 20))
            regenerate = bool(cfg["posting"].get("regenerate_on_retry", True))

            for index in range(repeat):
                if self.stop_event.is_set():
                    raise StopRequested()
                if index == 0:
                    title, body, summary = first_title, first_body, first_summary
                else:
                    self.ui_queue.put(("status", "正在生成第 " + str(index + 1) + " 篇内容..."))
                    self._emit_log("info", "正在生成第 " + str(index + 1) + " 篇内容")
                    post = generate_post(mode, category, topic, llm, preset_cfg, index)
                    title, body, summary = post.title, post.body, ""
                    self.ui_queue.put(("preview_ready", post.title, post.body, post.category, True))

                attempt = 0
                while True:
                    if self.stop_event.is_set():
                        raise StopRequested()
                    label = "第 " + str(index + 1) + "/" + str(repeat) + " 篇"
                    if attempt:
                        label += "（第 " + str(attempt + 1) + " 次尝试）"
                    self._emit_log("info", label + "，标题：" + title)
                    result = flow.publish(account, password, title, body, summary)
                    if result.ok:
                        self.ui_queue.put(("post_success", result, index + 1, repeat))
                        break
                    if attempt >= retry_count:
                        self.ui_queue.put(("post_failure", result, index + 1, repeat))
                        break
                    attempt += 1
                    self._emit_log(
                        "warn",
                        "发布失败：" + str(result.message) + "；"
                        + str(retry_interval) + " 秒后重试（" + str(attempt) + "/" + str(retry_count) + "）",
                    )
                    self.ui_queue.put(
                        ("status", "发布失败，准备第 " + str(attempt) + "/" + str(retry_count) + " 次重试")
                    )
                    if regenerate:
                        try:
                            post = generate_post(mode, category, topic, llm, preset_cfg, index + attempt)
                            title, body, summary = post.title, post.body, ""
                            self.ui_queue.put(("preview_ready", post.title, post.body, post.category, True))
                            self._emit_log("info", "已换一版内容重试：" + title)
                        except Exception as exc:
                            self._emit_log("warn", "生成新内容失败，沿用原内容重试：" + str(exc))
                    engine.wait(retry_interval)
                if not result.ok:
                    break

                if index < repeat - 1:
                    self.ui_queue.put(("status", "等待发帖间隔 " + str(interval) + " 秒..."))
                    self._emit_log("info", "等待 " + str(interval) + " 秒后发布下一篇（平台频率限制保护）。")
                    engine.wait(interval)

        except StopRequested:
            self._emit_log("warn", "任务已停止。")
            self.ui_queue.put(("result", "任务已停止。", "warn"))
            self.ui_queue.put(("status", "已停止"))
        except Exception as exc:
            self._emit_log("error", "任务异常：" + str(exc))
            self._emit_log("error", traceback.format_exc(limit=6))
            self.ui_queue.put(("result", "任务失败：" + truncate(str(exc), 180), "error"))
            self.ui_queue.put(("status", "任务失败"))
        finally:
            if engine is not None:
                if keep_open:
                    self._emit_log("info", "已按设置保持浏览器打开；关闭本工具不会自动关闭浏览器。")
                else:
                    self._emit_log("info", "正在关闭浏览器...")
                    try:
                        engine.stop()
                    except Exception as exc:
                        self._emit_log("warn", "关闭浏览器时出错：" + str(exc))
                if not keep_open:
                    self.engine = None
            self.ui_queue.put(("finished",))

    # ---------------------------------------------------------------- manual
    def _manual_gate(self, message: str, timeout: float) -> bool:
        self.manual_result = False
        self._manual_failure_reason = ""
        self.manual_event.clear()
        self.manual_focus_event.clear()
        self.ui_queue.put(("manual_request", message, timeout))
        # 验证出现时工具窗口经常正好挡住浏览器。自动让位：先最小化工具，
        # 把 Edge 露出来；验证完成（或失败）后 _hide_manual 会自动恢复。
        self.ui_queue.put(("manual_focus",))
        deadline = time.time() + max(1.0, float(timeout))
        next_alive_check = time.time() + 1.5
        next_challenge_check = 0.0
        next_url_check = time.time() + 1.2
        seen_challenge = False
        last_challenge_seen = 0.0
        # 注意：不能只在进入等待的瞬间判断验证框是否存在。真实发帖时，
        # 110 响应可能先到、Shumei DOM 稍后才渲染，静态判断会让 seen_challenge
        # 永远为 False，用户完成验证后工具还是傻等。这里改成动态观察：
        # 只要本次等待期间见过验证框，后续它消失 1 秒就自动继续。
        while time.time() < deadline:
            if self.stop_event.is_set():
                break
            if self.manual_focus_event.is_set():
                self.manual_focus_event.clear()
                self._bring_browser_forward()
            if self.manual_event.wait(0.2):
                break
            now = time.time()
            if now >= next_alive_check:
                next_alive_check = now + 2.0
                alive = True
                try:
                    if self.engine is not None:
                        alive = bool(self.engine.is_alive())
                except Exception:
                    alive = False
                if not alive:
                    self.manual_result = False
                    self._manual_failure_reason = "浏览器已关闭或调试连接已断开"
                    self._emit_log("error", "浏览器已关闭或调试连接已断开，停止等待人工验证。")
                    break
            if now >= next_url_check:
                next_url_check = now + 1.5
                try:
                    url = str(self.engine.current_url()) if self.engine is not None else ""
                except Exception:
                    url = ""
                # 用户可能在我们刚进入等待前就完成了验证，SPA 已经跳到“我的帖子”。
                if "/bbs/my/posts" in url:
                    self._emit_log("info", "检测到帖子已发布并跳到我的帖子，自动继续。")
                    self.manual_result = True
                    self.manual_event.set()
                    break
            if now >= next_challenge_check:
                next_challenge_check = now + 0.6
                visible = True
                try:
                    if self.engine is not None:
                        visible = bool(
                            self.engine.exists(SHUMEI_POPUP_SELECTOR, visible=True)
                            or self.engine.exists(IMAGE_CAPTCHA_SELECTOR, visible=True)
                        )
                except Exception:
                    visible = True
                if visible:
                    if not seen_challenge:
                        self._emit_log("info", "检测到安全验证框；完成后会自动继续，无需再点按钮。")
                    seen_challenge = True
                    last_challenge_seen = now
                elif seen_challenge and (now - last_challenge_seen) >= 1.0:
                    self._emit_log("info", "检测到安全验证框已消失，自动继续。")
                    self.manual_result = True
                    self.manual_event.set()
                    break
        self.ui_queue.put(("manual_close",))
        return bool(self.manual_result) and not self.stop_event.is_set()

    def focus_browser(self) -> None:
        # “打开浏览器”是用户明确要把浏览器露出来：先把工具窗口最小化，
        # 这样即使 Windows 前台切换策略不生效，验证码也不会被工具挡住。
        try:
            if self.root.state() != "iconic":
                self._minimized_for_manual = True
                self.root.iconify()
                self._emit_log("info", "已最小化工具窗口，验证完成后会自动恢复。")
        except Exception:
            pass
        self.manual_focus_event.set()
        self._emit_log("info", "正在把浏览器切到前台...")

    def _bring_browser_forward(self) -> None:
        try:
            if self.engine is not None:
                self.engine.bring_to_front()
                self._emit_log("info", "已将浏览器切到前台。")
        except Exception as exc:
            self._emit_log("warn", "切换浏览器到前台失败：" + str(exc))

    def complete_manual(self) -> None:
        self.manual_result = True
        self.manual_event.set()
        self.manual_ok_btn.configure(state="disabled")
        self._emit_log("info", "用户确认已手动完成验证，继续执行。")

    def cancel_manual(self) -> None:
        self.manual_result = False
        self.manual_event.set()
        self.manual_cancel_btn.configure(state="disabled")
        self._emit_log("warn", "用户取消了本次人工验证。")

    def _flash_taskbar(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD),
                ]

            hwnd = int(self.root.winfo_id())
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x00000003 | 0x0000000C, 0, 0)
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    def _show_manual(self, message: str, timeout: float) -> None:
        self.manual_msg_var.set(
            message.strip()
            + "\n工具会自动让出屏幕并把浏览器切到前台；验证框消失后自动继续。"
            + "若没有自动继续，确认验证框已消失后再点“已完成，继续”（最多 "
            + str(int(timeout)) + " 秒）。"
        )
        self.manual_focus_btn.configure(state="normal")
        self.manual_ok_btn.configure(state="normal")
        self.manual_cancel_btn.configure(state="normal")
        self.manual_frame.grid()
        try:
            # 先清掉上一次的固定行高，并让窗口真正完成一次布局，
            # 否则启动早期/首次弹出时控件宽度还是 1，会把文字排成很多行。
            self.root.rowconfigure(2, minsize=0)
            self.manual_label.configure(wraplength=900)
            self.root.update()
            width = int(self.manual_frame.winfo_width())
            if width < 400:
                width = int(self.root.winfo_screenwidth())
            wrap = max(520, width - 44)
            self.manual_label.configure(wraplength=wrap)
            self.root.update_idletasks()
            # 提示面板出现时保证这一行有足够高度；否则 Tk 会优先压缩无权重行，
            # 在 150% 缩放的小屏上只显示一行提示。
            self.root.rowconfigure(2, minsize=int(self.manual_frame.winfo_reqheight()) + 6)
            self.root.title("【需要人工验证】" + self._base_title)
        except Exception:
            pass
        self._flash_taskbar()
        try:
            self.root.bell()
        except Exception:
            pass

    def _hide_manual(self) -> None:
        self.manual_frame.grid_remove()
        try:
            self.root.rowconfigure(2, minsize=0)
        except Exception:
            pass
        if self._minimized_for_manual:
            self._minimized_for_manual = False
            try:
                if self.root.state() == "iconic":
                    self.root.deiconify()
                self.root.state("zoomed")
            except Exception:
                pass
        self.manual_focus_event.clear()
        try:
            self.root.title(self._base_title)
        except Exception:
            pass
        try:
            self.manual_focus_btn.configure(state="disabled")
            self.manual_ok_btn.configure(state="disabled")
            self.manual_cancel_btn.configure(state="disabled")
        except Exception:
            pass

    # ----------------------------------------------------------------- queue
    def _thread_status(self, stage: str, message: str) -> None:
        self.ui_queue.put(("status", message))

    def _poll_queue(self) -> None:
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0] if item else ""
                if kind == "log":
                    self._append_log_line(item[1], item[2])
                elif kind == "status":
                    self.status_var.set(str(item[1]))
                elif kind == "engine":
                    self.engine_label_var.set("引擎：" + str(item[1]))
                    self._emit_log("info", "当前浏览器引擎：" + str(item[1]))
                elif kind == "preview_ready":
                    title, body, category, replace = item[1], item[2], item[3], item[4]
                    if replace:
                        self._set_preview_text(title, body)
                    self.generate_btn.configure(state="normal")
                    self.status_var.set("内容已生成")
                    self._emit_log("success", "内容已生成，分类：" + str(category))
                elif kind == "preview_error":
                    self.generate_btn.configure(state="normal")
                    self.status_var.set("内容生成失败")
                    self._emit_log("error", "内容生成失败：" + str(item[1]))
                    messagebox.showerror("内容生成失败", str(item[1]))
                elif kind == "manual_request":
                    self._show_manual(str(item[1]), float(item[2]))
                elif kind == "manual_focus":
                    self.focus_browser()
                elif kind == "manual_close":
                    self._hide_manual()
                elif kind == "post_success":
                    result, index, total = item[1], item[2], item[3]
                    evidence = ""
                    try:
                        evidence = str((result.evidence or {}).get("evidence") or "")
                    except Exception:
                        evidence = ""
                    self._emit_log("success", "第 " + str(index) + "/" + str(total) + " 篇发布成功。" + evidence)
                    self._set_result("发布成功（第 " + str(index) + "/" + str(total) + " 篇）。" + truncate(evidence, 90), "success")
                    self.status_var.set("发布成功")
                elif kind == "post_failure":
                    result, index, total = item[1], item[2], item[3]
                    self._last_screenshot = str(getattr(result, "screenshot", "") or "")
                    self._emit_log("error", "第 " + str(index) + "/" + str(total) + " 篇发布失败：" + str(result.message))
                    if self._last_screenshot:
                        self._emit_log("info", "失败截图：" + self._last_screenshot)
                    self._set_result("发布失败（第 " + str(index) + "/" + str(total) + " 篇）：" + truncate(str(result.message), 120), "error")
                    self.status_var.set("发布失败")
                elif kind == "result":
                    self._set_result(str(item[1]), str(item[2]))
                elif kind == "finished":
                    self._finish_run()
                elif kind == "task_error":
                    self._set_result("任务失败：" + str(item[1]), "error")
                    self.status_var.set("任务失败")
        except queue.Empty:
            pass
        except Exception as exc:
            try:
                self._emit_log("error", "界面消息处理异常：" + str(exc))
            except Exception:
                pass
        finally:
            if not self._closing:
                self.root.after(120, self._poll_queue)

    def _finish_run(self) -> None:
        self.running = False
        self.generate_btn.configure(state="normal")
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.save_btn.configure(state="normal")
        self._hide_manual()
        if self.status_var.get() not in ("发布成功", "发布失败", "已停止", "任务失败"):
            self.status_var.set("就绪")
        self._emit_log("info", "本次任务结束。")

    def _set_result(self, text: str, kind: str) -> None:
        # 成功 = 白底墨字 + 墨色小旗；失败/待处理 = 强调色填充（红色只留给删除）
        fills = {
            "info": (efui.PANEL, efui.INK, efui.SIGNAL),
            "success": (efui.PANEL, efui.INK, efui.INK),
            "error": (efui.SIGNAL, efui.ON_SIGNAL, efui.INK),
            "warn": (efui.SIGNAL, efui.ON_SIGNAL, efui.INK),
        }
        bg, fg, flag = fills.get(kind, fills["info"])
        self.result_var.set(text)
        try:
            self.result_label.configure(bg=bg, fg=fg)
            self.result_flag.configure(bg=flag)
        except Exception:
            pass

    # ------------------------------------------------------------------ misc
    def open_screenshot_dir(self) -> None:
        try:
            path = screenshot_dir()
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno("任务正在运行", "任务正在运行，确定要退出吗？\n退出会尝试关闭浏览器。") :
                return
            self._closing = True
            self.stop_event.set()
            self.manual_event.set()
            try:
                if self.engine is not None:
                    self.engine.stop()
            except Exception:
                pass
        try:
            cfg = self.collect_settings()
            save_settings(cfg, self.settings_path)
        except Exception:
            pass
        self._emit_log("info", "程序退出。")
        try:
            self.root.destroy()
        except Exception:
            pass
