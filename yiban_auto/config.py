# -*- coding: utf-8 -*-
"""配置文件读写。账号密码绝不写入配置。"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict, List

from .util import app_dir, data_dir, ensure_dir, read_json, write_json_atomic


DEFAULT_SETTINGS: Dict[str, Any] = {
    "schema_version": 1,
    "browser": {
        "engine": "auto",              # auto | cdp | selenium
        "browser_path": "",
        "profile_dir": "",              # 留空使用数据目录下的 browser-profile
        "headless": False,
        "keep_browser_open": False,
        "window_width": 1280,
        "window_height": 900,
    },
    "posting": {
        "interval_seconds": 30,        # 两次发帖之间的间隔，允许 10-86400
        "repeat_count": 1,             # 单次运行最多连续发几篇，1-100
        "retry_count": 2,              # 单篇失败后的自动重试次数，0-10
        "retry_interval_seconds": 20,  # 每次重试前的等待秒数，5-600
        "regenerate_on_retry": True,   # 重试时是否换一版内容（避免“相同标题”）
        "verify_via_http": True,       # 发帖成功后再用浏览器 Cookie 走 HTTP 复核一次
        "verify_url": "https://s.yiban.cn/bbs/mypost",
        "community": "",               # 留空使用页面默认的第一个微社区
        "board": "",                   # 留空使用页面默认的第一个版块
        "cover_mode": 1,               # 仅支持 1=无图，避免自动上传无关图片
    },
    "content": {
        "mode": "template",             # template | llm | preset
        "template_category": "日常寒暄",
        "preset_items": [],             # [{"title": "...", "body": "..."}]
        "preset_pick": "sequential",    # sequential | random
        "llm": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
            "temperature": 0.8,
            "timeout_seconds": 90,
        },
    },
    "captcha": {
        # auto：先尝试自动处理（外接服务 → 滑块轨迹模拟 → 可选 OCR），失败后按
        #       fallback_manual 决定是否转人工；
        # manual：只人工接管；http/command：只调用用户自己的外接验证服务。
        "mode": "auto",
        "attempts": 2,                  # 每处验证最多自动尝试几次
        "retry_interval_seconds": 4,    # 两次自动尝试之间的等待
        "auto_cv": True,                # 离线识图：颜色 + 轮廓判断图形，不需要账号
        "click_mode": "system",        # system=系统真实鼠标 | browser=浏览器事件 | auto
        "auto_slider": True,            # 内置滑块/拖动轨迹模拟
        "auto_ocr": True,               # 自动尝试图片/文字点选 OCR（需要 ddddocr，缺失则跳过）
        # 数美「空间逻辑验证」（按文字找图形）需要语义理解，用视觉模型识别。
        # base_url / api_key / model 留空时复用 content.llm 的配置。
        "vision": {
            "enabled": True,
            "base_url": "",
            "api_key": "",
            "model": "",
            "timeout_seconds": 60,
        },
        "http_url": "",
        "http_key": "",
        "command": "",
        "timeout_seconds": 60,
        "settle_seconds": 3,
        "fallback_manual": True,
    },
    "security": {
        "remember_credentials": False,  # 勾选后用 Windows DPAPI 加密保存账号密码
    },
    "advanced": {
        "browser_start_timeout": 45,
        "page_timeout": 30,
        "login_timeout": 300,
        "manual_timeout": 300,
        "publish_timeout": 90,
        "screenshot_on_error": True,
    },
}

VALID_ENGINES = ("auto", "cdp", "selenium")
VALID_MODES = ("template", "llm", "preset")
VALID_PICKS = ("sequential", "random")
VALID_CAPTCHA_MODES = ("auto", "manual", "http", "command")
TEMPLATE_CATEGORIES = ("日常寒暄", "生活随感", "古诗词", "随机字母数字")
_LEGACY_CATEGORIES = {"校园问候": "日常寒暄"}
MAX_PRESET_ITEMS = 50


def _clean_preset_items(raw) -> List[Dict[str, str]]:
    """把界面/配置里的预置内容规整成 [{"title","body"}]，丢弃空项。"""
    items: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return items
    for entry in raw[:MAX_PRESET_ITEMS]:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        body = str(entry.get("body") or "").strip()
        if not title and not body:
            continue
        if len(title) > 30:
            title = title[:30]
        if len(body) > 10000:
            body = body[:10000]
        items.append({"title": title, "body": body})
    return items


class ConfigError(RuntimeError):
    pass


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _as_int(value, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        n = default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def _as_float(value, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def normalize_settings(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _deep_merge(DEFAULT_SETTINGS, raw if isinstance(raw, dict) else {})
    browser = cfg["browser"]
    engine = str(browser.get("engine", "auto")).lower().strip()
    browser["engine"] = engine if engine in VALID_ENGINES else "auto"
    browser["headless"] = bool(browser.get("headless", False))
    browser["keep_browser_open"] = bool(browser.get("keep_browser_open", False))
    browser["window_width"] = _as_int(browser.get("window_width"), 1280, 1000, 3840)
    browser["window_height"] = _as_int(browser.get("window_height"), 900, 700, 2160)
    browser["browser_path"] = str(browser.get("browser_path") or "").strip()
    browser["profile_dir"] = str(browser.get("profile_dir") or "").strip()

    posting = cfg["posting"]
    # 安全约束：两篇之间至少间隔 10 秒，避免高频请求被平台风控。
    posting["interval_seconds"] = _as_int(posting.get("interval_seconds"), 30, 10, 86400)
    posting["repeat_count"] = _as_int(posting.get("repeat_count"), 1, 1, 100)
    posting["retry_count"] = _as_int(posting.get("retry_count"), 2, 0, 10)
    posting["retry_interval_seconds"] = _as_int(posting.get("retry_interval_seconds"), 20, 5, 600)
    posting["regenerate_on_retry"] = bool(posting.get("regenerate_on_retry", True))
    posting["verify_via_http"] = bool(posting.get("verify_via_http", True))
    verify_url = str(posting.get("verify_url") or DEFAULT_SETTINGS["posting"]["verify_url"]).strip()
    posting["verify_url"] = verify_url if verify_url.lower().startswith(("http://", "https://")) \
        else DEFAULT_SETTINGS["posting"]["verify_url"]
    posting["community"] = str(posting.get("community") or "").strip()
    posting["board"] = str(posting.get("board") or "").strip()
    posting["cover_mode"] = _as_int(posting.get("cover_mode"), 1, 1, 1)

    content = cfg["content"]
    mode = str(content.get("mode", "template")).lower().strip()
    content["mode"] = mode if mode in VALID_MODES else "template"
    category = str(content.get("template_category") or "日常寒暄").strip()
    category = _LEGACY_CATEGORIES.get(category, category)
    content["template_category"] = category if category in TEMPLATE_CATEGORIES else "日常寒暄"
    content["preset_items"] = _clean_preset_items(content.get("preset_items"))
    pick = str(content.get("preset_pick") or "sequential").lower().strip()
    content["preset_pick"] = pick if pick in VALID_PICKS else "sequential"
    llm = content["llm"]
    llm["base_url"] = str(llm.get("base_url") or DEFAULT_SETTINGS["content"]["llm"]["base_url"]).strip()
    llm["api_key"] = str(llm.get("api_key") or "").strip()
    llm["model"] = str(llm.get("model") or DEFAULT_SETTINGS["content"]["llm"]["model"]).strip()
    llm["temperature"] = _as_float(llm.get("temperature"), 0.8, 0.0, 2.0)
    llm["timeout_seconds"] = _as_int(llm.get("timeout_seconds"), 90, 15, 300)

    captcha = cfg["captcha"]
    captcha_mode = str(captcha.get("mode") or "auto").lower().strip()
    captcha["mode"] = captcha_mode if captcha_mode in VALID_CAPTCHA_MODES else "auto"
    captcha["attempts"] = _as_int(captcha.get("attempts"), 2, 1, 10)
    captcha["retry_interval_seconds"] = _as_int(captcha.get("retry_interval_seconds"), 4, 1, 120)
    captcha["auto_cv"] = bool(captcha.get("auto_cv", True))
    click_mode = str(captcha.get("click_mode") or "system").lower().strip()
    captcha["click_mode"] = click_mode if click_mode in ("system", "browser", "auto") else "system"
    captcha["auto_slider"] = bool(captcha.get("auto_slider", True))
    captcha["auto_ocr"] = bool(captcha.get("auto_ocr", True))
    vision = captcha.get("vision") if isinstance(captcha.get("vision"), dict) else {}
    vision["enabled"] = bool(vision.get("enabled", True))
    vision["base_url"] = str(vision.get("base_url") or "").strip()
    vision["api_key"] = str(vision.get("api_key") or "").strip()
    vision["model"] = str(vision.get("model") or "").strip()
    vision["timeout_seconds"] = _as_int(vision.get("timeout_seconds"), 60, 10, 180)
    captcha["vision"] = vision
    captcha["http_url"] = str(captcha.get("http_url") or "").strip()
    captcha["http_key"] = str(captcha.get("http_key") or "").strip()
    captcha["command"] = str(captcha.get("command") or "").strip()
    captcha["timeout_seconds"] = _as_int(captcha.get("timeout_seconds"), 60, 5, 300)
    captcha["settle_seconds"] = _as_int(captcha.get("settle_seconds"), 3, 1, 20)
    captcha["fallback_manual"] = bool(captcha.get("fallback_manual", True))

    security = cfg["security"]
    security["remember_credentials"] = bool(security.get("remember_credentials", False))

    adv = cfg["advanced"]
    for key, default in (
        ("browser_start_timeout", 45),
        ("page_timeout", 30),
        ("login_timeout", 300),
        ("manual_timeout", 300),
        ("publish_timeout", 90),
    ):
        adv[key] = _as_int(adv.get(key), default, 5, 3600)
    adv["screenshot_on_error"] = bool(adv.get("screenshot_on_error", True))
    return cfg


def resolve_settings_path() -> Path:
    """优先使用 exe/项目目录下的 settings.json；不可写时退回数据目录。

    这里只探测目录可写性，不创建空文件，避免首次启动把空文件当成损坏配置。
    """
    preferred = app_dir() / "settings.json"
    if preferred.exists():
        return preferred
    try:
        ensure_dir(preferred.parent)
        probe = preferred.parent / (".settings-probe-" + str(os.getpid()) + ".tmp")
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return preferred
    except OSError:
        return ensure_dir(data_dir()) / "settings.json"


def load_settings(path: Path | None = None) -> Dict[str, Any]:
    path = Path(path) if path else resolve_settings_path()
    if not path.exists():
        cfg = normalize_settings({})
        save_settings(cfg, path)
        return cfg
    if path.stat().st_size == 0:
        cfg = normalize_settings({})
        save_settings(cfg, path)
        return cfg
    try:
        raw = read_json(path)
    except Exception as exc:
        raise ConfigError(f"配置文件读取失败：{path}\n{exc}") from exc
    return normalize_settings(raw)


def save_settings(settings: Dict[str, Any], path: Path | None = None) -> Path:
    path = Path(path) if path else resolve_settings_path()
    clean = normalize_settings(settings)
    # 双保险：如果调用方误传账号密码字段，也绝不落盘。
    for forbidden in ("account", "username", "password", "passwd", "pwd"):
        clean.pop(forbidden, None)
    write_json_atomic(path, clean)
    return path


def settings_without_secrets(settings: Dict[str, Any]) -> Dict[str, Any]:
    clone = copy.deepcopy(settings)
    try:
        clone["content"]["llm"]["api_key"] = ""
    except Exception:
        pass
    try:
        clone["captcha"]["http_key"] = ""
    except Exception:
        pass
    return clone
