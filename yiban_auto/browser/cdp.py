# -*- coding: utf-8 -*-
"""基于 Chrome DevTools Protocol 的浏览器引擎。

不依赖 chromedriver / msedgedriver：直接启动本机 Edge 或 Chrome，
通过 remote debugging 端口与 WebSocket 控制页面。适用于 WebDriver
无法创建会话的环境，同时保留真实浏览器的 DOM、Cookie 和 JS 行为。
"""
from __future__ import annotations

import base64
import json
import os
import random
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .base import BrowserEngine, BrowserError, ElementNotFound

try:
    import websocket
except Exception as exc:  # pragma: no cover
    websocket = None
    _WS_IMPORT_ERROR = exc
else:
    _WS_IMPORT_ERROR = None


STEALTH_JS = r"""
(function () {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: function () { return undefined; } });
  } catch (e) {}
  try {
    if (!window.chrome) { window.chrome = {}; }
    if (!window.chrome.runtime) { window.chrome.runtime = {}; }
  } catch (e) {}
  try {
    Object.defineProperty(navigator, 'languages', { get: function () { return ['zh-CN', 'zh']; } });
  } catch (e) {}
})();
"""

NETWORK_JS = r"""
(function () {
  if (window.__YB_NET__) { return; }
  window.__YB_NET__ = [];
  function push(item) {
    try {
      window.__YB_NET__.push(item);
      if (window.__YB_NET__.length > 100) { window.__YB_NET__.shift(); }
    } catch (e) {}
  }
  function interesting(url) {
    return /\/api|post|login|captcha|security|community|board/i.test(String(url || ''));
  }
  try {
    var origFetch = window.fetch;
    if (origFetch) {
      window.fetch = function () {
        var args = arguments;
        return origFetch.apply(this, args).then(function (resp) {
          try {
            var url = resp.url || (args && args[0] && args[0].url) || '';
            if (interesting(url)) {
              resp.clone().text().then(function (text) {
                push({ url: url, method: String((args && args[1] && args[1].method) || 'GET').toUpperCase(), status: resp.status, body: String(text || '').slice(0, 6000), t: Date.now() });
              }).catch(function () {});
            }
          } catch (e) {}
          return resp;
        });
      };
    }
  } catch (e) {}
  try {
    var origOpen = XMLHttpRequest.prototype.open;
    var origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      this.__yb_url = url;
      this.__yb_method = String(method || 'GET').toUpperCase();
      return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      var xhr = this;
      try {
        xhr.addEventListener('load', function () {
          try {
            var url = xhr.responseURL || xhr.__yb_url || '';
            if (interesting(url)) {
              var text = '';
              try {
                text = xhr.responseType === 'json' ? JSON.stringify(xhr.response) : xhr.responseText;
              } catch (e) { text = ''; }
              push({ url: url, method: String(xhr.__yb_method || 'GET').toUpperCase(), status: xhr.status, body: String(text || '').slice(0, 6000), t: Date.now() });
            }
          } catch (e) {}
        });
      } catch (e) {}
      return origSend.apply(this, arguments);
    };
  } catch (e) {}
})();
"""


