# -*- coding: utf-8 -*-
"""浏览器引擎工厂。

engine=auto：先尝试 Selenium（满足 Selenium 主路线），若本机 WebDriver
无法创建会话，则自动切换到 CDP 引擎。失败原因会记录到数据目录，
7 天内直接使用 CDP，避免每次启动都等待。engine=selenium/cdp 可强制指定。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from .base import BrowserEngine, BrowserError, StopRequested
from .cdp import CDPEngine
from .selenium_engine import SeleniumEngine


MARKER_TTL_SECONDS = 7 * 24 * 3600


def _marker_path() -> Path:
    from ..util import data_dir
    return data_dir() / "webdriver-unavailable.json"


def _marker_recent() -> bool:
    try:
        from ..util import read_json
        data = read_json(_marker_path())
        return (time.time() - float(data.get("time", 0))) < MARKER_TTL_SECONDS
    except Exception:
        return False


def _write_marker(error: str) -> None:
    try:
        from ..util import write_json_atomic
        write_json_atomic(_marker_path(), {"time": time.time(), "error": str(error)[:2000]})
    except Exception:
        pass


def _clear_marker() -> None:
    try:
        path = _marker_path()
        if path.exists():
            path.unlink()
    except Exception:
        pass


class AutoEngine(BrowserEngine):
    name = "auto"

    def __init__(self, settings: Dict[str, Any], log):
        super().__init__(settings, log)
        self.active: BrowserEngine = None
        self.failed_selenium: str = ""

    def _emit(self, level: str, message: str) -> None:
        try:
            self.log(level, message)
        except Exception:
            pass

    @property
    def engine_name(self) -> str:
        return self.active.name if self.active is not None else "auto"

    def start(self) -> None:
        mode = str((self.settings.get("browser") or {}).get("engine") or "auto").lower()
        if mode == "cdp":
            engine = CDPEngine(self.settings, self.log)
            engine.bind_stop(self.stop_event)
            engine.start()
            self.active = engine
            return
        if mode == "selenium":
            engine = SeleniumEngine(self.settings, self.log)
            engine.bind_stop(self.stop_event)
            engine.start()
            self.active = engine
            return
        if _marker_recent():
            self._emit("info", "检测到本机 WebDriver 曾创建失败，本次直接使用 CDP 引擎。")
            engine = CDPEngine(self.settings, self.log)
            engine.bind_stop(self.stop_event)
            engine.start()
            self.active = engine
            return
        selenium = SeleniumEngine(self.settings, self.log)
        selenium.bind_stop(self.stop_event)
        try:
            selenium.start()
            self.active = selenium
            _clear_marker()
            self._emit("info", "当前使用 Selenium 引擎。")
            return
        except StopRequested:
            raise
        except Exception as exc:
            self.failed_selenium = str(exc)
            brief = " ".join(str(exc).strip().splitlines()[:2])
            self._emit("warn", "Selenium 引擎启动失败：" + brief[:300])
            try:
                selenium.stop()
            except Exception:
                pass
            _write_marker(str(exc))
            self._emit("info", "自动切换到 CDP 引擎（真实 Edge/Chrome + DevTools 协议，无需 WebDriver）。")
        cdp = CDPEngine(self.settings, self.log)
        cdp.bind_stop(self.stop_event)
        try:
            cdp.start()
        except Exception as exc:
            raise BrowserError("Selenium 与 CDP 均启动失败。Selenium：" + self.failed_selenium + "；CDP：" + str(exc))
        self.active = cdp
        self._emit("info", "当前使用 CDP 引擎。")

    def stop(self) -> None:
        engine = self.active
        self.active = None
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass

    def is_alive(self) -> bool:
        if self.active is None:
            return False
        try:
            return bool(self.active.is_alive())
        except Exception:
            return False

    def _call(self, name: str, *args, **kwargs):
        if self.active is None:
            raise BrowserError("浏览器尚未启动。")
        return getattr(self.active, name)(*args, **kwargs)

    def navigate(self, url: str, timeout: float = 45) -> None:
        self._call("navigate", url, timeout)

    def eval(self, expression: str, await_promise: bool = False) -> Any:
        return self._call("eval", expression, await_promise)

    def current_url(self) -> str:
        return self._call("current_url")

    def title(self) -> str:
        return self._call("title")

    def click(self, selector: str, timeout: float = 15) -> None:
        self._call("click", selector, timeout)

    def fill(self, selector: str, value: str, timeout: float = 15) -> None:
        self._call("fill", selector, value, timeout)

    def fill_contenteditable(self, selector: str, value: str, timeout: float = 20) -> None:
        self._call("fill_contenteditable", selector, value, timeout)

    def screenshot(self, path: str, full_page: bool = False) -> str:
        return self._call("screenshot", path, full_page)

    def save_html(self, path: str) -> str:
        return self._call("save_html", path)

    def get_cookies(self) -> Dict[str, str]:
        return self._call("get_cookies")

    def drag(self, x1: float, y1: float, x2: float, y2: float, duration: float = 1.0) -> None:
        self._call("drag", x1, y1, x2, y2, duration)

    def click_at_system(self, x: float, y: float) -> bool:
        return bool(self._call("click_at_system", x, y))

    def element_screenshot(self, selector: str, path: str, scale: float = 1.0) -> bool:
        return self._call("element_screenshot", selector, path, scale)

    def __getattr__(self, item: str):
        active = self.__dict__.get("active")
        if active is not None:
            return getattr(active, item)
        raise AttributeError(item)


def create_engine(settings: Dict[str, Any], log) -> BrowserEngine:
    mode = str((settings.get("browser") or {}).get("engine") or "auto").lower()
    if mode == "cdp":
        return CDPEngine(settings, log)
    if mode == "selenium":
        return SeleniumEngine(settings, log)
    return AutoEngine(settings, log)
