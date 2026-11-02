# -*- coding: utf-8 -*-
"""冻结 exe 自检。

自检只做只读操作：读取/写入临时配置、生成一篇模板内容、无界面加载易班登录页。
不会填写账号密码，不会提交登录，不会发帖。
"""
from __future__ import annotations

import json
import platform
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __app_name__, __version__
from .config import load_settings, normalize_settings, save_settings
from .content import TEMPLATE_CATEGORIES, generate_post, validate_post
from .util import app_dir, data_dir, log_dir, now_text, resource_path, screenshot_dir


class _Collector:
    def __init__(self) -> None:
        self.lines: List[str] = []

    def __call__(self, level: str, message: str) -> None:
        self.lines.append("[" + str(level) + "] " + str(message))


def _record(checks: List[Dict[str, Any]], name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})


def _all_ok(checks: List[Dict[str, Any]]) -> bool:
    return all(item.get("ok") for item in checks)


def _write_report(text: str) -> Path:
    candidates = [app_dir() / "selftest-report.txt", data_dir() / "selftest-report.txt"]
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return path
        except Exception:
            continue
    return Path("selftest-report.txt")


def run_selftest(full: bool = True, headless: bool = True) -> Tuple[bool, str, Path]:
    """返回 (是否通过, 报告文本, 报告路径)。headless=False 用于诊断有界面浏览器启动。"""
    started = time.time()
    checks: List[Dict[str, Any]] = []
    details: List[str] = []

    _record(checks, "Python 版本", sys.version_info >= (3, 9), platform.python_version())
    frozen = bool(getattr(sys, "frozen", False))
    _record(checks, "运行形态", True, "PyInstaller 冻结 exe" if frozen else "Python 源码")

    try:
        import tkinter  # noqa: F401
        _record(checks, "tkinter 可用", True, "Tk " + str(tkinter.TkVersion))
    except Exception as exc:
        _record(checks, "tkinter 可用", False, repr(exc))

    try:
        import selenium  # noqa: F401
        _record(checks, "selenium 可用", True, getattr(selenium, "__version__", "unknown"))
    except Exception as exc:
        _record(checks, "selenium 可用", False, repr(exc))

    settings_tmp = Path(tempfile.mkdtemp(prefix="yiban-selftest-settings-"))
    try:
        settings_path = settings_tmp / "settings.json"
        cfg = load_settings(settings_path)
        saved = normalize_settings(cfg)
        save_settings(saved, settings_path)
        loaded = load_settings(settings_path)
        ok = loaded.get("posting", {}).get("interval_seconds") == 30 and settings_path.exists()
        _record(checks, "配置读写", bool(ok), str(settings_path))
    except Exception as exc:
        _record(checks, "配置读写", False, repr(exc))
        details.append(traceback.format_exc())
    finally:
        shutil.rmtree(settings_tmp, ignore_errors=True)

    try:
        post = generate_post("template", TEMPLATE_CATEGORIES[0], "")
        validate_post(post.title, post.body)
        ok = 5 <= len(post.title) <= 30 and 0 < len(post.body) <= 10000
        _record(checks, "模板内容生成", ok, "标题：" + post.title + "；正文 " + str(len(post.body)) + " 字")
    except Exception as exc:
        _record(checks, "模板内容生成", False, repr(exc))
        details.append(traceback.format_exc())

    try:
        from .captcha_cv import analyze, decode_png, match_points, parse_instruction

        sample = resource_path("assets/captcha-sample.png")
        data = sample.read_bytes()
        width, height, rgb = decode_png(data)
        blobs = analyze(width, height, rgb)
        targets = parse_instruction("请依次点击：绿色圆柱、红色正方体")
        points = match_points(blobs, targets, width, height)
        ok = len(blobs) >= 5 and len(points) == len(targets)
        _record(
            checks, "离线识图",
            ok,
            str(width) + "x" + str(height) + "，图形 " + str(len(blobs))
            + " 个，命中 " + str(len(points)) + " 个",
        )
    except Exception as exc:
        _record(checks, "离线识图", False, repr(exc))
        details.append(traceback.format_exc())

    if full:
        profile = Path(tempfile.mkdtemp(prefix="yiban-selftest-profile-"))
        collector = _Collector()
        engine = None
        try:
            from .browser import create_engine
            cfg = normalize_settings({})
            cfg["browser"]["engine"] = "cdp"
            cfg["browser"]["headless"] = bool(headless)
            cfg["browser"]["profile_dir"] = str(profile)
            cfg["advanced"]["page_timeout"] = 40
            engine = create_engine(cfg, collector)
            engine.start()
            _record(checks, "CDP 浏览器启动", True, getattr(engine, "engine_name", "cdp"))
            engine.navigate("https://www.yiban.cn/login", timeout=40)
            engine.wait_ready(40)
            title = engine.title()
            url = engine.current_url()
            _record(checks, "登录页加载", "yiban.cn" in url.lower(), "URL=" + url + "；标题=" + title)
            _record(checks, "登录表单存在", bool(engine.exists("#account-txt", visible=True)), "#account-txt")
            try:
                shot = screenshot_dir() / "selftest-login.png"
                engine.screenshot(str(shot))
                _record(checks, "页面截图", shot.exists(), str(shot))
            except Exception as exc:
                _record(checks, "页面截图", False, repr(exc))
            if not headless:
                # 系统鼠标换算自检：点一个已知坐标，看页面收到的坐标对不对。
                # 换机器后“点不到”基本都出在这里（DPI 缩放 / 工具栏 / 侧边栏）。
                try:
                    # 在当前页面上盖一层透明遮罩记录点击坐标，避免点到真实链接跳走
                    engine.eval(
                        "(function(){var d=document.getElementById('__yb_probe');"
                        "if(!d){d=document.createElement('div');d.id='__yb_probe';"
                        "d.style.cssText='position:fixed;left:0;top:0;width:100%;height:100%;"
                        "z-index:2147483647;background:transparent';document.body.appendChild(d);}"
                        "window.__C=[];d.addEventListener('click',function(e){"
                        "window.__C.push([e.clientX,e.clientY]);});return true;})()"
                    )
                    clicked = False
                    first = []
                    for _attempt in range(2):
                        engine.wait(0.6)
                        clicked = bool(engine.click_at_system(200, 140))
                        engine.wait(0.5)
                        got = engine.eval("window.__C") or []
                        first = got[0] if got else []
                        if first:
                            break
                    hit = bool(first) and abs(float(first[0]) - 200) <= 4 and abs(float(first[1]) - 140) <= 4
                    _record(
                        checks,
                        "系统鼠标点击",
                        clicked and hit,
                        "请求 (200,140)，接口返回 " + str(clicked) + "，页面收到 " + (str(first) if first else "无"),
                    )
                    engine.eval("(function(){var d=document.getElementById('__yb_probe');if(d)d.remove();})()")
                except Exception as exc:
                    _record(checks, "系统鼠标点击", False, repr(exc))
        except Exception as exc:
            _record(checks, "CDP 浏览器/登录页", False, repr(exc))
            details.append(traceback.format_exc())
            details.extend(collector.lines[-20:])
        finally:
            if engine is not None:
                try:
                    engine.stop()
                except Exception:
                    pass
            if not _all_ok(checks):
                details.append("诊断用的浏览器配置目录（失败时保留）：" + str(profile))
                for name in ("DevToolsActivePort", "browser-stderr.log"):
                    try:
                        p = profile / name
                        if p.exists():
                            text = p.read_text(encoding="utf-8", errors="replace")
                            details.append("--- " + name + " ---")
                            details.extend(text.splitlines()[-80:])
                    except Exception as exc:
                        details.append("读取 " + name + " 失败：" + repr(exc))
            else:
                shutil.rmtree(profile, ignore_errors=True)

    ok = all(item["ok"] for item in checks)
    elapsed = time.time() - started
    lines = [
        "=" * 64,
        __app_name__ + " 自检报告",
        "版本：" + __version__,
        "时间：" + now_text(),
        "模式：" + (("完整（含浏览器，有界面）" if not headless else "完整（含浏览器，无界面）") if full else "基础（不含浏览器）"),
        "耗时：" + ("%.1f" % elapsed) + " 秒",
        "=" * 64,
    ]
    for item in checks:
        lines.append(("[PASS] " if item["ok"] else "[FAIL] ") + item["name"] + ("：" + item["detail"] if item["detail"] else ""))
    if details:
        lines.extend(["", "诊断信息：", "-" * 64])
        lines.extend(details[-60:])
    lines.extend(["", "结论：" + ("PASS（自检通过）" if ok else "FAIL（自检未通过）")])
    text = "\n".join(lines) + "\n"
    path = _write_report(text)
    return ok, text, path


def selftest_main(full: bool = True, headless: bool = True) -> int:
    ok, text, path = run_selftest(full, headless=headless)
    # windowed exe 下 stdout 可能为 None；有控制台时才打印。
    try:
        if sys.stdout is not None:
            sys.stdout.write(text)
            sys.stdout.write("报告文件：" + str(path) + "\n")
            sys.stdout.flush()
    except Exception:
        pass
    return 0 if ok else 1
