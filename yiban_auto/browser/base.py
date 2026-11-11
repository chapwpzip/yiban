# -*- coding: utf-8 -*-
"""浏览器引擎公共接口。"""
from __future__ import annotations

import abc
import json
import time
from typing import Any, Dict, Optional


class BrowserError(RuntimeError):
    pass


class StopRequested(RuntimeError):
    pass


class ElementNotFound(BrowserError):
    pass


class BrowserEngine(abc.ABC):
    name = "base"

    def __init__(self, settings: Dict[str, Any], log):
        self.settings = settings
        self.log = log
        self.stop_event = None

    def bind_stop(self, event) -> None:
        self.stop_event = event

    def check_stop(self) -> None:
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopRequested()

    @abc.abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def navigate(self, url: str, timeout: float = 45) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def eval(self, expression: str, await_promise: bool = False) -> Any:
        raise NotImplementedError

    @abc.abstractmethod
    def current_url(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def title(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def click(self, selector: str, timeout: float = 15) -> None:
        raise NotImplementedError

    def click_at(self, x: float, y: float) -> None:
        """按页面坐标点击；具体引擎可覆盖为真实鼠标事件。"""
        expression = (
            "(function(){var e=document.elementFromPoint(" + str(float(x)) + "," + str(float(y)) + ");"
            "if(!e)return false;e.click();return true;})()"
        )
        if not self.eval(expression):
            raise ElementNotFound("坐标 (" + str(x) + ", " + str(y) + ") 处没有可点击元素")

    def click_at_dom(self, x: float, y: float) -> str:
        """DOM 兜底点击：向命中元素派发完整的 pointer/mouse/click 事件链。

        数美这类控件只监听 pointerdown/mousedown 而不是 click，单用 `element.click()`
        经常没反应；这里把事件链补全，并在命中元素内部再往下钻几层，
        避免点到 `pointer-events` 空壳层上。
        """
        expression = """
        (function(px, py){
          var el = document.elementFromPoint(px, py);
          if (!el) return '';
          var depth = 0;
          while (el && el.firstElementChild && depth < 6) {
            var kids = el.children, hit = null;
            for (var i = 0; i < kids.length; i++) {
              var r = kids[i].getBoundingClientRect();
              if (px >= r.left && px <= r.right && py >= r.top && py <= r.bottom) { hit = kids[i]; break; }
            }
            if (!hit) break;
            el = hit; depth++;
          }
          var o = {bubbles:true, cancelable:true, composed:true, clientX:px, clientY:py,
                   screenX:px, screenY:py, view:window, button:0, buttons:1, detail:1};
          function pe(t, b){ try { el.dispatchEvent(new PointerEvent(t, Object.assign({}, o, {buttons:b}))); } catch(e){} }
          function me(t, b){ try { el.dispatchEvent(new MouseEvent(t, Object.assign({}, o, {buttons:b}))); } catch(e){} }
          pe('pointerover', 1); pe('pointerenter', 1); pe('pointermove', 1); pe('pointerdown', 1);
          me('mouseover', 1); me('mousemove', 1); me('mousedown', 1);
          pe('pointerup', 0); me('mouseup', 0); me('click', 0);
          try { el.click(); } catch(e){}
          return el.tagName + (el.className ? '.' + String(el.className).split(' ')[0] : '');
        })(%s, %s)
        """ % (repr(float(x)), repr(float(y)))
        try:
            value = self.eval(expression)
            return str(value or "")
        except Exception:
            return ""

    @abc.abstractmethod
    def fill(self, selector: str, value: str, timeout: float = 15) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def fill_contenteditable(self, selector: str, value: str, timeout: float = 15) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def screenshot(self, path: str, full_page: bool = False) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def save_html(self, path: str) -> str:
        raise NotImplementedError

    def get_cookies(self) -> Dict[str, str]:
        """取当前页面 Cookie（name -> value），供 HTTP 复核等旁路请求复用登录态。"""
        try:
            raw = self.eval("document.cookie") or ""
        except Exception:
            return {}
        cookies: Dict[str, str] = {}
        for part in str(raw).split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            name = name.strip()
            if name:
                cookies[name] = value.strip()
        return cookies

    def drag(self, x1: float, y1: float, x2: float, y2: float, duration: float = 1.0) -> None:
        """按住 (x1,y1) 拖动到 (x2,y2)，用于滑块验证。具体引擎覆盖实现。"""
        raise BrowserError("当前浏览器引擎不支持鼠标拖动。")

    def click_at_system(self, x: float, y: float) -> bool:
        """用系统级真实鼠标点击视口坐标 (x, y)；不支持时返回 False。"""
        return False

    def lock_window(self, hwnd: int) -> bool:
        """手动指定浏览器窗口句柄（GUI 捕捉浏览器用）；不支持的引擎返回 False。"""
        return False

    def element_screenshot(self, selector: str, path: str, scale: float = 1.0) -> bool:
        """截取单个元素；scale>1 可放大截图（提高验证码识别率）。"""
        return False

    def is_alive(self) -> bool:
        """浏览器控制通道是否仍然可用；默认认为存活，具体引擎可覆盖。"""
        return True

    def wait(self, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            self.check_stop()
            time.sleep(min(0.2, max(0.0, end - time.time())))

    def wait_ready(self, timeout: float = 45) -> None:
        end = time.time() + timeout
        while time.time() < end:
            self.check_stop()
            try:
                state = self.eval("document.readyState")
                if state in ("interactive", "complete"):
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise BrowserError("页面加载超时。")

    def wait_for_selector(self, selector: str, timeout: float = 15, visible: bool = True) -> bool:
        end = time.time() + timeout
        sel = json.dumps(selector, ensure_ascii=False)
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel + "));"
            "for(var i=0;i<els.length;i++){var el=els[i];"
            "if(" + ("true" if not visible else "false") + ") return true;"
            "var r=el.getBoundingClientRect();var s=getComputedStyle(el);"
            "var vis=r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0';"
            "if(vis) return true;}return false;})()"
        )
        while time.time() < end:
            self.check_stop()
            try:
                if self.eval(js):
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def wait_for_text(self, text: str, timeout: float = 15) -> bool:
        end = time.time() + timeout
        needle = json.dumps(text, ensure_ascii=False)
        js = "(document.body ? (document.body.innerText||'') : '').indexOf(" + needle + ") !== -1"
        while time.time() < end:
            self.check_stop()
            try:
                if self.eval(js):
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def wait_until(self, expression: str, timeout: float = 15, interval: float = 0.3) -> bool:
        end = time.time() + max(0.0, timeout)
        while time.time() < end:
            self.check_stop()
            try:
                if self.eval(expression):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def exists(self, selector: str, visible: bool = False) -> bool:
        sel = json.dumps(selector, ensure_ascii=False)
        if visible:
            js = (
                "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel + "));"
                "for(var i=0;i<els.length;i++){var el=els[i];var r=el.getBoundingClientRect();"
                "var s=getComputedStyle(el);if(r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'&&s.opacity!=='0')return true;}"
                "return false;})()"
            )
        else:
            js = "!!document.querySelector(" + sel + ")"
        try:
            return bool(self.eval(js))
        except Exception:
            return False

    def get_text(self, selector: str) -> str:
        sel = json.dumps(selector, ensure_ascii=False)
        return str(self.eval("(function(){var e=document.querySelector(" + sel + ");return e?(e.innerText||e.textContent||''):'';})()") or "")

    def get_value(self, selector: str) -> str:
        sel = json.dumps(selector, ensure_ascii=False)
        return str(self.eval("(function(){var e=document.querySelector(" + sel + ");return e?(e.value!==undefined?e.value:''):'';})()") or "")

    def page_text(self) -> str:
        try:
            return str(self.eval("document.body ? (document.body.innerText||'') : ''") or "")
        except Exception:
            return ""

    def get_network(self) -> list:
        try:
            data = self.eval("window.__YB_NET__ || []")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def clear_network(self) -> None:
        try:
            self.eval("window.__YB_NET__ = []")
        except Exception:
            pass

    def click_js(self, selector: str) -> bool:
        sel = json.dumps(selector, ensure_ascii=False)
        js = (
            "(function(){var el=document.querySelector(" + sel + ");if(!el)return false;"
            "el.scrollIntoView({block:'center',inline:'center'});"
            "el.click();return true;})()"
        )
        return bool(self.eval(js))

    def click_by_text(self, selector: str, text: str, timeout: float = 15) -> None:
        end = time.time() + timeout
        sel_lit = json.dumps(selector, ensure_ascii=False)
        text_lit = json.dumps(text, ensure_ascii=False)
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel_lit + "));"
            "var t=(" + text_lit + ").replace(/\\s+/g,'');"
            "for(var i=0;i<els.length;i++){var e=els[i];var s=(e.innerText||e.textContent||'').replace(/\\s+/g,'');"
            "if(s&&s.indexOf(t)!==-1){e.setAttribute('data-yb-click-target','1');"
            "e.scrollIntoView({block:'center',inline:'center'});return true;}}return false;})()"
        )
        while time.time() < end:
            self.check_stop()
            if self.eval(js):
                self.wait(0.15)
                self.click("[data-yb-click-target='1']")
                try:
                    self.eval("(function(){var e=document.querySelector('[data-yb-click-target=\"1\"]');if(e)e.removeAttribute('data-yb-click-target');})()")
                except Exception:
                    pass
                return
            time.sleep(0.3)
        raise ElementNotFound("未找到文本包含「" + text + "」的 " + selector + " 元素")