def _browser_candidates() -> List[Tuple[str, str]]:
    items = []
    for env_key, kind in (("PROGRAMFILES(X86)", "edge"), ("PROGRAMFILES", "edge"), ("LOCALAPPDATA", "edge")):
        base = os.environ.get(env_key, "")
        if base:
            items.append((str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"), kind))
    for env_key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env_key, "")
        if base:
            items.append((str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"), "chrome"))
    for name, kind in (("msedge.exe", "edge"), ("chrome.exe", "chrome")):
        found = shutil.which(name)
        if found:
            items.append((found, kind))
    return items


class CDPClient:
    def __init__(self, ws_url: str, timeout: float = 30):
        if websocket is None:
            raise BrowserError("缺少 websocket-client 依赖：" + str(_WS_IMPORT_ERROR))
        self.ws_url = ws_url
        self.timeout = timeout
        self._ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
        self._next_id = 0
        self._session_id: Optional[str] = None

    def set_session(self, session_id: Optional[str]) -> None:
        self._session_id = session_id

    def send(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        payload: Dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
        if self._session_id:
            payload["sessionId"] = self._session_id
        try:
            self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            raise BrowserError("CDP 连接已断开：" + str(exc))
        end = time.time() + (timeout if timeout is not None else self.timeout)
        while time.time() < end:
            remain = max(0.5, end - time.time())
            try:
                self._ws.settimeout(remain)
                raw = self._ws.recv()
            except Exception as exc:
                if websocket is not None and isinstance(exc, getattr(websocket, "WebSocketTimeoutException", ())):
                    raise BrowserError("等待浏览器响应超时：" + method)
                raise BrowserError("CDP 通信失败：" + str(exc))
            if not raw:
                raise BrowserError("CDP 连接被浏览器关闭。")
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") == msg_id:
                if "error" in msg:
                    err = msg.get("error") or {}
                    raise BrowserError(str(err.get("message") or err))
                return msg.get("result") or {}
        raise BrowserError("等待浏览器响应超时：" + method)

    def eval(self, expression: str, await_promise: bool = False) -> Any:
        result = self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": bool(await_promise),
            "userGesture": True,
        })
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = details.get("text") or ""
            exc = details.get("exception") or {}
            desc = exc.get("description") or ""
            raise BrowserError((text + " " + desc).strip() or "页面脚本执行失败。")
        value = result.get("result") or {}
        if value.get("type") == "undefined":
            return None
        if "value" in value:
            return value.get("value")
        return value.get("unserializableValue")

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


class CDPEngine(BrowserEngine):
    name = "cdp"

    def __init__(self, settings: Dict[str, Any], log):
        super().__init__(settings, log)
        self._proc: Optional[subprocess.Popen] = None
        self._client: Optional[CDPClient] = None
        self._port: Optional[int] = None
        self._session_id: Optional[str] = None
        self._target_id: Optional[str] = None
        self._profile = Path(".")
        self._browser_path = ""
        self._browser_kind = "edge"
        self._log_file: Optional[Path] = None

    def _emit(self, level: str, message: str) -> None:
        try:
            self.log(level, message)
        except Exception:
            pass

    def is_alive(self) -> bool:
        if self._client is None or not self._port:
            return False
        try:
            with socket.create_connection(("127.0.0.1", int(self._port)), timeout=1.0):
                return True
        except Exception:
            return False

    def _browser_config(self) -> Dict[str, Any]:
        return self.settings.get("browser") or {}

    def _resolve_browser(self) -> Tuple[str, str]:
        cfg = self._browser_config()
        configured = str(cfg.get("browser_path") or "").strip()
        if configured:
            path = Path(configured).expanduser()
            if not path.is_absolute():
                path = Path(self.settings.get("_app_dir") or os.getcwd()) / path
            if path.exists():
                kind = "chrome" if "chrome" in path.name.lower() else "edge"
                return str(path), kind
            raise BrowserError("配置的浏览器路径不存在：" + str(path))
        for path_text, kind in _browser_candidates():
            if Path(path_text).exists():
                return path_text, kind
        raise BrowserError("未找到 Microsoft Edge 或 Google Chrome。请安装浏览器，或在设置中填写浏览器路径。")

    def _resolve_profile(self) -> Path:
        from ..util import app_dir, data_dir, ensure_dir
        cfg = self._browser_config()
        raw = str(cfg.get("profile_dir") or "").strip()
        if raw:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = app_dir() / path
        else:
            path = data_dir() / "browser-profile"
        ensure_dir(path)
        return path

    def start(self) -> None:
        self._browser_path, self._browser_kind = self._resolve_browser()
        self._profile = self._resolve_profile()
        self._emit("info", "启动浏览器：" + self._browser_kind + " (" + self._browser_path + ")")
        self._emit("info", "浏览器配置目录：" + str(self._profile))
        last_error: Optional[Exception] = None
        for attempt in range(1, 3):
            self.check_stop()
            try:
                self._launch_once()
                self._emit("info", "浏览器调试通道已就绪，端口 " + str(self._port))
                return
            except Exception as exc:
                last_error = exc
                self._emit("warn", "浏览器启动失败（第 " + str(attempt) + " 次）：" + str(exc))
                self._cleanup_process()
                self.wait(1.0)
        raise BrowserError("无法启动浏览器：" + str(last_error))

    def _close_stale_profile_browsers(self) -> int:
        """关闭仍占用当前配置目录的旧 Edge/Chrome 进程。

        只匹配命令行动态路径包含本工具配置目录的进程，不会关闭用户其他浏览器窗口。
        Edge 被兼容层重启后，最早由 Popen 启动的进程会退出，真正浏览器继续占用配置目录；
        不清理它，下一次启动会一直等不到新的调试端口。
        """
        if os.name != "nt" or not self._browser_kind:
            return 0
        exe_name = "chrome.exe" if self._browser_kind == "chrome" else "msedge.exe"
        profile = str(self._profile).replace("'", "''")
        script = (
            "$p='" + profile + "'; $n=0; "
            "Get-CimInstance Win32_Process -Filter \"Name='" + exe_name + "'\" | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($p) } | "
            "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $n++ } catch {} }; "
            "Write-Output $n"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            done = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=25,
                creationflags=flags,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            count = 0
            for line in (done.stdout or "").splitlines():
                line = line.strip()
                if line.isdigit():
                    count = int(line)
            if count:
                self._emit("warn", "已关闭 " + str(count) + " 个占用浏览器配置目录的旧进程，继续启动。")
                time.sleep(0.8)
            return count
        except Exception:
            return 0

    def _launch_once(self) -> None:
        cfg = self._browser_config()
        headless = bool(cfg.get("headless", False))
        try:
            width = int(cfg.get("window_width") or 1280)
            height = int(cfg.get("window_height") or 900)
        except Exception:
            width, height = 1280, 900
        self._close_stale_profile_browsers()
        port_file = self._profile / "DevToolsActivePort"
        try:
            if port_file.exists():
                port_file.unlink()
        except Exception:
            pass
        args = [
            self._browser_path,
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            # Edge 的兼容层重启会杀掉最初由 Popen 启动的进程，再拉起一个新进程。
            # 新版 Edge 可能把调试端口参数丢掉，导致永远等不到 DevToolsActivePort。
            "--disable-features=msRelaunchNoCompatLayer",
            "--disable-background-mode",
            "--user-data-dir=" + str(self._profile),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--window-size=" + str(width) + "," + str(height),
        ]
        if headless:
            args.extend(["--headless=new", "--disable-gpu"])
        else:
            args.extend(["--window-position=80,40"])
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._log_file = self._profile / "browser-stderr.log"
        try:
            err_handle = open(self._log_file, "ab")
        except Exception:
            err_handle = subprocess.DEVNULL
        env = os.environ.copy()
        # 某些 Windows 环境的 TEMP 指向不可用目录（或相对路径），会让 Edge 启动卡住。
        # 给浏览器一个确定可写的临时目录，避免继承到异常 TEMP/TMP。
        browser_tmp = self._profile / "browser-tmp"
        try:
            browser_tmp.mkdir(parents=True, exist_ok=True)
            env["TEMP"] = str(browser_tmp)
            env["TMP"] = str(browser_tmp)
        except Exception:
            pass
        try:
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err_handle,
                creationflags=flags,
                close_fds=True,
                env=env,
            )
        finally:
            if hasattr(err_handle, "close"):
                try:
                    err_handle.close()
                except Exception:
                    pass
        end = time.time() + 45
        port = None
        exit_code = None
        exit_at = 0.0
        while time.time() < end:
            self.check_stop()
            rc = self._proc.poll()
            if rc is not None:
                if exit_code is None:
                    exit_code = rc
                    exit_at = time.time()
                    self._emit("info", "浏览器启动进程已退出，代码 " + str(rc) + "，继续等待调试端口。")
                # Edge 兼容层重启时，最初进程正常退出（0），真正浏览器会稍后写端口文件。
                # 退出码非 0 时多等 5 秒；只要端口出现仍然继续。
                if rc != 0 and time.time() - exit_at > 5:
                    break
            if port_file.exists():
                try:
                    lines = port_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if lines and lines[0].strip().isdigit():
                        port = int(lines[0].strip())
                        break
                except Exception:
                    pass
            time.sleep(0.25)
        if not port:
            extra = ""
            if exit_code is not None:
                extra = "（启动进程退出代码 " + str(exit_code) + "）"
            raise BrowserError(
                "浏览器没有在 45 秒内开放调试端口" + extra + "，日志：" + str(self._log_file) +
                "。请关闭占用同一配置目录的 Edge/Chrome 后重试；若反复失败，可删除配置目录 "
                + str(self._profile) + " 再试（会退出当前登录）。"
            )
        self._port = port
        # 冻结 exe 首次启动 Edge 时，端口文件会先出现，HTTP 端点可能还要几秒才可用。
        version = self._get_json("http://127.0.0.1:" + str(port) + "/json/version", timeout=30)
        ws_url = str((version or {}).get("webSocketDebuggerUrl") or "")
        if not ws_url:
            raise BrowserError("浏览器没有返回 WebSocket 调试地址。")
        self._emit("info", "浏览器：" + str((version or {}).get("Browser") or self._browser_kind))
        self._client = CDPClient(ws_url, timeout=45)
        targets = self._client.send("Target.getTargets", {})
        page_targets = [t for t in (targets.get("targetInfos") or []) if t.get("type") == "page"]
        target_id = None
        for item in page_targets:
            if str(item.get("url") or "").startswith("about:blank") or not item.get("url"):
                target_id = item.get("targetId")
                break
        if target_id is None:
            # 冻结 exe 启动的 Edge 可能直接打开 edge://newtab，而不是 about:blank。
            # 优先复用普通页面；同步确认页不适合承载自动化页面。
            for item in page_targets:
                url = str(item.get("url") or "")
                if "sync-confirmation" not in url:
                    target_id = item.get("targetId")
                    break
        if target_id is None:
            created = self._client.send("Target.createTarget", {"url": "about:blank"})
            target_id = created.get("targetId")
        if not target_id:
            raise BrowserError("无法创建浏览器页面。")
        attached = self._client.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        session_id = attached.get("sessionId")
        if not session_id:
            raise BrowserError("无法连接浏览器页面。")
        self._target_id = target_id
        self._session_id = session_id
        self._client.set_session(session_id)
        self._client.send("Page.enable", {}, timeout=45)
        self._client.send("Runtime.enable", {}, timeout=45)
        self._client.send("Page.bringToFront", {}, timeout=20)
        self._client.send("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS + "\n" + NETWORK_JS}, timeout=45)
        try:
            self._client.eval(STEALTH_JS)
            self._client.eval(NETWORK_JS)
        except Exception:
            pass
        try:
            win = self._client.send("Browser.getWindowForTarget", {"targetId": target_id})
            window_id = win.get("windowId")
            if window_id is not None and not headless:
                self._client.send("Browser.setWindowBounds", {
                    "windowId": window_id,
                    "bounds": {"windowState": "normal", "width": width, "height": height, "left": 80, "top": 40},
                })
        except Exception:
            pass
        self.navigate("about:blank", timeout=15)

    def _get_json(self, url: str, timeout: float = 30) -> Dict[str, Any]:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        last = None
        end = time.time() + timeout
        while time.time() < end:
            try:
                with opener.open(url, timeout=4) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
            except Exception as exc:
                last = exc
                time.sleep(0.3)
        raise BrowserError("无法连接浏览器调试端口：" + str(last))

    def _cleanup_process(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    pass
        except Exception:
            pass
        if proc.poll() is None:
            try:
                taskkill = shutil.which("taskkill") or "taskkill.exe"
                subprocess.run([taskkill, "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:
                pass

    def stop(self) -> None:
        client = self._client
        if client is not None:
            try:
                client.send("Browser.close", {}, timeout=5)
            except Exception:
                pass
        self._cleanup_process()

    def navigate(self, url: str, timeout: float = 45) -> None:
        self.check_stop()
        if self._client is None:
            raise BrowserError("浏览器尚未启动。")
        result = self._client.send("Page.navigate", {"url": url})
        error_text = result.get("errorText")
        if error_text:
            raise BrowserError("打开页面失败：" + str(error_text))
        self.wait_ready(timeout)
        self.wait(0.4)

    def eval(self, expression: str, await_promise: bool = False) -> Any:
        if self._client is None:
            raise BrowserError("浏览器尚未启动。")
        return self._client.eval(expression, await_promise=await_promise)

    def current_url(self) -> str:
        return str(self.eval("location.href") or "")

    def title(self) -> str:
        return str(self.eval("document.title") or "")

    def _rect(self, selector: str, timeout: float = 15, mark: bool = True) -> Optional[Dict[str, Any]]:
        sel = json.dumps(selector, ensure_ascii=False)
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel + "));"
            "var best=null;for(var i=0;i<els.length;i++){var e=els[i];var r=e.getBoundingClientRect();"
            "var s=getComputedStyle(e);var vis=r.width>1&&r.height>1&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0';"
            "if(vis){best=e;break;}}if(!best)return null;"
            "if(" + ("true" if mark else "false") + "){best.setAttribute('data-yb-target','1');}"
            "best.scrollIntoView({block:'center',inline:'center'});"
            "var b=best.getBoundingClientRect();"
            "return {x:b.left+b.width/2,y:b.top+b.height/2,w:b.width,h:b.height,text:(best.innerText||best.value||'').slice(0,200)};})()"
        )
        end = time.time() + timeout
        while time.time() < end:
            self.check_stop()
            try:
                rect = self.eval(js)
                if isinstance(rect, dict) and rect.get("w", 0) > 0:
                    return rect
            except Exception:
                pass
            time.sleep(0.25)
        return None

    def _mouse_click(self, x: float, y: float) -> None:
        common = {"x": x, "y": y, "button": "left", "clickCount": 1}
        self._client.send("Input.dispatchMouseEvent", dict(common, type="mouseMoved", buttons=0))
        self._client.send("Input.dispatchMouseEvent", dict(common, type="mousePressed", buttons=1))
        self.wait(0.06)
        self._client.send("Input.dispatchMouseEvent", dict(common, type="mouseReleased", buttons=0))

    def click(self, selector: str, timeout: float = 15) -> None:
        rect = self._rect(selector, timeout=timeout)
        if not rect:
            raise ElementNotFound("未找到可点击元素：" + selector)
        self.wait(0.15)
        try:
            self._mouse_click(float(rect["x"]), float(rect["y"]))
        finally:
            try:
                self.eval("(function(){var e=document.querySelector('[data-yb-target=\"1\"]');if(e)e.removeAttribute('data-yb-target');})()")
            except Exception:
                pass

    def click_at(self, x: float, y: float) -> None:
        if self._client is None:
            raise BrowserError("浏览器尚未启动。")
        self.wait(0.08)
        self._mouse_click(float(x), float(y))

    def click_at_system(self, x: float, y: float) -> bool:
        """真实鼠标点击（Windows SendInput）：换算到屏幕坐标后移动+按下。"""
        proc = getattr(self, "_proc", None)
        if proc is None:
            return False
        try:
            from ..sysmouse import click_client_point, find_window
        except Exception:
            return False
        try:
            hwnd = find_window(int(proc.pid))
            if not hwnd:
                return False
            view = self.eval("({w:window.innerWidth,h:window.innerHeight})") or {}
            return bool(
                click_client_point(
                    hwnd, float(x), float(y),
                    float(view.get("w") or 0),
                    float(view.get("h") or 0),
                )
            )
        except Exception:
            return False

    def drag(self, x1: float, y1: float, x2: float, y2: float, duration: float = 1.0) -> None:
        """真实鼠标拖动：先按下，再按缓动曲线分步移动，最后松开。"""
        if self._client is None:
            raise BrowserError("浏览器尚未启动。")
        duration = max(0.3, min(6.0, float(duration)))
        steps = max(16, int(duration * 45))
        base = {"button": "left", "clickCount": 1}
        self._client.send("Input.dispatchMouseEvent", dict(base, type="mouseMoved", x=float(x1), y=float(y1), buttons=0))
        self.wait(0.12)
        self._client.send("Input.dispatchMouseEvent", dict(base, type="mousePressed", x=float(x1), y=float(y1), buttons=1))
        self.wait(0.08)
        for index in range(1, steps + 1):
            self.check_stop()
            t = index / float(steps)
            eased = 1.0 - (1.0 - t) ** 3
            x = float(x1) + (float(x2) - float(x1)) * eased + random.uniform(-0.7, 0.7)
            y = float(y1) + (float(y2) - float(y1)) * eased + random.uniform(-0.5, 0.5)
            self._client.send(
                "Input.dispatchMouseEvent",
                dict(base, type="mouseMoved", x=x, y=y, buttons=1),
            )
            time.sleep(duration / steps)
        self._client.send("Input.dispatchMouseEvent", dict(base, type="mouseReleased", x=float(x2), y=float(y2), buttons=0))
        self.wait(0.2)

    def element_screenshot(self, selector: str, path: str, scale: float = 1.0) -> bool:
        if self._client is None:
            return False
        sel = json.dumps(selector, ensure_ascii=False)
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel + "));"
            "for(var i=0;i<els.length;i++){var e=els[i],r=e.getBoundingClientRect(),s=getComputedStyle(e);"
            "if(r.width>4&&r.height>4&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0'){"
            "return {x:r.left,y:r.top,w:r.width,h:r.height};}}return null;})()"
        )
        try:
            rect = self.eval(js)
        except Exception:
            return False
        if not isinstance(rect, dict) or float(rect.get("w") or 0) <= 0:
            return False
        clip = {
            "x": float(rect["x"]),
            "y": float(rect["y"]),
            "width": float(rect["w"]),
            "height": float(rect["h"]),
            "scale": max(1.0, min(4.0, float(scale or 1.0))),
        }
        try:
            result = self._client.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": False, "clip": clip},
                timeout=20,
            )
        except Exception:
            return False
        data = result.get("data") or ""
        if not data:
            return False
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(data))
        return True

    def get_cookies(self) -> Dict[str, str]:
        """优先走 Network.getCookies（能拿到 HttpOnly），失败再退回 document.cookie。"""
        if self._client is not None:
            try:
                self._client.send("Network.enable", {}, timeout=10)
                result = self._client.send(
                    "Network.getCookies",
                    {"urls": ["https://www.yiban.cn/", "https://s.yiban.cn/"]},
                    timeout=10,
                )
                cookies: Dict[str, str] = {}
                for item in result.get("cookies") or []:
                    if not isinstance(item, dict):
                        continue
                    domain = str(item.get("domain") or "")
                    name = str(item.get("name") or "")
                    if name and "yiban" in domain:
                        cookies[name] = str(item.get("value") or "")
                if cookies:
                    return cookies
            except Exception:
                pass
        return super().get_cookies()

    def fill(self, selector: str, value: str, timeout: float = 15) -> None:
        if not self.wait_for_selector(selector, timeout=timeout, visible=True):
            raise ElementNotFound("未找到输入框：" + selector)
        sel = json.dumps(selector, ensure_ascii=False)
        val = json.dumps(value, ensure_ascii=False)
        js = (
            "(function(){var el=document.querySelector(" + sel + ");"
            "if(!el){throw new Error('input not found');}"
            "el.focus();var proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
            "var d=Object.getOwnPropertyDescriptor(proto,'value');"
            "if(d&&d.set){d.set.call(el," + val + ");}else{el.value=" + val + ";}"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));"
            "return el.value;})()"
        )
        self.eval(js)
        self.wait(0.15)

    def fill_contenteditable(self, selector: str, value: str, timeout: float = 20) -> None:
        sel = json.dumps(selector, ensure_ascii=False)
        best_js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel + "));"
            "var best=null;var bestScore=-1;for(var i=0;i<els.length;i++){var e=els[i];var r=e.getBoundingClientRect();"
            "var s=getComputedStyle(e);var vis=r.width>2&&r.height>2&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0';"
            "if(!vis)continue;var score=0;if(e.getAttribute('data-slate-editor'))score+=4;"
            "if(e.closest('.w-e-textarea'))score+=3;if(e.getAttribute('contenteditable')==='true')score+=2;"
            "if(e.getAttribute('role')==='textarea')score+=1;"
            "if(score>bestScore){bestScore=score;best=e;}}"
            "if(!best)return false;best.setAttribute('data-yb-editor','1');best.focus();"
            "var range=document.createRange();range.selectNodeContents(best);var sel2=window.getSelection();"
            "sel2.removeAllRanges();sel2.addRange(range);return true;})()"
        )
        end = time.time() + timeout
        ok = False
        while time.time() < end:
            self.check_stop()
            try:
                if self.eval(best_js):
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(0.3)
        if not ok:
            raise ElementNotFound("未找到正文富文本编辑器：" + selector)
        self.wait(0.2)
        try:
            self._client.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
            self._client.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8, "nativeVirtualKeyCode": 8})
        except Exception:
            pass
        self.wait(0.15)
        try:
            self._client.send("Input.insertText", {"text": value})
        except Exception as exc:
            self._emit("warn", "Input.insertText 失败，尝试 execCommand：" + str(exc))
        self.wait(0.5)
        actual = str(self.eval("(function(){var e=document.querySelector('[data-yb-editor=\"1\"]');return e?(e.innerText||e.textContent||''):'';})()") or "")
        if not actual.strip():
            val = json.dumps(value, ensure_ascii=False)
            fallback = (
                "(function(){var e=document.querySelector('[data-yb-editor=\"1\"]');if(!e)return '';e.focus();"
                "document.execCommand('insertText',false," + val + ");return e.innerText||e.textContent||'';})()"
            )
            actual = str(self.eval(fallback) or "")
            self.wait(0.3)
        if not actual.strip():
            html = "".join("<p>" + line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</p>" for line in value.split("\n"))
            html_lit = json.dumps(html, ensure_ascii=False)
            fallback = (
                "(function(){var e=document.querySelector('[data-yb-editor=\"1\"]');if(!e)return '';e.focus();"
                "document.execCommand('insertHTML',false," + html_lit + ");return e.innerText||e.textContent||'';})()"
            )
            actual = str(self.eval(fallback) or "")
        if not actual.strip():
            if self._fill_editor_in_frames(value):
                return
            raise BrowserError("正文编辑器未能写入内容，可能是页面结构已变化。")
        try:
            self.eval("(function(){var e=document.querySelector('[data-yb-editor=\"1\"]');if(e)e.removeAttribute('data-yb-editor');})()")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # iframe / 编辑器兜底
    # ------------------------------------------------------------------

    def _child_frames(self) -> list:
        if self._client is None:
            return []
        try:
            tree = self._client.send("Page.getFrameTree", {}, timeout=10)
        except Exception:
            return []
        frames = []

        def walk(node, is_root: bool) -> None:
            frame = (node or {}).get("frame") or {}
            if not is_root and frame.get("id"):
                frames.append(frame)
            for child in (node or {}).get("childFrames") or []:
                walk(child, False)

        walk(tree.get("frameTree") or {}, True)
        return frames

    def _frame_eval(self, frame_id: str, expression: str):
        context_id = None
        try:
            created = self._client.send(
                "Page.createIsolatedWorld",
                {"frameId": frame_id, "worldName": "yiban-autoposter", "grantUniveralAccess": True},
                timeout=10,
            )
            context_id = created.get("executionContextId")
        except Exception:
            context_id = None
        params = {"expression": expression, "returnByValue": True, "awaitPromise": False}
        if context_id is not None:
            params["contextId"] = context_id
        return self._client.send("Runtime.evaluate", params, timeout=15)

    def _fill_editor_in_frames(self, value: str) -> bool:
        """主文档找不到编辑器时，逐个 iframe 尝试（含 iframe 内 contenteditable）。"""
        for frame in self._child_frames():
            self.check_stop()
            frame_id = str(frame.get("id") or "")
            if not frame_id:
                continue
            focus_js = (
                "(function(){var els=Array.prototype.slice.call(document.querySelectorAll("
                "'[contenteditable=\"true\"], [contenteditable=\"\"], body'));"
                "for(var i=0;i<els.length;i++){var e=els[i],r=e.getBoundingClientRect();"
                "if(e.isContentEditable||e.tagName==='BODY'){"
                " if(!e.isContentEditable && e.tagName!=='BODY') continue;"
                " e.setAttribute('data-yb-frame-editor','1'); e.focus();"
                " var rg=document.createRange(); rg.selectNodeContents(e);"
                " var s=window.getSelection(); s.removeAllRanges(); s.addRange(rg);"
                " return true;}}}return false;})()"
            )
            try:
                result = self._frame_eval(frame_id, focus_js)
                focused = bool(((result.get("result") or {}).get("value")))
            except Exception:
                continue
            if not focused:
                continue
            self._emit("warn", "正文编辑器位于 iframe 内，已切换到 iframe 填写。")
            self.wait(0.2)
            try:
                self._client.send("Input.insertText", {"text": value})
            except Exception:
                pass
            self.wait(0.5)
            check_js = (
                "(function(){var e=document.querySelector('[data-yb-frame-editor=\"1\"]');"
                "if(!e)return '';return (e.innerText||e.textContent||'');})()"
            )
            try:
                checked = self._frame_eval(frame_id, check_js)
                text = str(((checked.get("result") or {}).get("value")) or "")
            except Exception:
                text = ""
            if text.strip():
                return True
            # 再试 execCommand
            fallback_js = (
                "(function(){var e=document.querySelector('[data-yb-frame-editor=\"1\"]');"
                "if(!e)return '';e.focus();document.execCommand('insertText',false,%s);"
                "return (e.innerText||e.textContent||'');})()" % json.dumps(value, ensure_ascii=False)
            )
            try:
                checked = self._frame_eval(frame_id, fallback_js)
                text = str(((checked.get("result") or {}).get("value")) or "")
            except Exception:
                text = ""
            if text.strip():
                return True
        return False

    def screenshot(self, path: str, full_page: bool = False) -> str:
        if self._client is None:
            raise BrowserError("浏览器尚未启动。")
        params: Dict[str, Any] = {"format": "png", "fromSurface": True}
        if full_page:
            params["captureBeyondViewport"] = True
        result = self._client.send("Page.captureScreenshot", params, timeout=30)
        data = result.get("data") or ""
        if not data:
            raise BrowserError("截图失败：浏览器没有返回图像数据。")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(data))
        return str(target)

    def save_html(self, path: str) -> str:
        html = str(self.eval("document.documentElement ? document.documentElement.outerHTML : ''") or "")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8", errors="replace")
        return str(target)

    def bring_to_front(self) -> None:
        if self._client is None:
            return
        # 先恢复窗口（最小化时仅 bringToFront 不会还原），再激活标签页。
        # Page.bringToFront 能聚焦标签，但不一定把整个 Edge 窗口抬到最前；
        # 后面的 Windows 前台激活由 GUI 侧补充。
        try:
            if self._target_id:
                win = self._client.send("Browser.getWindowForTarget", {"targetId": self._target_id}, timeout=10)
                window_id = win.get("windowId")
                if window_id is not None:
                    self._client.send(
                        "Browser.setWindowBounds",
                        {"windowId": window_id, "bounds": {"windowState": "normal"}},
                        timeout=10,
                    )
        except Exception:
            pass
        try:
            self._client.send("Page.bringToFront", {}, timeout=10)
        except Exception:
            pass

    def press_key(self, key: str, code: Optional[str] = None) -> None:
        mapping = {
            "Enter": 13,
            "Tab": 9,
            "Escape": 27,
            "Backspace": 8,
            "ArrowDown": 40,
            "ArrowUp": 38,
        }
        vk = mapping.get(key, 0)
        params = {"key": key, "code": code or key, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
        self._client.send("Input.dispatchKeyEvent", dict(params, type="keyDown"))
        self._client.send("Input.dispatchKeyEvent", dict(params, type="keyUp"))
