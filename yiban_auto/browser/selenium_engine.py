# -*- coding: utf-8 -*-
"""Selenium 浏览器引擎（可选路线）。

优先使用用户配置的 WebDriver；未配置时交给 Selenium Manager 查找。
本机 WebDriver 存在系统级临时目录缺陷，因此默认“自动”模式会在
Selenium 启动失败后回落至 CDP 引擎；单独选择 Selenium 时仍可排错。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .base import BrowserEngine, BrowserError, ElementNotFound
from .cdp import NETWORK_JS, STEALTH_JS


class SeleniumEngine(BrowserEngine):
    name = "selenium"

    def __init__(self, settings: Dict[str, Any], log):
        super().__init__(settings, log)
        self.driver = None
        self._browser_kind = "edge"
        self._browser_path = ""

    def _emit(self, level: str, message: str) -> None:
        try:
            self.log(level, message)
        except Exception:
            pass

    def is_alive(self) -> bool:
        if self.driver is None:
            return False
        try:
            _ = self.driver.window_handles
            return True
        except Exception:
            return False

    def _cfg(self) -> Dict[str, Any]:
        return self.settings.get("browser") or {}

    def _resolve_browser(self):
        cfg = self._cfg()
        configured = str(cfg.get("browser_path") or "").strip()
        if configured:
            path = Path(configured).expanduser()
            if path.exists():
                return str(path), ("chrome" if "chrome" in path.name.lower() else "edge")
            raise BrowserError("配置的浏览器路径不存在：" + str(path))
        candidates = [
            (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "edge"),
            (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "edge"),
            (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
            (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chrome"),
        ]
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.append((str(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"), "chrome"))
        for path, kind in candidates:
            if Path(path).exists():
                return path, kind
        raise BrowserError("未找到 Edge 或 Chrome，无法使用 Selenium 引擎。")

    def _driver_path(self, kind: str) -> Optional[str]:
        cfg = self._cfg()
        configured = str(cfg.get("driver_path") or "").strip()
        if configured:
            path = Path(configured).expanduser()
            if path.exists():
                return str(path)
            raise BrowserError("配置的 WebDriver 路径不存在：" + str(path))
        from ..util import app_dir, resource_path
        names = ["msedgedriver.exe"] if kind == "edge" else ["chromedriver.exe"]
        for name in names:
            for folder in ("drivers", ".", "bin"):
                path = app_dir() / folder / name
                if path.exists():
                    return str(path)
            bundled = resource_path("drivers/" + name)
            if bundled.exists():
                return str(bundled)
        return None

    def start(self) -> None:
        try:
            from selenium import webdriver
            from selenium.webdriver.edge.options import Options as EdgeOptions
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.edge.service import Service as EdgeService
            from selenium.webdriver.chrome.service import Service as ChromeService
        except Exception as exc:
            raise BrowserError("未安装 Selenium，无法使用 Selenium 引擎：" + str(exc))
        self._browser_path, self._browser_kind = self._resolve_browser()
        cfg = self._cfg()
        headless = bool(cfg.get("headless", False))
        try:
            width = int(cfg.get("window_width") or 1280)
            height = int(cfg.get("window_height") or 900)
        except Exception:
            width, height = 1280, 900
        self._emit("info", "Selenium 启动浏览器：" + self._browser_kind + " (" + self._browser_path + ")")
        if self._browser_kind == "edge":
            options = EdgeOptions()
            service_cls = EdgeService
        else:
            options = ChromeOptions()
            service_cls = ChromeService
        options.binary_location = self._browser_path
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--lang=zh-CN")
        options.add_argument("--window-size=" + str(width) + "," + str(height))
        if headless:
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
        try:
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
        except Exception:
            pass
        driver_path = self._driver_path(self._browser_kind)
        try:
            if driver_path:
                service = service_cls(executable_path=driver_path)
            else:
                service = service_cls()
            if self._browser_kind == "edge":
                self.driver = webdriver.Edge(service=service, options=options)
            else:
                self.driver = webdriver.Chrome(service=service, options=options)
        except Exception as exc:
            raise BrowserError("Selenium 会话创建失败：" + str(exc))
        try:
            self.driver.set_page_load_timeout(45)
            self.driver.implicitly_wait(0)
            self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS + "\n" + NETWORK_JS})
        except Exception:
            pass
        try:
            self.driver.execute_cdp_cmd("Runtime.evaluate", {"expression": STEALTH_JS})
        except Exception:
            pass
        self._emit("info", "Selenium 会话已建立")

    def stop(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def navigate(self, url: str, timeout: float = 45) -> None:
        self.check_stop()
        if self.driver is None:
            raise BrowserError("Selenium 尚未启动。")
        try:
            self.driver.set_page_load_timeout(timeout)
            self.driver.get(url)
        except Exception as exc:
            raise BrowserError("打开页面失败：" + str(exc))
        self.wait(0.3)

    def eval(self, expression: str, await_promise: bool = False) -> Any:
        if self.driver is None:
            raise BrowserError("Selenium 尚未启动。")
        try:
            return self.driver.execute_script("return (" + expression + ");")
        except Exception as exc:
            raise BrowserError("页面脚本执行失败：" + str(exc))

    def current_url(self) -> str:
        return str(self.driver.current_url if self.driver is not None else "")

    def title(self) -> str:
        return str(self.driver.title if self.driver is not None else "")

    def _find(self, selector: str, timeout: float = 15, visible: bool = True):
        from selenium.webdriver.common.by import By
        end = time.time() + timeout
        while time.time() < end:
            self.check_stop()
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                elements = []
            for el in elements:
                try:
                    if not visible:
                        return el
                    if el.is_displayed() and el.size.get("width", 0) > 1 and el.size.get("height", 0) > 1:
                        return el
                except Exception:
                    continue
            time.sleep(0.25)
        return None

    def click(self, selector: str, timeout: float = 15) -> None:
        el = self._find(selector, timeout=timeout)
        if el is None:
            raise ElementNotFound("未找到可点击元素：" + selector)
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'center'});", el)
            self.wait(0.15)
            el.click()
        except Exception:
            try:
                self.driver.execute_script("arguments[0].click();", el)
            except Exception as exc:
                raise BrowserError("点击元素失败：" + str(exc))

    def click_at(self, x: float, y: float) -> None:
        if self.driver is None:
            raise BrowserError("Selenium 尚未启动。")
        common = {"x": float(x), "y": float(y), "button": "left", "clickCount": 1}
        try:
            self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", dict(common, type="mouseMoved", buttons=0))
            self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", dict(common, type="mousePressed", buttons=1))
            self.wait(0.06)
            self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", dict(common, type="mouseReleased", buttons=0))
        except Exception:
            # 老版本驱动/浏览器不支持 CDP 输入时，退化为 DOM 点击。
            super().click_at(x, y)

    def fill(self, selector: str, value: str, timeout: float = 15) -> None:
        from selenium.webdriver.common.keys import Keys
        el = self._find(selector, timeout=timeout)
        if el is None:
            raise ElementNotFound("未找到输入框：" + selector)
        try:
            el.click()
            el.send_keys(Keys.CONTROL, "a")
            el.send_keys(Keys.BACKSPACE)
            el.send_keys(value)
        except Exception as exc:
            raise BrowserError("填写输入框失败：" + str(exc))
        self.wait(0.15)

    def fill_contenteditable(self, selector: str, value: str, timeout: float = 20) -> None:
        from selenium.webdriver.common.keys import Keys
        el = self._find(selector, timeout=timeout)
        if el is None:
            if self._fill_in_frames(value):
                return
            raise ElementNotFound("未找到正文编辑器：" + selector)
        try:
            el.click()
            el.send_keys(Keys.CONTROL, "a")
            el.send_keys(Keys.BACKSPACE)
            el.send_keys(value)
        except Exception:
            pass
        self.wait(0.3)
        try:
            text = str(el.text or "")
        except Exception:
            text = ""
        if not text.strip() or (value.strip() and value.strip()[:10] not in text):
            val = json.dumps(value, ensure_ascii=False)
            try:
                self.driver.execute_script(
                    "var el=arguments[0]; el.focus(); document.execCommand('insertText', false, " + val + ");", el
                )
            except Exception as exc:
                raise BrowserError("写入正文编辑器失败：" + str(exc))
        self.wait(0.2)

    def _fill_in_frames(self, value: str) -> bool:
        """主文档没有编辑器时，进入 iframe 查找 contenteditable。"""
        if self.driver is None:
            return False
        val = json.dumps(value, ensure_ascii=False)
        try:
            frames = self.driver.find_elements("css selector", "iframe")
        except Exception:
            return False
        for frame in frames:
            self.check_stop()
            try:
                self.driver.switch_to.frame(frame)
            except Exception:
                try:
                    self.driver.switch_to.default_content()
                except Exception:
                    pass
                continue
            try:
                candidates = self.driver.find_elements(
                    "css selector", "[contenteditable='true'], .w-e-textarea, body"
                )
                for el in candidates:
                    try:
                        tag = str(el.tag_name or "").lower()
                        editable = str(el.get_attribute("contenteditable") or "").lower() == "true"
                        if tag == "body" and len(candidates) > 1:
                            continue
                        if tag != "body" and not editable:
                            continue
                        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        el.click()
                        self.driver.execute_script(
                            "var e=arguments[0]; e.focus();"
                            "document.execCommand('selectAll', false, null);"
                            "document.execCommand('insertText', false, " + val + ");",
                            el,
                        )
                        self.wait(0.3)
                        text = str(el.text or "")
                        if text.strip() and (not value.strip() or value.strip()[:10] in text):
                            self._emit("warn", "正文编辑器位于 iframe 内，已切换到 iframe 填写。")
                            return True
                    except Exception:
                        continue
            finally:
                try:
                    self.driver.switch_to.default_content()
                except Exception:
                    pass
        return False

    def get_cookies(self) -> Dict[str, str]:
        if self.driver is None:
            return {}
        try:
            return {str(c.get("name")): str(c.get("value") or "") for c in self.driver.get_cookies()}
        except Exception:
            return super().get_cookies()

    def drag(self, x1: float, y1: float, x2: float, y2: float, duration: float = 1.0) -> None:
        if self.driver is None:
            raise BrowserError("Selenium 尚未启动。")
        duration = max(0.3, min(6.0, float(duration)))
        steps = max(16, int(duration * 45))
        base = {"button": "left", "clickCount": 1}
        try:
            self.driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                dict(base, type="mouseMoved", x=float(x1), y=float(y1), buttons=0),
            )
            self.driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                dict(base, type="mousePressed", x=float(x1), y=float(y1), buttons=1),
            )
            for index in range(1, steps + 1):
                self.check_stop()
                t = index / float(steps)
                eased = 1.0 - (1.0 - t) ** 3
                x = float(x1) + (float(x2) - float(x1)) * eased
                y = float(y1) + (float(y2) - float(y1)) * eased
                self.driver.execute_cdp_cmd(
                    "Input.dispatchMouseEvent",
                    dict(base, type="mouseMoved", x=x, y=y, buttons=1),
                )
                time.sleep(duration / steps)
            self.driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                dict(base, type="mouseReleased", x=float(x2), y=float(y2), buttons=0),
            )
        except Exception as exc:
            raise BrowserError("拖动失败：" + str(exc))
        self.wait(0.2)

    def element_screenshot(self, selector: str, path: str, scale: float = 1.0) -> bool:
        el = self._find(selector, timeout=8)
        if el is None:
            return False
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            el.screenshot(str(target))
            return True
        except Exception:
            return False

    def screenshot(self, path: str, full_page: bool = False) -> str:
        if self.driver is None:
            raise BrowserError("Selenium 尚未启动。")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if full_page:
                import base64
                data = self.driver.execute_cdp_cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
                target.write_bytes(base64.b64decode(data.get("data") or ""))
            else:
                self.driver.save_screenshot(str(target))
        except Exception as exc:
            raise BrowserError("截图失败：" + str(exc))
        return str(target)

    def save_html(self, path: str) -> str:
        html = str(self.driver.page_source if self.driver is not None else "")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8", errors="replace")
        return str(target)

    def bring_to_front(self) -> None:
        try:
            self.driver.switch_to.window(self.driver.current_window_handle)
        except Exception:
            pass

    def press_key(self, key: str, code: Optional[str] = None) -> None:
        from selenium.webdriver.common.keys import Keys
        mapping = {"Enter": Keys.ENTER, "Tab": Keys.TAB, "Escape": Keys.ESCAPE, "Backspace": Keys.BACKSPACE}
        try:
            body = self.driver.find_element("tag name", "body")
            body.send_keys(mapping.get(key, key))
        except Exception:
            pass
