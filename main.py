# -*- coding: utf-8 -*-
"""易班微社区自动发帖工具入口。"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import traceback
from pathlib import Path


def _ensure_std_streams() -> None:
    """--windowed 打包后 stdout/stderr 为 None，避免库里的 print 崩溃。"""
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")  # type: ignore[assignment]
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")  # type: ignore[assignment]
    except Exception:
        pass


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _set_app_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("YibanAutoPoster.1")
    except Exception:
        pass


def _write_crash(exc_type, exc, tb) -> str:
    try:
        from yiban_auto.util import log_dir, now_text
        folder = log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("crash-" + now_text().replace(":", "").replace(" ", "-") + ".txt")
        path.write_text("".join(traceback.format_exception(exc_type, exc, tb)), encoding="utf-8")
        return str(path)
    except Exception:
        return ""


def _install_exception_hooks(root) -> None:
    def hook(exc_type, exc, tb):
        path = _write_crash(exc_type, exc, tb)
        try:
            from tkinter import messagebox
            messagebox.showerror("程序错误", "发生未处理的错误：\n" + str(exc) + ("\n\n日志：" + path if path else ""))
        except Exception:
            pass

    sys.excepthook = hook

    def tk_hook(exc_type, exc, tb):
        path = _write_crash(exc_type, exc, tb)
        try:
            from tkinter import messagebox
            messagebox.showerror("界面错误", "界面回调发生错误：\n" + str(exc) + ("\n\n日志：" + path if path else ""))
        except Exception:
            pass

    try:
        root.report_callback_exception = tk_hook
    except Exception:
        pass


def _icon_path() -> str:
    try:
        from yiban_auto.util import resource_path
        path = resource_path("assets/yiban.ico")
        if path.exists():
            return str(path)
    except Exception:
        pass
    return ""


def run_gui() -> int:
    _enable_dpi()
    _set_app_id()
    import tkinter as tk
    from yiban_auto.gui import App

    root = tk.Tk()
    icon = _icon_path()
    if icon:
        try:
            root.iconbitmap(default=icon)
        except Exception:
            pass
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    _install_exception_hooks(root)
    root.mainloop()
    return 0


def run_browser_check() -> int:
    """诊断：用当前配置的真实配置目录启动 Edge，打开登录页并写报告。"""
    from yiban_auto.browser import create_engine
    from yiban_auto.config import load_settings
    from yiban_auto.util import app_dir, now_text, screenshot_dir

    cfg = load_settings()
    cfg["browser"]["engine"] = "cdp"
    cfg["browser"]["headless"] = False
    lines = []
    def log(level, message):
        lines.append("[" + str(level) + "] " + str(message))

    engine = None
    ok = False
    try:
        engine = create_engine(cfg, log)
        engine.start()
        engine.navigate("https://www.yiban.cn/login", timeout=40)
        engine.wait_ready(40)
        url = engine.current_url()
        title = engine.title()
        lines.append("URL=" + url)
        lines.append("TITLE=" + title)
        shot = screenshot_dir() / "browser-check.png"
        engine.screenshot(str(shot))
        lines.append("SCREENSHOT=" + str(shot))
        ok = "yiban.cn" in url.lower()
    except Exception as exc:
        lines.append("ERROR=" + repr(exc))
    finally:
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass
    report = app_dir() / "browser-check-report.txt"
    lines.append("REPORT=" + str(report))
    text = "浏览器启动诊断\n时间：" + now_text() + "\n结果：" + ("PASS" if ok else "FAIL") + "\n\n" + "\n".join(lines) + "\n"
    try:
        report.write_text(text, encoding="utf-8")
    except Exception:
        pass
    try:
        if sys.stdout is not None:
            sys.stdout.write(text + "\n")
    except Exception:
        pass
    return 0 if ok else 1


def main(argv=None) -> int:
    _ensure_std_streams()
    parser = argparse.ArgumentParser(prog="YibanAutoPoster", description="易班微社区自动发帖工具")
    parser.add_argument("--selftest", action="store_true", help="运行完整自检（含无界面浏览器加载登录页）并写报告")
    parser.add_argument("--selftest-headful", action="store_true", help="运行完整自检，但启动有界面浏览器（用于诊断浏览器启动问题）")
    parser.add_argument("--selftest-basic", action="store_true", help="只运行基础自检，不启动浏览器")
    parser.add_argument("--browser-check", action="store_true", help="诊断：用当前配置目录启动浏览器并打开登录页")
    parser.add_argument("--version", action="store_true", help="显示版本")
    args, _unknown = parser.parse_known_args(argv)

    if args.version:
        from yiban_auto import __version__
        try:
            if sys.stdout is not None:
                sys.stdout.write(__version__ + "\n")
        except Exception:
            pass
        return 0
    if args.browser_check:
        try:
            return run_browser_check()
        except Exception:
            _write_crash(*sys.exc_info())
            return 1
    if args.selftest or args.selftest_basic or args.selftest_headful:
        try:
            from yiban_auto.selftest import selftest_main
            return selftest_main(not args.selftest_basic, headless=not args.selftest_headful)
        except Exception:
            path = _write_crash(*sys.exc_info())
            try:
                from yiban_auto.util import app_dir
                report = app_dir() / "selftest-report.txt"
                report.write_text(
                    "自检启动失败（未完成）：\n" + (("崩溃日志：" + path + "\n\n") if path else "") +
                    "".join(traceback.format_exc()),
                    encoding="utf-8",
                )
            except Exception:
                pass
            return 1

    try:
        return run_gui()
    except Exception:
        path = _write_crash(*sys.exc_info())
        try:
            from tkinter import messagebox
            messagebox.showerror("启动失败", "程序启动失败。\n" + (("日志：" + path) if path else ""))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
