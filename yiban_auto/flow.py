# -*- coding: utf-8 -*-
"""易班登录与微社区发帖流程。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from .browser.base import BrowserError, ElementNotFound, StopRequested
from .captcha import CaptchaSolver
from .util import file_stamp, normalize_space, screenshot_dir, truncate, write_json_atomic


LOGIN_URL = "https://www.yiban.cn/login"
CREATE_URL = "https://s.yiban.cn/bbs/post/create"

TITLE_SELECTOR = "input[placeholder*='帖子标题'], input[placeholder*='标题'], input[maxlength='30'], input[name='title']"
SUMMARY_SELECTOR = "textarea[placeholder*='摘要'], textarea[maxlength='200']"
BODY_SELECTOR = (
    ".w-e-textarea [contenteditable='true'], [data-slate-editor='true'], "
    ".post-form-view div[contenteditable='true'], div[role='textarea'][contenteditable='true']"
)
PUBLISH_SELECTOR = "button.btn-publish, .btn-publish"
SHUMEI_SELECTOR = (
    "iframe[src*='shumei'], iframe[src*='sm-captcha'], [id*='shumei_captcha'], "
    "[class*='shumei_captcha'], [id*='sm-captcha'], [class*='sm-captcha']"
)
# 只匹配验证码弹层本身，不匹配外层 shumei_<epoch>_pannel 容器。
# 外层容器在验证码隐藏后可能仍有尺寸，若用它判断会出现“验证已完成却一直提示”的问题。
SHUMEI_POPUP_SELECTOR = (
    ".shumei_captcha_popup_wrapper, .shumei_captcha_wrapper, "
    "[class*='shumei_captcha_popup_wrapper'], [class*='shumei_captcha_wrapper']"
)
IMAGE_CAPTCHA_SELECTOR = "#login-captcha, .login-captcha"
LOGIN_ERROR_SELECTOR = ".js-error, .ui-alert, .error"
DIALOG_SCOPE = ".mdc-dialog button, .ui-dialog button, .post-dialog button, .mdc-dialog a, .ui-dialog a"
TOAST_SELECTORS = (
    ".mdc-snackbar__label, .ui-toast, .ui-message, .el-message, "
    "[class*='toast'] [class*='content'], [class*='message__content']"
)
ERROR_TEXT_SELECTORS = (
    ".mdc-dialog__content, .ui-dialog__body, .post-dialog, .mdc-snackbar__label, "
    ".ui-toast, .ui-message, .ui-alert, .js-error"
)
CAPTCHA_HINTS = (
    "拖动滑块", "请完成验证", "请依次点击", "空间逻辑验证", "滑块验证",
    "短信验证", "身份验证", "安全验证", "人脸识别", "请完成下方验证",
)
SUCCESS_HINTS = ("成功发布帖子", "发布成功")
FAILURE_HINTS = (
    "失败", "错误", "不能为空", "请选择", "最多", "不能超过", "限制",
    "频率", "稍后再试", "稍后重试", "相同标题", "异常", "验证码错误",
)


@dataclass
class PostResult:
    ok: bool
    message: str
    detail: str = ""
    stage: str = ""
    screenshot: str = ""
    html: str = ""
    network: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


class FlowError(RuntimeError):
    pass


class YibanFlow:
    def __init__(self, engine, settings: Dict[str, Any], log: Callable[[str, str], None],
                 manual_gate: Optional[Callable[[str, float], bool]] = None,
                 status: Optional[Callable[[str, str], None]] = None):
        self.engine = engine
        self.settings = settings
        self.log = log
        self.manual_gate = manual_gate
        self.status_cb = status
        self._stage = ""
        self.captcha_solver = CaptchaSolver(settings, log)

    def _emit(self, level: str, message: str) -> None:
        try:
            self.log(level, message)
        except Exception:
            pass

    def _status(self, stage: str, message: str) -> None:
        self._stage = stage
        if self.status_cb:
            try:
                self.status_cb(stage, message)
            except Exception:
                pass
        self._emit("info", message)

    def _sleep(self, seconds: float) -> None:
        self.engine.check_stop()
        self.engine.wait(seconds)

    def _cfg(self, section: str, key: str, default=None):
        value = (self.settings.get(section) or {}).get(key)
        return default if value is None else value

    def _visible_text(self, selector: str) -> str:
        sel = json.dumps(selector, ensure_ascii=False)
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel + "));"
            "var out=[];for(var i=0;i<els.length;i++){var e=els[i];var r=e.getBoundingClientRect();"
            "var s=getComputedStyle(e);if(r.width>1&&r.height>1&&s.display!=='none'&&s.visibility!=='hidden'){"
            "var t=(e.innerText||e.textContent||'').trim();if(t)out.push(t);}}return out.join('\\n');})()"
        )
        try:
            return str(self.engine.eval(js) or "")
        except Exception:
            return ""

    def _body_text(self) -> str:
        try:
            return str(self.engine.eval("document.body ? (document.body.innerText || '') : ''") or "")
        except Exception:
            return ""

    def _is_login_url(self, url: str = "") -> bool:
        url = (url or self.engine.current_url() or "").lower()
        return "yiban.cn" in url and "login" in url

    def _is_create_url(self, url: str = "") -> bool:
        url = (url or self.engine.current_url() or "").lower()
        return "s.yiban.cn" in url and "/bbs" in url and "login" not in url

    def _create_ready(self) -> bool:
        try:
            if self._is_login_url():
                return False
            if not self._is_create_url():
                return False
            return self.engine.exists(TITLE_SELECTOR, visible=True)
        except Exception:
            return False

    def _wait_create_ready(self, timeout: float = 60) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self.engine.check_stop()
            if self._create_ready():
                return True
            if self._is_login_url():
                return False
            time.sleep(0.4)
        return False

    def _login_error_text(self) -> str:
        text = normalize_space(self._visible_text(LOGIN_ERROR_SELECTOR))
        if not text:
            text = normalize_space(self._visible_text(".ui-alert, .mdc-snackbar__label"))
        if text and len(text) < 300:
            return text
        return ""

    def _login_challenge_reason(self) -> str:
        if self.engine.exists(IMAGE_CAPTCHA_SELECTOR, visible=True):
            return "图片验证码"
        if self.engine.exists(SHUMEI_SELECTOR, visible=True):
            return "滑动或空间验证"
        text = self._body_text()
        for hint in CAPTCHA_HINTS:
            if hint in text:
                return hint
        return ""

    def _visible_challenge(self) -> str:
        """只检查仍然可见的验证控件，避免把页面里的说明文字当成验证框。"""
        try:
            if self.engine.exists(SHUMEI_POPUP_SELECTOR, visible=True):
                return "空间/滑块安全验证"
            if self.engine.exists(IMAGE_CAPTCHA_SELECTOR, visible=True):
                return "图片验证码"
        except Exception:
            pass
        return ""

    def _manual(self, message: str, allow_external: bool = False, attempt: int = 1) -> None:
        # 自动/外接验证只在明确的验证码场景尝试；短信/人脸/身份验证仍必须人工完成。
        if allow_external and self.captcha_solver.enabled:
            mode = self.captcha_solver.mode
            label = "自动验证" if mode == "auto" else "外接验证服务"
            self._emit("info", "检测到验证码，" + label + "处理中（模式：" + mode + "）。")
            self._status("manual", label + "处理中")
            try:
                solved = self.captcha_solver.attempt(self.engine, SHUMEI_POPUP_SELECTOR)
            except StopRequested:
                raise
            except Exception as exc:
                self._emit("warn", label + "异常：" + str(exc))
                solved = False
            if solved:
                self._emit("success", label + "已完成验证，继续提交。")
                self._status("manual", "自动验证完成，继续")
                return
            if not self.captcha_solver.fallback_manual:
                raise FlowError(
                    "自动验证未能完成验证，且设置中已关闭人工接管（captcha.fallback_manual=false）。"
                    "请在设置里开启人工接管，或检查验证码配置后重试。"
                )
            self._emit("warn", "自动验证未通过，转入人工接管。")
        if bool(self._cfg("browser", "headless", False)):
            raise FlowError("当前为无界面模式，但平台要求人工验证。请关闭设置中的 headless 后重试。")
        if self.manual_gate is None:
            raise FlowError("平台要求人工验证，但当前没有可用的界面提示。")
        self._emit("warn", message)
        self._status("manual", "等待人工完成验证")
        # 浏览器可能被工具窗口挡住；先切到前台，用户才能看到验证码。
        try:
            self.engine.bring_to_front()
        except Exception:
            pass
        total_timeout = float(self._cfg("advanced", "manual_timeout", 300))
        deadline = time.time() + total_timeout
        ok = False
        attempts = 0
        while attempts < 3:
            remaining = max(20.0, deadline - time.time())
            try:
                ok = bool(self.manual_gate(message, remaining))
            except StopRequested:
                raise
            except Exception as exc:
                raise FlowError("等待人工操作失败：" + str(exc))
            if not ok:
                raise FlowError("人工验证未完成或已取消。")
            # 用户可能点在验证码消失前就点了继续；确认页面真的没有可见验证框。
            self._sleep(0.8)
            remaining_challenge = self._visible_challenge()
            if not remaining_challenge:
                self._status("manual", "人工操作完成，继续执行")
                return
            attempts += 1
            if attempts >= 3 or time.time() >= deadline:
                raise FlowError("页面仍显示" + remaining_challenge + "，已停止等待。请完成验证后重试。")
            self._emit("warn", "页面仍显示" + remaining_challenge + "，请继续完成验证。")
            message = (
                "页面仍显示" + remaining_challenge + "。\n\n"
                "请回到浏览器完成验证，确认验证框已经消失后，再点击“已完成，继续”。"
            )
        raise FlowError("人工验证未完成或已取消。")

    def _navigate_login(self) -> None:
        url = LOGIN_URL + "?go=" + quote(CREATE_URL, safe="")
        self._status("login", "打开发帖页，等待登录跳转")
        self.engine.navigate(CREATE_URL, timeout=float(self._cfg("advanced", "page_timeout", 30)))
        self._sleep(1.5)
        if self._is_login_url():
            return
        if self._create_ready():
            return
        self.engine.navigate(url, timeout=float(self._cfg("advanced", "page_timeout", 30)))
        self._sleep(1.0)

    def _login_form_kind(self) -> str:
        if self.engine.exists("#account-txt", visible=True) and self.engine.exists("#password-txt", visible=True):
            return "www"
        account = "input[placeholder*='账号'], input[placeholder*='手机'], input[placeholder*='邮箱']"
        password = "input[type='password']"
        if self.engine.exists(account, visible=True) and self.engine.exists(password, visible=True):
            return "spa"
        return ""

    def _fill_login_form(self, account: str, password: str) -> str:
        kind = self._login_form_kind()
        if kind == "www":
            self.engine.fill("#account-txt", account, timeout=10)
            self.engine.fill("#password-txt", password, timeout=10)
            return "www"
        if kind == "spa":
            account_sel = "input[placeholder*='账号'], input[placeholder*='手机'], input[placeholder*='邮箱']"
            self.engine.fill(account_sel, account, timeout=10)
            self.engine.fill("input[type='password']", password, timeout=10)
            return "spa"
        raise FlowError("未找到登录表单，页面结构可能已变化。")

    def _submit_login(self, kind: str) -> None:
        if kind == "www":
            if self.engine.exists("#login-btn", visible=True):
                self.engine.click("#login-btn", timeout=10)
            else:
                self.engine.click_by_text("a, button", "登 录", timeout=5)
        else:
            try:
                self.engine.click_by_text("button", "登 录", timeout=5)
            except ElementNotFound:
                self.engine.click_by_text("button", "登录", timeout=5)

    def _handle_login(self, account: str, password: str) -> None:
        kind = self._fill_login_form(account, password)
        self._emit("info", "已填写账号 " + account[:3] + "***，提交登录")
        self._status("login", "正在登录")
        self._submit_login(kind)
        self._sleep(2.5)
        deadline = time.time() + float(self._cfg("advanced", "login_timeout", 300))
        gates = 0
        ocr_tries = 0
        last_gate_at = 0.0
        while time.time() < deadline:
            self.engine.check_stop()
            if self._create_ready():
                self._emit("info", "登录成功，已进入发帖页")
                return
            if not self._is_login_url():
                if self._is_create_url():
                    if self._wait_create_ready(30):
                        self._emit("info", "登录成功，已进入发帖页")
                        return
                self.engine.navigate(CREATE_URL, timeout=float(self._cfg("advanced", "page_timeout", 30)))
                self._sleep(1.5)
                continue
            error_text = self._login_error_text()
            reason = self._login_challenge_reason()
            if reason:
                if gates >= 3:
                    raise FlowError("登录验证多次未通过：" + (error_text or reason))
                # 图片验证码：先试内置 OCR（ddddocr），识别成功就重新提交登录。
                if "图片" in reason and ocr_tries < 3 and self.captcha_solver.auto_ocr:
                    ocr_tries += 1
                    if self.captcha_solver.solve_image_captcha(self.engine):
                        self._emit("info", "已填写图片验证码，重新提交登录。")
                        self._submit_login(kind)
                        self._sleep(2.5)
                        continue
                if time.time() - last_gate_at < 3:
                    time.sleep(0.5)
                    continue
                message = (
                    "登录需要人工验证（" + reason + "）。\n"
                    "请在浏览器中按页面提示完成滑块、图片验证码、短信或人脸验证。"
                )
                allow_external = not any(k in reason for k in ("短信", "人脸", "身份"))
                self._manual(message, allow_external=allow_external, attempt=gates + 1)
                gates += 1
                last_gate_at = time.time()
                deadline = time.time() + float(self._cfg("advanced", "login_timeout", 300))
                self._sleep(1.5)
                continue
            if error_text and any(k in error_text for k in ("账号", "密码", "错误", "失败", "不存在", "锁定", "频繁")):
                raise FlowError("登录失败：" + error_text)
            if time.time() - last_gate_at > 4 and gates < 3:
                message = (
                    "登录尚未完成。\n"
                    "若浏览器出现滑块、验证码、短信或身份验证，请先手动完成；"
                    "若提示账号或密码有误，请停止后检查。"
                )
                self._manual(message)
                gates += 1
                last_gate_at = time.time()
                deadline = time.time() + float(self._cfg("advanced", "login_timeout", 300))
            time.sleep(0.5)
        raise FlowError("登录超时，未进入发帖页。" + ((" 页面提示：" + self._login_error_text()) if self._login_error_text() else ""))

    def _ensure_create_page(self, account: str, password: str) -> None:
        self._status("login", "正在打开发帖页")
        self.engine.navigate(CREATE_URL, timeout=float(self._cfg("advanced", "page_timeout", 30)))
        self._sleep(2.0)
        if self._wait_create_ready(25):
            self._emit("info", "检测到已登录状态，直接进入发帖")
            return
        if not self._is_login_url():
            self._navigate_login()
        if not self._is_login_url():
            if self._wait_create_ready(25):
                return
            raise FlowError("打开发帖页后未跳转到登录页，请检查网络或页面结构。")
        if not self.engine.exists("#account-txt, input[type='password']", visible=True):
            self.engine.wait_for_selector("#account-txt, input[type='password']", timeout=20)
        self._handle_login(account, password)
        if not self._wait_create_ready(60):
            if self._is_login_url():
                raise FlowError("登录已完成，但平台仍停留在登录页。")
            self.engine.navigate(CREATE_URL, timeout=float(self._cfg("advanced", "page_timeout", 30)))
            self._sleep(1.5)
            if not self._wait_create_ready(45):
                raise FlowError("登录成功，但发帖页没有加载完成。")

    def _handle_draft_dialog(self) -> None:
        text = self._visible_text(".mdc-dialog, .ui-dialog, .post-dialog")
        if "草稿" not in text:
            return
        self._emit("info", "检测到草稿提示，选择不恢复旧草稿")
        for label in ("不恢复", "放弃", "删除草稿", "取消"):
            try:
                if self._click_text_if_present(DIALOG_SCOPE, label, timeout=1.5):
                    self._sleep(0.5)
                    return
            except Exception:
                pass
        try:
            self.engine.press_key("Escape")
        except Exception:
            pass
        self._sleep(0.3)

    def _click_text_if_present(self, selector: str, text: str, timeout: float = 2.0) -> bool:
        sel = json.dumps(selector, ensure_ascii=False)
        needle = json.dumps(text, ensure_ascii=False)
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel + "));"
            "var t=(" + needle + ").replace(/\\s+/g,'');"
            "for(var i=0;i<els.length;i++){var e=els[i];var s=(e.innerText||e.textContent||'').replace(/\\s+/g,'');"
            "if(s&&s.indexOf(t)!==-1)return true;}return false;})()"
        )
        try:
            if not self.engine.eval(js):
                return False
            self.engine.click_by_text(selector, text, timeout=timeout)
            return True
        except Exception:
            return False

    def _fill_title(self, title: str) -> None:
        if not self.engine.wait_for_selector(TITLE_SELECTOR, timeout=20, visible=True):
            raise FlowError("未找到标题输入框。")
        self.engine.fill(TITLE_SELECTOR, title, timeout=10)
        actual = normalize_space(self.engine.get_value(TITLE_SELECTOR))
        if normalize_space(title) not in actual:
            raise FlowError("标题写入后校验失败，当前值：" + truncate(actual, 60))
        self._emit("info", "已填写标题：" + title)

    def _editor_text(self) -> str:
        sel = json.dumps(BODY_SELECTOR, ensure_ascii=False)
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll(" + sel + "));"
            "for(var i=0;i<els.length;i++){var e=els[i];var r=e.getBoundingClientRect();"
            "var s=getComputedStyle(e);if(r.width>2&&r.height>2&&s.display!=='none'&&s.visibility!=='hidden'){"
            "return e.innerText||e.textContent||'';}}return '';})()"
        )
        try:
            return str(self.engine.eval(js) or "")
        except Exception:
            return ""

    def _fill_body(self, body: str) -> None:
        if not self.engine.wait_for_selector(BODY_SELECTOR, timeout=25, visible=True):
            raise FlowError("未找到正文编辑器。")
        last_error = ""
        for attempt in range(1, 3):
            try:
                self.engine.fill_contenteditable(BODY_SELECTOR, body, timeout=20)
                actual = self._editor_text()
                if actual.strip():
                    self._emit("info", "已填写正文（" + str(len(body)) + " 字）")
                    return
                last_error = "编辑器内容仍为空"
            except Exception as exc:
                last_error = str(exc)
                self._emit("warn", "正文写入第 " + str(attempt) + " 次失败：" + str(exc))
            self._sleep(0.8)
        raise FlowError("正文写入失败：" + last_error)

    def _fill_summary(self, summary: str) -> None:
        if not summary:
            return
        if self.engine.exists(SUMMARY_SELECTOR, visible=True):
            try:
                self.engine.fill(SUMMARY_SELECTOR, summary, timeout=5)
                self._emit("info", "已填写摘要")
            except Exception as exc:
                self._emit("warn", "摘要填写失败，将留空由页面自动截取：" + str(exc))

    def _mark_scope(self, label: str, key: str) -> bool:
        label_lit = json.dumps(label, ensure_ascii=False)
        key_lit = json.dumps(key, ensure_ascii=False)
        js = (
            "(function(){var label=(" + label_lit + ").replace(/\\s+/g,'');var key=" + key_lit + ";"
            "var all=document.querySelectorAll('body *');var best=null;"
            "for(var i=0;i<all.length;i++){var e=all[i];if(e.children&&e.children.length===0){"
            "var t=(e.innerText||e.textContent||'').replace(/\\s+/g,'').replace(/[：:]/g,'');"
            "if(t===label){best=e;break;}}}"
            "var target=null;if(best){var p=best;for(var d=0;d<6&&p;d++){"
            "var s=p.querySelector?p.querySelector('.mdc-select'):null;if(s){target=s;break;}p=p.parentElement;}}"
            "if(!target){var selects=document.querySelectorAll('.mdc-select');var idx=key==='community'?0:1;"
            "if(selects[idx])target=selects[idx];}"
            "if(!target)return false;var anchor=target.querySelector('.mdc-select__anchor')||target;"
            "anchor.setAttribute('data-yb-scope',key);return true;})()"
        )
        try:
            return bool(self.engine.eval(js))
        except Exception:
            return False

    def _selected_text(self, key: str) -> str:
        key_lit = json.dumps(key, ensure_ascii=False)
        js = (
            "(function(){var a=document.querySelector('[data-yb-scope='+" + key_lit + "+']');"
            "if(!a)return '';var s=a.closest?a.closest('.mdc-select'):a;"
            "var t=s?s.querySelector('.mdc-select__selected-text'):null;"
            "return t?(t.innerText||t.textContent||''):'';})()"
        )
        try:
            return normalize_space(str(self.engine.eval(js) or ""))
        except Exception:
            return ""

    def _scope_options(self) -> List[Dict[str, Any]]:
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll('.mdc-list-item'));"
            "var out=[];for(var i=0;i<els.length;i++){var e=els[i];var r=e.getBoundingClientRect();"
            "var s=getComputedStyle(e);if(!(r.width>1&&r.height>1&&s.display!=='none'&&s.visibility!=='hidden'))continue;"
            "var t=(e.innerText||e.textContent||'').trim();if(!t)continue;"
            "var dis=e.getAttribute('aria-disabled')==='true'||String(e.className||'').indexOf('mdc-list-item--disabled')!==-1||"
            "(e.getAttribute('data-value')||'').indexOf('__group_')===0;"
            "out.push({text:t,disabled:dis});}return out;})()"
        )
        try:
            data = self.engine.eval(js)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _choose_scope_option(self, desired: str) -> bool:
        options = self._scope_options()
        usable = [o for o in options if not o.get("disabled")]
        if not usable:
            return False
        index = -1
        want = normalize_space(desired)
        if want:
            for i, item in enumerate(options):
                if item.get("disabled"):
                    continue
                if normalize_space(str(item.get("text") or "")) == want:
                    index = i
                    break
            if index < 0:
                for i, item in enumerate(options):
                    if item.get("disabled"):
                        continue
                    if want in normalize_space(str(item.get("text") or "")):
                        index = i
                        break
            if index < 0:
                names = "、".join(normalize_space(str(o.get("text") or "")) for o in usable[:12])
                raise FlowError("下拉选项中没有“" + desired + "”。可选：" + names)
        else:
            for i, item in enumerate(options):
                if not item.get("disabled"):
                    index = i
                    break
        if index < 0:
            return False
        idx_lit = str(index)
        js = (
            "(function(){var els=Array.prototype.slice.call(document.querySelectorAll('.mdc-list-item'));"
            "var vis=[];for(var i=0;i<els.length;i++){var e=els[i];var r=e.getBoundingClientRect();"
            "var s=getComputedStyle(e);if(r.width>1&&r.height>1&&s.display!=='none')vis.push(e);}"
            "var e=vis[" + idx_lit + "];if(!e)return false;e.setAttribute('data-yb-option','1');return true;})()"
        )
        if not self.engine.eval(js):
            return False
        try:
            self.engine.click("[data-yb-option='1']", timeout=6)
        finally:
            try:
                self.engine.eval("(function(){var e=document.querySelector('[data-yb-option=\"1\"]');if(e)e.removeAttribute('data-yb-option');})()")
            except Exception:
                pass
        self._sleep(0.6)
        return True

    def _select_scope(self, label: str, key: str, desired: str) -> bool:
        if not self._mark_scope(label, key):
            self._emit("warn", "未找到“" + label + "”下拉框，可能页面结构已变化")
            return False
        current = self._selected_text(key)
        if desired:
            if normalize_space(desired) in current or current in normalize_space(desired):
                self._emit("info", label + "已选择：" + current)
                return True
        elif current:
            self._emit("info", label + "默认选择：" + current)
            return True
        self.engine.click("[data-yb-scope='" + key + "']", timeout=8)
        self._sleep(0.4)
        if not self.engine.wait_for_selector(".mdc-list-item", timeout=8, visible=True):
            raise FlowError("“" + label + "”下拉选项没有展开。")
        ok = self._choose_scope_option(desired)
        if not ok:
            raise FlowError("“" + label + "”没有可选项。")
        self.engine.wait(0.5)
        current = self._selected_text(key)
        if not current:
            raise FlowError("“" + label + "”选择后没有显示选中值。")
        self._emit("info", label + "已选择：" + current)
        return True

    def _prepare_scope(self) -> None:
        community = str(self._cfg("posting", "community", "") or "")
        board = str(self._cfg("posting", "board", "") or "")
        self._sleep(2.5)
        self._select_scope("发布范围", "community", community)
        self._sleep(2.0)
        try:
            self._select_scope("选择版块", "board", board)
        except FlowError:
            if board:
                raise
            self._emit("warn", "版块下拉不可用，依赖页面默认选择")
        self._sleep(0.5)

    def _fill_form(self, title: str, body: str, summary: str) -> None:
        self._status("form", "正在填写帖子表单")
        self._handle_draft_dialog()
        self._fill_title(title)
        self._fill_summary(summary)
        self._fill_body(body)
        self._prepare_scope()
        cover = int(self._cfg("posting", "cover_mode", 1) or 1)
        if cover != 1:
            raise FlowError("当前仅支持“无封面图”模式，请在设置中把 cover_mode 设为 1。")
        self._emit("info", "表单填写完成，准备发布")

    def _click_publish(self) -> None:
        if not self.engine.wait_for_selector(PUBLISH_SELECTOR, timeout=15, visible=True):
            try:
                self.engine.click_by_text("button", "发布", timeout=5)
                return
            except ElementNotFound:
                raise FlowError("未找到发布按钮。")
        disabled = False
        try:
            disabled = bool(self.engine.eval("(function(){var b=document.querySelector('button.btn-publish,.btn-publish');return b?(b.disabled||b.getAttribute('aria-disabled')==='true'):true;})()"))
        except Exception:
            pass
        if disabled:
            raise FlowError("发布按钮当前不可点击，请检查表单是否填写完整。")
        self.engine.clear_network()
        self.engine.click(PUBLISH_SELECTOR, timeout=10)

    def _network_items(self) -> List[Dict[str, Any]]:
        items = self.engine.get_network()
        return items if isinstance(items, list) else []

    @staticmethod
    def _parse_body(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        body = item.get("body")
        if isinstance(body, dict):
            return body
        text = str(body or "").strip()
        if not text or text[0] not in "{[":
            return None
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    def _network_findings(self) -> Dict[str, Any]:
        result = {"success": [], "captcha": [], "limit": [], "error": []}
        for item in self._network_items():
            obj = self._parse_body(item)
            if not obj:
                continue
            code = obj.get("code")
            if code is None:
                code = obj.get("status")
            data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
            if code is None and data:
                code = data.get("code")
            message = obj.get("message") or obj.get("msg") or data.get("message") or data.get("msg") or ""
            code_text = str(code)
            record = {"code": code_text, "message": str(message), "url": item.get("url", ""), "method": item.get("method", ""), "status": item.get("status")}
            if code_text in ("110",):
                result["captcha"].append(record)
            elif code_text in ("-1002", "1002"):
                result["limit"].append(record)
            elif code_text in ("0", "200") or obj.get("success") is True:
                method = str(item.get("method") or "").upper()
                url = str(item.get("url") or "")
                low_url = url.lower()
                # 只认帖子提交接口，避免把 security/token 等其它 POST 的 code=0 当成发帖成功。
                if method == "POST" and "post" in low_url and "getpostbyname" not in low_url:
                    result["success"].append(record)
            elif code_text not in ("", "None"):
                result["error"].append(record)
        return result

    def _success_evidence(self) -> Optional[str]:
        try:
            url = self.engine.current_url().lower()
        except Exception:
            url = ""
        if "mypost" in url or "my-post" in url:
            return "页面已跳转到我的帖子：" + url
        toast = normalize_space(self._visible_text(TOAST_SELECTORS))
        for hint in SUCCESS_HINTS:
            if hint in toast:
                return "页面提示：" + toast[:120]
        text = self._body_text()
        for hint in SUCCESS_HINTS:
            if hint in text:
                return "页面文本：" + hint
        findings = self._network_findings()
        if findings["success"]:
            record = findings["success"][-1]
            return "服务端返回 code=" + str(record.get("code")) + "，URL=" + str(record.get("url"))
        return None

    def _duplicate_dialog(self) -> bool:
        text = self._visible_text(".mdc-dialog, .ui-dialog, .post-dialog")
        if "相同标题" not in text:
            return False
        self._emit("warn", "平台提示存在相同标题的帖子，已选择返回修改以避免重复发布")
        for label in ("返回修改", "取消"):
            if self._click_text_if_present(DIALOG_SCOPE, label, timeout=2):
                break
        return True

    def _ambient_error(self) -> str:
        text = self._visible_text(ERROR_TEXT_SELECTORS)
        if not text:
            return ""
        lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
        for line in lines:
            if any(hint in line for hint in SUCCESS_HINTS):
                continue
            if any(hint in line for hint in FAILURE_HINTS):
                return truncate(line, 240)
        return ""

    def _wait_publish(self) -> Dict[str, Any]:
        deadline = time.time() + float(self._cfg("advanced", "publish_timeout", 90))
        gates = 0
        while time.time() < deadline:
            self.engine.check_stop()
            evidence = self._success_evidence()
            if evidence:
                return {"ok": True, "message": "发布成功", "evidence": evidence}
            if self._duplicate_dialog():
                return {"ok": False, "message": "平台提示已有相同标题的帖子，已停止提交。请修改标题后重试。"}
            findings = self._network_findings()
            if findings["limit"]:
                msg = findings["limit"][-1].get("message") or "平台频率限制"
                return {"ok": False, "message": "发布失败：平台返回频率限制（-1002）。" + str(msg) + " 请等待一段时间后重试。"}
            if findings["captcha"]:
                if gates >= 3:
                    return {"ok": False, "message": "发布时验证码多次未通过，请稍后重试。"}
                self._manual(
                    "发布时平台要求安全验证。\n请在浏览器中按页面提示完成滑块/空间验证或图片验证码。",
                    allow_external=True,
                    attempt=gates + 1,
                )
                gates += 1
                self.engine.clear_network()
                deadline = time.time() + float(self._cfg("advanced", "publish_timeout", 90))
                self._sleep(1.0)
                continue
            reason = self._login_challenge_reason()
            if reason and "登录" not in reason:
                if gates >= 3:
                    return {"ok": False, "message": "发布时安全验证多次未通过。"}
                self._manual(
                    "发布时检测到安全验证（" + reason + "）。\n请在浏览器中完成验证。",
                    allow_external=True,
                    attempt=gates + 1,
                )
                gates += 1
                self.engine.clear_network()
                deadline = time.time() + float(self._cfg("advanced", "publish_timeout", 90))
                continue
            if findings["error"]:
                record = findings["error"][-1]
                message = str(record.get("message") or "").strip() or ("服务端 code=" + str(record.get("code")))
                return {"ok": False, "message": "发布失败：" + truncate(message, 240)}
            ambient = self._ambient_error()
            if ambient:
                return {"ok": False, "message": "发布失败：" + ambient}
            time.sleep(0.6)
        return {"ok": False, "message": "提交后未检测到明确的成功或失败结果，请查看截图和浏览器页面确认。"}

    def _dump(self) -> Dict[str, str]:
        paths = {"screenshot": "", "html": "", "network": ""}
        if not bool(self._cfg("advanced", "screenshot_on_error", True)):
            return paths
        stamp = file_stamp()
        folder = screenshot_dir()
        try:
            paths["screenshot"] = str(self.engine.screenshot(str(folder / ("post-" + stamp + ".png"))))
        except Exception as exc:
            self._emit("warn", "截图失败：" + str(exc))
        try:
            paths["html"] = str(self.engine.save_html(str(folder / ("post-" + stamp + ".html"))))
        except Exception as exc:
            self._emit("warn", "保存页面 HTML 失败：" + str(exc))
        try:
            network = self._network_items()
            target = folder / ("post-" + stamp + ".json")
            write_json_atomic(target, network)
            paths["network"] = str(target)
        except Exception as exc:
            self._emit("warn", "保存网络记录失败：" + str(exc))
        if paths["screenshot"]:
            self._emit("info", "失败现场截图：" + paths["screenshot"])
        return paths

    def _http_verify(self, title: str) -> str:
        """发帖成功后的旁路复核（浏览器 Cookie → HTTP）。任何异常都不影响主结果。"""
        if not bool(self._cfg("posting", "verify_via_http", True)):
            return ""
        url = str(self._cfg("posting", "verify_url", "https://s.yiban.cn/bbs/mypost") or "").strip()
        if not url:
            return ""
        try:
            from .verify import verify_published_post

            result = verify_published_post(self.engine, url, title, timeout=12.0)
        except Exception as exc:
            self._emit("warn", "HTTP 复核异常：" + str(exc))
            return ""
        note = str(result.get("note") or "")
        if result.get("found"):
            self._emit("info", "HTTP 复核：" + note)
        elif result.get("checked"):
            self._emit("info", "HTTP 复核：" + note)
        else:
            self._emit("warn", "HTTP 复核：" + (note or "未执行"))
        return note

    def publish(self, account: str, password: str, title: str, body: str, summary: str = "") -> PostResult:
        from .content import validate_post
        try:
            validate_post(title, body)
        except Exception as exc:
            return PostResult(False, "内容校验失败：" + str(exc), stage="validate")
        try:
            self._ensure_create_page(account, password)
            self._fill_form(title, body, summary)
            self._status("publish", "正在提交帖子")
            self._click_publish()
            outcome = self._wait_publish()
            if outcome.get("ok"):
                evidence = str(outcome.get("evidence") or "")
                verify_note = self._http_verify(title)
                if verify_note:
                    evidence = (evidence + "；" if evidence else "") + verify_note
                self._status("done", "发布成功")
                self._emit("info", "发布结果：成功。证据：" + evidence)
                return PostResult(True, str(outcome.get("message") or "发布成功"),
                                  evidence={"evidence": evidence}, stage="done")
            self._status("failed", "发布失败")
            paths = self._dump()
            self._emit("error", str(outcome.get("message") or "发布失败"))
            return PostResult(False, str(outcome.get("message") or "发布失败"), stage=self._stage,
                              screenshot=paths.get("screenshot", ""), html=paths.get("html", ""),
                              network=paths.get("network", ""))
        except StopRequested:
            raise
        except FlowError as exc:
            paths = self._dump()
            self._emit("error", str(exc))
            return PostResult(False, str(exc), stage=self._stage, screenshot=paths.get("screenshot", ""),
                              html=paths.get("html", ""), network=paths.get("network", ""))
        except BrowserError as exc:
            paths = self._dump()
            self._emit("error", "浏览器操作失败：" + str(exc))
            return PostResult(False, "浏览器操作失败：" + str(exc), stage=self._stage,
                              screenshot=paths.get("screenshot", ""), html=paths.get("html", ""),
                              network=paths.get("network", ""))
        except Exception as exc:
            paths = self._dump()
            self._emit("error", "未预期错误：" + str(exc))
            return PostResult(False, "未预期错误：" + str(exc), stage=self._stage,
                              screenshot=paths.get("screenshot", ""), html=paths.get("html", ""),
                              network=paths.get("network", ""))
