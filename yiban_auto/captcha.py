# -*- coding: utf-8 -*-
"""验证码自动处理：默认 auto（自动尝试 + 失败回退人工），也可外接服务。

三种模式：
    auto     先尝试自动处理：外接服务（若配置）→ 图片验证码 OCR（可选 ddddocr）
             → 滑块轨迹模拟 → 按 attempts 重试；仍失败则按 fallback_manual 转人工。
    manual   只人工接管。
    http     只调用用户自己的 HTTP 识别服务。
    command  只调用用户自己的本地程序。

自动处理是"尽力而为"：易班登录会出现数美滑块（空间/滑块验证）、图片验证码、
短信验证、人脸识别；后两者无法自动完成，会直接转人工（或按配置报错）。
滑块轨迹模拟只对常见滑块实现有效，平台升级后可能失效 —— 失败会按配置重试并回退。

两种接入方式：
    http    POST JSON 到用户配置的 URL，返回 JSON。
    command 启动用户配置的程序，JSON 从 stdin 传入，JSON 从 stdout 返回。

请求字段（HTTP body / 外部程序 stdin）：
    {
      "type": "yiban_captcha",
      "page_url": "https://s.yiban.cn/bbs/post/create",
      "instruction": "页面验证码区域可见文字（可能为空）",
      "image_base64": "PNG 的 base64",
      "image_width": 1280,
      "image_height": 720,
      "viewport_width": 1280,
      "viewport_height": 720,
      "attempt": 1,
      "image_path": "本机留存的截图路径，便于调试"
    }

返回字段（HTTP body / 外部程序 stdout）：
    {"points": [[x, y], [x, y]], "message": "可选说明"}
    points 使用截图像素坐标，按顺序点击。主程序会按
    viewport_width / image_width 换算成页面坐标。
    只需要点击型验证（例如“点击图中最小的蓝色长方体”）；滑块拖动不在本接口范围内。

安全说明：
    这只是扩展点，不是验证码破解程序。是否接入识别服务、是否遵守平台规则，
    由使用者自行决定并承担相应责任。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib import request as urlrequest
from urllib.error import URLError

from .browser.base import BrowserError, StopRequested
from .util import file_stamp, screenshot_dir


VALID_MODES = ("auto", "manual", "http", "command")

# 图片验证码输入框（登录页会出现）
IMAGE_INPUT_SELECTOR = (
    "#captcha, input[name='captcha'], input[id*='captcha'], "
    "input[placeholder*='验证码']"
)
IMAGE_ELEMENT_SELECTOR = (
    "#login-captcha, .login-captcha, img[id*='captcha'], img[class*='captcha'], "
    "img[src*='captcha']"
)
# 数美「空间逻辑验证」：图形区域与题目文字。id 带随机 uuid，只能按 class 取。
SHUMEI_IMAGE_SELECTOR = ".shumei_captcha_loaded_img_bg"
SHUMEI_TIPS_SELECTOR = ".shumei_captcha_slide_tips, .shumei_captcha_img_wrapper"
# 滑块把手候选（数美 / 通用滑块）
SLIDER_HANDLE_SELECTOR = (
    ".shumei_captcha_slide_btn, [class*='slide'] [class*='btn'], "
    "[class*='slider'] [class*='handle'], [class*='drag'] [class*='btn'], "
    "[class*='slider'] [class*='btn']"
)
SLIDER_TRACK_SELECTOR = (
    ".shumei_captcha_slide, [class*='slide-bar'], [class*='slider-track'], "
    "[class*='slide'] [class*='track']"
)


class CaptchaSolver:
    """把验证码图片交给外部服务，并按返回坐标点击。"""

    def __init__(self, settings: Dict[str, Any], log):
        self.settings = settings
        self.log = log
        cfg = settings.get("captcha") or {}
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self.mode = str(self.cfg.get("mode") or "manual").lower().strip()
        if self.mode not in VALID_MODES:
            self.mode = "manual"
        self.http_url = str(self.cfg.get("http_url") or "").strip()
        self.http_key = str(self.cfg.get("http_key") or "").strip()
        self.command = str(self.cfg.get("command") or "").strip()
        try:
            self.timeout = float(self.cfg.get("timeout_seconds") or 60)
        except (TypeError, ValueError):
            self.timeout = 60.0
        self.timeout = max(5.0, min(300.0, self.timeout))
        try:
            self.settle = float(self.cfg.get("settle_seconds") or 3)
        except (TypeError, ValueError):
            self.settle = 3.0
        self.settle = max(1.0, min(20.0, self.settle))
        self.fallback_manual = bool(self.cfg.get("fallback_manual", True))
        try:
            self.attempts = int(self.cfg.get("attempts") or 2)
        except (TypeError, ValueError):
            self.attempts = 2
        self.attempts = max(1, min(10, self.attempts))
        try:
            self.retry_interval = float(self.cfg.get("retry_interval_seconds") or 4)
        except (TypeError, ValueError):
            self.retry_interval = 4.0
        self.retry_interval = max(1.0, min(120.0, self.retry_interval))
        self.auto_cv = bool(self.cfg.get("auto_cv", True))
        self.click_mode = str(self.cfg.get("click_mode") or "system").lower().strip()
        if self.click_mode not in ("system", "browser", "auto"):
            self.click_mode = "system"
        self._sysmouse_noted = False
        self.auto_slider = bool(self.cfg.get("auto_slider", True))
        self.auto_ocr = bool(self.cfg.get("auto_ocr", True))
        vision = self.cfg.get("vision") if isinstance(self.cfg.get("vision"), dict) else {}
        # 留空则复用 content.llm 的 Base URL / Key / 模型。
        llm = (settings.get("content") or {}).get("llm") or {}
        self.vision_enabled = bool(vision.get("enabled", False))
        self.vision_base = str(vision.get("base_url") or llm.get("base_url") or "").strip()
        self.vision_key = str(vision.get("api_key") or llm.get("api_key") or "").strip()
        self.vision_model = str(vision.get("model") or llm.get("model") or "").strip()
        try:
            self.vision_timeout = float(vision.get("timeout_seconds") or 60)
        except (TypeError, ValueError):
            self.vision_timeout = 60.0
        self.vision_timeout = max(10.0, min(180.0, self.vision_timeout))
        self._ocr = None
        self._ocr_tried = False

    @property
    def enabled(self) -> bool:
        """是否值得调用 attempt()：auto 总有内置尝试，其余看是否配置了服务。"""
        if self.mode == "auto":
            return True
        if self.mode == "http":
            return bool(self.http_url)
        if self.mode == "command":
            return bool(self.command)
        return False

    @property
    def external_enabled(self) -> bool:
        return bool(self.http_url) if self.mode in ("auto", "http") else \
            bool(self.command) if self.mode in ("auto", "command") else False

    def _emit(self, level: str, message: str) -> None:
        try:
            self.log(level, message)
        except Exception:
            pass

    @staticmethod
    def _png_size(data: bytes) -> Tuple[int, int]:
        if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        return 0, 0

    @staticmethod
    def _viewport(engine) -> Tuple[int, int]:
        try:
            value = engine.eval("({w:window.innerWidth,h:window.innerHeight})")
            if isinstance(value, dict):
                return int(value.get("w") or 0), int(value.get("h") or 0)
        except Exception:
            pass
        return 0, 0

    def _wait_popup(self, engine, popup_selector: str, timeout: float = 6.0) -> bool:
        """110 响应可能先到，Shumei DOM 稍后才渲染，这里短暂等弹层出现。"""
        end = time.time() + max(0.0, timeout)
        while time.time() < end:
            engine.check_stop()
            try:
                if engine.exists(popup_selector, visible=True):
                    return True
            except Exception:
                pass
            time.sleep(0.25)
        return False

    def _build_payload(self, engine, popup_selector: str, attempt: int) -> Tuple[Dict[str, Any], Path]:
        stamp = file_stamp() + "-" + str(int(time.time() * 1000) % 100000)
        image_path = screenshot_dir() / ("captcha-" + stamp + ".png")
        engine.screenshot(str(image_path), full_page=False)
        data = image_path.read_bytes()
        image_w, image_h = self._png_size(data)
        viewport_w, viewport_h = self._viewport(engine)
        if viewport_w <= 0 or viewport_h <= 0:
            viewport_w, viewport_h = image_w, image_h
        instruction = ""
        try:
            instruction = str(engine.get_text(popup_selector) or "").strip()[:500]
        except Exception:
            pass
        payload = {
            "type": "yiban_captcha",
            "page_url": str(engine.current_url() or ""),
            "instruction": instruction,
            "image_base64": base64.b64encode(data).decode("ascii"),
            "image_width": int(image_w),
            "image_height": int(image_h),
            "viewport_width": int(viewport_w),
            "viewport_height": int(viewport_h),
            "attempt": int(attempt),
            "image_path": str(image_path),
        }
        self._emit("info", "验证码截图已保存：" + str(image_path))
        return payload, image_path

    def _call_http(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"}
        if self.http_key:
            headers["Authorization"] = "Bearer " + self.http_key
        request = urlrequest.Request(self.http_url, data=body, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except URLError as exc:
            raise BrowserError("外接验证 HTTP 请求失败：" + str(exc)) from exc
        return self._parse_response(raw)

    def _call_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        command = self.command
        if not command:
            raise BrowserError("未配置外接验证程序路径。")
        args = [command]
        if command.lower().endswith((".bat", ".cmd")):
            args = ["cmd.exe", "/c", '"' + command + '"']
        try:
            proc = subprocess.run(
                args,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            raise BrowserError("找不到外接验证程序：" + command) from exc
        except subprocess.TimeoutExpired as exc:
            raise BrowserError("外接验证程序超时（" + str(int(self.timeout)) + " 秒）。") from exc
        if proc.stderr:
            self._emit("warn", "外接验证程序 stderr：" + proc.stderr.strip()[:500])
        if proc.returncode != 0:
            raise BrowserError("外接验证程序退出码 " + str(proc.returncode) + "：" + (proc.stdout or "").strip()[:300])
        return self._parse_response(proc.stdout or "")

    @staticmethod
    def _parse_response(raw: str) -> Dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            raise BrowserError("外接验证服务没有返回内容。")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 兼容程序把 JSON 包在日志里的情况：截取第一个 { 到最后一个 }。
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError as exc:
                    raise BrowserError("外接验证服务返回的不是有效 JSON：" + text[:200]) from exc
            else:
                raise BrowserError("外接验证服务返回的不是有效 JSON：" + text[:200])
        if not isinstance(data, dict):
            raise BrowserError("外接验证服务返回的 JSON 必须是对象。")
        return data

    @staticmethod
    def _points(result: Dict[str, Any]) -> List[Tuple[float, float]]:
        points = result.get("points")
        if not isinstance(points, list):
            return []
        cleaned: List[Tuple[float, float]] = []
        for item in points[:12]:
            try:
                if isinstance(item, dict):
                    x, y = float(item.get("x")), float(item.get("y"))
                else:
                    x, y = float(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            if x >= 0 and y >= 0:
                cleaned.append((x, y))
        return cleaned

    def solve(self, engine, popup_selector: str, attempt: int = 1) -> bool:
        """尝试用外接服务完成一次点击型验证；成功返回 True。"""
        if not self.external_enabled:
            return False
        if not self._wait_popup(engine, popup_selector, timeout=6.0):
            self._emit("info", "外接验证：当前没有可见的验证码弹层，跳过。")
            return False
        self._emit("info", "外接验证：正在调用 " + ("HTTP 服务" if self.http_url else "外部程序") + "。")
        payload, image_path = self._build_payload(engine, popup_selector, attempt)
        try:
            if self.http_url:
                result = self._call_http(payload)
            else:
                result = self._call_command(payload)
        except StopRequested:
            raise
        except Exception as exc:
            self._emit("warn", "外接验证失败：" + str(exc))
            return False
        points = self._points(result)
        message = str(result.get("message") or "").strip()
        if message:
            self._emit("info", "外接验证返回：" + message[:300])
        if not points:
            self._emit("warn", "外接验证没有返回可点击坐标；截图：" + str(image_path))
            return False
        image_w = max(1, int(payload.get("image_width") or 1))
        image_h = max(1, int(payload.get("image_height") or 1))
        viewport_w = max(1, int(payload.get("viewport_width") or image_w))
        viewport_h = max(1, int(payload.get("viewport_height") or image_h))
        scale_x = viewport_w / image_w
        scale_y = viewport_h / image_h
        try:
            for x, y in points:
                self._click_point(engine, x * scale_x, y * scale_y)
                time.sleep(0.35)
        except StopRequested:
            raise
        except Exception as exc:
            self._emit("warn", "外接验证点击失败：" + str(exc))
            return False
        end = time.time() + self.settle
        while time.time() < end:
            engine.check_stop()
            try:
                if not engine.exists(popup_selector, visible=True):
                    self._emit("success", "外接验证：验证框已消失。")
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        self._emit("warn", "外接验证：点击后验证框仍未消失，可能需要重试或改为人工完成。")
        return False

    # ------------------------------------------------------------------
    # 自动模式（auto）：外接服务 → 滑块轨迹 → OCR → 重试
    # ------------------------------------------------------------------

    def _popup_gone(self, engine, popup_selector: str, wait: float = 2.0) -> bool:
        end = time.time() + max(0.0, wait)
        while True:
            engine.check_stop()
            try:
                if not engine.exists(popup_selector, visible=True):
                    return True
            except Exception:
                pass
            if time.time() >= end:
                return False
            time.sleep(0.3)

    @staticmethod
    def _rect_of(engine, selector: str) -> Dict[str, float]:
        expression = (
            "(function(sel){"
            "var els=document.querySelectorAll(sel);"
            "for(var i=0;i<els.length;i++){"
            "var el=els[i],r=el.getBoundingClientRect(),s=getComputedStyle(el);"
            "if(r.width>2&&r.height>2&&s.visibility!=='hidden'&&s.display!=='none'){"
            "return {x:r.left+window.scrollX,y:r.top+window.scrollY,w:r.width,h:r.height};}}"
            "return null;})(%s)" % json.dumps(selector)
        )
        try:
            value = engine.eval(expression)
            if isinstance(value, dict) and float(value.get("w") or 0) > 0:
                return {
                    "x": float(value.get("x") or 0),
                    "y": float(value.get("y") or 0),
                    "w": float(value.get("w") or 0),
                    "h": float(value.get("h") or 0),
                }
        except Exception:
            pass
        return {}

    def _load_ocr(self):
        if self._ocr_tried:
            return self._ocr
        self._ocr_tried = True
        try:
            import ddddocr  # type: ignore

            try:
                self._ocr = ddddocr.DdddOcr(show_ad=False)
            except TypeError:
                self._ocr = ddddocr.DdddOcr()
            self._emit("info", "已启用内置图片验证码 OCR（ddddocr）。")
        except Exception:
            self._ocr = None
            self._emit("info", "未安装 ddddocr，跳过内置 OCR；需要时执行 pip install ddddocr 后重新打包。")
        return self._ocr

    def solve_image_captcha(self, engine) -> bool:
        """识别并填写登录页图片验证码；成功返回 True（调用方需再点一次登录）。"""
        if not self.auto_ocr or not self.enabled:
            return False
        try:
            if not engine.exists(IMAGE_ELEMENT_SELECTOR, visible=True):
                return False
        except Exception:
            return False
        ocr = self._load_ocr()
        if ocr is None:
            return False
        path = screenshot_dir() / ("captcha-image-" + file_stamp() + ".png")
        try:
            ok = engine.element_screenshot(IMAGE_ELEMENT_SELECTOR, str(path))
            if not ok:
                return False
            code = str(ocr.classification(path.read_bytes()) or "").strip()
        except StopRequested:
            raise
        except Exception as exc:
            self._emit("warn", "图片验证码识别失败：" + str(exc))
            return False
        code = "".join(ch for ch in code if ch.isalnum())
        if not code:
            self._emit("warn", "图片验证码识别结果为空；截图：" + str(path))
            return False
        self._emit("info", "图片验证码识别结果：" + code)
        try:
            engine.fill(IMAGE_INPUT_SELECTOR, code, timeout=8)
        except Exception as exc:
            self._emit("warn", "填写图片验证码失败：" + str(exc))
            return False
        return True

    def _try_slider(self, engine, popup_selector: str) -> bool:
        drag = getattr(engine, "drag", None)
        if drag is None:
            return False
        handle = self._rect_of(engine, SLIDER_HANDLE_SELECTOR)
        if not handle:
            self._emit("info", "自动验证：未找到滑块把手，跳过轨迹模拟。")
            return False
        track = self._rect_of(engine, SLIDER_TRACK_SELECTOR) or handle
        start_x = handle["x"] + handle["w"] / 2.0
        start_y = handle["y"] + handle["h"] / 2.0
        distance = max(40.0, float(track.get("w") or 300.0) - handle["w"])
        end_x = start_x + distance
        self._emit("info", "自动验证：模拟拖动滑块约 " + str(int(distance)) + " 像素。")
        try:
            drag(start_x, start_y, end_x, start_y, duration=1.1)
        except StopRequested:
            raise
        except Exception as exc:
            self._emit("warn", "滑块拖动失败：" + str(exc))
            return False
        return self._popup_gone(engine, popup_selector, 3.0)

    # ------------------------------------------------------------------
    # 图形点选（数美 spatial_select）：视觉模型 → OCR 点选
    # ------------------------------------------------------------------

    @staticmethod
    def _rect_client(engine, selector: str) -> Dict[str, float]:
        expression = (
            "(function(sel){var els=document.querySelectorAll(sel);"
            "for(var i=0;i<els.length;i++){var e=els[i],r=e.getBoundingClientRect(),s=getComputedStyle(e);"
            "if(r.width>4&&r.height>4&&s.visibility!=='hidden'&&s.display!=='none'){"
            "return {x:r.left,y:r.top,w:r.width,h:r.height,src:e.src||''};}}return null;})(%s)"
            % json.dumps(selector)
        )
        try:
            value = engine.eval(expression)
            if isinstance(value, dict) and float(value.get("w") or 0) > 0:
                return {
                    "x": float(value.get("x") or 0),
                    "y": float(value.get("y") or 0),
                    "w": float(value.get("w") or 0),
                    "h": float(value.get("h") or 0),
                    "src": str(value.get("src") or ""),
                }
        except Exception:
            pass
        return {}

    @staticmethod
    def _image_size(data: bytes) -> Tuple[int, int]:
        if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data[:2] == b"\xff\xd8":
            index = 2
            length = len(data)
            while index + 9 < length:
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height = int.from_bytes(data[index + 5:index + 7], "big")
                    width = int.from_bytes(data[index + 7:index + 9], "big")
                    return width, height
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                segment = int.from_bytes(data[index + 2:index + 4], "big")
                index += 2 + max(2, segment)
        return 0, 0

    def _capture_captcha_image(self, engine, popup_selector: str) -> Tuple[bytes, Dict[str, float]]:
        """优先直接下载验证码原图（600x300，比截屏清晰），失败再截元素。"""
        rect = self._rect_client(engine, SHUMEI_IMAGE_SELECTOR)
        if not rect:
            rect = self._rect_client(engine, popup_selector)
        data = b""
        src = str(rect.get("src") or "")
        if src.startswith("http"):
            try:
                request = urlrequest.Request(
                    src,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Referer": str(engine.current_url() or "https://www.yiban.cn/"),
                    },
                )
                with urlrequest.urlopen(request, timeout=15) as response:
                    data = response.read()
            except Exception as exc:
                self._emit("warn", "验证码原图下载失败，改用截屏：" + str(exc))
        if not data:
            path = screenshot_dir() / ("captcha-image-" + file_stamp() + ".png")
            try:
                if engine.element_screenshot(SHUMEI_IMAGE_SELECTOR, str(path)):
                    data = path.read_bytes()
            except Exception:
                data = b""
        return data, rect

    @staticmethod
    def _vision_endpoint(base_url: str) -> str:
        base = (base_url or "").strip().rstrip("/")
        if not base:
            raise BrowserError("视觉模型 Base URL 为空。")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    def _ask_vision(self, image: bytes, instruction: str, width: int, height: int) -> List[Tuple[float, float]]:
        if not self.vision_key or not self.vision_model:
            if not getattr(self, "_vision_warned", False):
                self._vision_warned = True
                self._emit(
                    "warn",
                    "图形点选需要视觉模型：请在“验证码 → 图形点选”里填写 Base URL / Key / 模型，"
                    "或先在“2. 内容生成”里配置一个支持图片的模型。",
                )
            return []
        mime = "image/png" if image[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        data_uri = "data:" + mime + ";base64," + base64.b64encode(image).decode("ascii")
        prompt = (
            "这是数美「空间逻辑验证」的图形点选题。图中是一组 3D 几何体，颜色和形状各不相同。\n"
            "题目要求（从页面读到的文字，可能为空）：" + (instruction or "（空）") + "\n\n"
            "请完成两件事：\n"
            "1. 如果题目要求为空，先观察图片，判断题目要求点击哪些图形（通常会写“请依次点击……”）；\n"
            "2. 按题目要求的顺序，找出每个图形的中心点。\n"
            "坐标必须是相对图片的归一化值：x 从左到右 0-1，y 从上到下 0-1，保留 3 位小数。\n"
            "只输出 JSON，不要 Markdown，不要解释，格式：\n"
            '{"instruction":"你读到的题目","targets":["蓝色圆锥体"],"points":[[0.23,0.61]]}\n'
            "图片尺寸（像素）：" + str(width) + "x" + str(height) + "。"
        )
        payload = {
            "model": self.vision_model,
            "messages": [
                {"role": "system", "content": "你是图形验证码识别助手，只输出 JSON。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            "temperature": 0,
            "max_tokens": 500,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(
            self._vision_endpoint(self.vision_base),
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.vision_key,
            },
        )
        self._emit("info", "视觉模型识别中（" + self.vision_model + "）...")
        try:
            with urlrequest.urlopen(request, timeout=self.vision_timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except URLError as exc:
            self._emit("warn", "视觉模型请求失败：" + str(exc))
            return []
        try:
            outer = json.loads(raw)
            content = outer["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = " ".join(
                    str(item.get("text") or "") for item in content if isinstance(item, dict)
                )
        except Exception:
            self._emit("warn", "视觉模型返回无法解析：" + raw[:200])
            return []
        text = str(content or "").strip()
        fence = chr(96) * 3
        if fence in text:
            text = text.split(fence)[1] if text.count(fence) >= 2 else text
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            self._emit("warn", "视觉模型没有返回 JSON：" + text[:200])
            return []
        try:
            result = json.loads(text[start:end + 1])
        except Exception:
            self._emit("warn", "视觉模型 JSON 解析失败：" + text[:200])
            return []
        if not isinstance(result, dict):
            return []
        targets = result.get("targets")
        if isinstance(targets, list) and targets:
            self._emit("info", "视觉模型识别目标：" + "、".join(str(t) for t in targets[:6]))
        points_raw = result.get("points")
        if not isinstance(points_raw, list):
            return []
        cleaned: List[Tuple[float, float]] = []
        for item in points_raw[:6]:
            try:
                if isinstance(item, dict):
                    x, y = float(item.get("x")), float(item.get("y"))
                else:
                    x, y = float(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            # 模型偶尔直接返回像素坐标：大于 1.2 时按图片尺寸归一化。
            if abs(x) > 1.2 or abs(y) > 1.2:
                if width > 0 and height > 0:
                    x, y = x / width, y / height
                else:
                    continue
            if -0.02 <= x <= 1.02 and -0.02 <= y <= 1.02:
                cleaned.append((min(1.0, max(0.0, x)), min(1.0, max(0.0, y))))
        return cleaned

    # ------------------------------------------------------------------
    # 验证码面板状态
    # ------------------------------------------------------------------

    _CAPTCHA_ERROR_SEL = ".shumei_captcha_img_load_error_wrapper, .shumei_captcha_network_fail_wrapper"
    _CAPTCHA_LOADING_SEL = ".shumei_captcha_img_loadding_wrapper"
    _CAPTCHA_NOISE = ("网络不佳", "请刷新重试", "验证成功", "加载中", "加载失败", "请完成验证")

    def _captcha_image_ready(self, engine) -> bool:
        try:
            if engine.exists(self._CAPTCHA_ERROR_SEL, visible=True):
                return False
            if engine.exists(self._CAPTCHA_LOADING_SEL, visible=True):
                return False
            return bool(engine.exists(SHUMEI_IMAGE_SELECTOR, visible=True))
        except Exception:
            return False

    def _refresh_captcha(self, engine) -> bool:
        for selector in (
            ".shumei_captcha_fail_refresh_btn",
            ".shumei_captcha_img_refresh_btn",
            ".shumei_captcha_footer_refresh_btn",
        ):
            try:
                if engine.exists(selector, visible=True):
                    engine.click(selector, timeout=5)
                    return True
            except Exception:
                continue
        return False

    def _captcha_instruction(self, engine, popup_selector: str) -> str:
        """读题目文字，过滤掉“网络不佳/验证成功”这类状态提示。"""
        texts = []
        for selector in (SHUMEI_TIPS_SELECTOR, popup_selector):
            try:
                text = str(engine.get_text(selector) or "").strip()
            except Exception:
                text = ""
            if text:
                texts.append(re.sub(r"\s+", " ", text))
        for text in texts:
            if "点击" in text and "网络不佳" not in text and "刷新重试" not in text:
                return text[:200]
        for text in texts:
            if not any(word in text for word in self._CAPTCHA_NOISE):
                return text[:200]
        return ""

    def _wait_captcha_image_loaded(self, engine, timeout: float = 4.0) -> bool:
        """等 <img> 真正解码完成（complete + naturalWidth>0），避免截到灰底占位。"""
        expression = (
            "(function(){var e=document.querySelector('.shumei_captcha_loaded_img_bg');"
            "return !!(e && e.complete && e.naturalWidth > 0);})()"
        )
        end = time.time() + max(0.5, timeout)
        while time.time() < end:
            try:
                if engine.eval(expression):
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def _ensure_captcha_image(self, engine) -> bool:
        if self._captcha_image_ready(engine) and self._wait_captcha_image_loaded(engine, 2.0):
            return True
        refreshed = self._refresh_captcha(engine)
        if refreshed:
            self._emit("info", "验证码图片没加载出来，已点刷新，等待重新加载。")
            time.sleep(1.2)
        if self._captcha_image_ready(engine):
            self._wait_captcha_image_loaded(engine, 4.0)
            return True
        self._emit("warn", "验证码图片不可用（网络不佳或加载失败），本轮跳过自动识别。")
        return False

    def _capture_element_png(self, engine, selector: str, scale: float = 2.0) -> Tuple[bytes, Dict[str, float]]:
        """按元素截图并放大 2 倍，给离线识别用（PNG 便于纯 Python 解码）。"""
        rect = self._rect_client(engine, selector)
        if not rect:
            return b"", {}
        path = screenshot_dir() / ("captcha-cv-" + file_stamp() + ".png")
        try:
            if engine.element_screenshot(selector, str(path), scale):
                return path.read_bytes(), rect
        except Exception:
            pass
        return b"", rect

    _CLICK_PROBE_JS = (
        "(function(){if(window.__ybClickProbe){return window.__ybClicks||0;}"
        "window.__ybClicks=0;window.__ybClickProbe=1;"
        "document.addEventListener('mousedown',function(){window.__ybClicks++;},true);"
        "return 0;})()"
    )

    def _click_count(self, engine) -> int:
        try:
            return int(engine.eval(self._CLICK_PROBE_JS) or 0)
        except Exception:
            return -1

    def _click_point(self, engine, x: float, y: float) -> None:
        """点一下指定坐标，并**确认页面真的收到了这次点击**。

        只判断“系统鼠标是否点出去了”不够：窗口找错、被弹窗盖住、缩放不对时
        点击会落到别的地方。这里在页面里埋一个 mousedown 计数器，点完核对计数，
        没涨就立刻改用浏览器事件点击（CDP），保证这一点一定落到页面上。
        """
        if self.click_mode == "browser":
            engine.click_at(x, y)
            return
        before = self._click_count(engine)
        ok = False
        try:
            ok = bool(engine.click_at_system(x, y))
        except Exception:
            ok = False
        if ok:
            if not self._sysmouse_noted:
                self._sysmouse_noted = True
                self._emit("info", "已切换为系统级真实鼠标点击（请保持浏览器在前台）。")
            if before < 0:
                return
            for _ in range(6):  # 最多等 0.6 秒
                time.sleep(0.1)
                if self._click_count(engine) > before:
                    return
            self._emit("warn", "系统鼠标已点击，但页面没收到（窗口/缩放可能对不上），改用浏览器事件点击。")
        elif self.click_mode == "system":
            self._emit("warn", "系统鼠标点击不可用（浏览器不在前台或窗口未找到），本次退回浏览器事件点击。")
        engine.click_at(x, y)
        if before >= 0:
            time.sleep(0.15)
            if self._click_count(engine) <= before:
                self._emit("warn", "浏览器事件点击后页面仍未收到点击，继续尝试其它方式。")

    def _capture_captcha_rgb(self, engine):
        """整屏截图后按元素矩形裁剪，返回 (w, h, rgb, path)。

        不用 element_screenshot 的 clip：中文页面经常是滚动状态，clip 用的是文档坐标，
        传入视口坐标会截到空白区域（实测会得到纯色图，识别 0 个图形）。
        """
        rect = self._rect_client(engine, SHUMEI_IMAGE_SELECTOR)
        if not rect:
            return None
        view_w, view_h = self._viewport(engine)
        path = screenshot_dir() / ("captcha-cv-" + file_stamp() + ".png")
        try:
            engine.screenshot(str(path))
            from .captcha_cv import decode_png

            width, height, rgb = decode_png(path.read_bytes())
        except Exception as exc:
            self._emit("warn", "验证码截图失败：" + str(exc))
            return None
        scale_x = (width / float(view_w)) if view_w else 1.0
        scale_y = (height / float(view_h)) if view_h else 1.0
        x0 = max(0, int(rect["x"] * scale_x))
        y0 = max(0, int(rect["y"] * scale_y))
        x1 = min(width, int((rect["x"] + rect["w"]) * scale_x))
        y1 = min(height, int((rect["y"] + rect["h"]) * scale_y))
        if x1 - x0 < 20 or y1 - y0 < 20:
            self._emit("warn", "验证码区域超出截图范围（" + str(width) + "x" + str(height) + "）")
            return None
        crop_w = x1 - x0
        crop_h = y1 - y0
        cropped = bytearray(crop_w * crop_h * 3)
        for row in range(crop_h):
            src = ((y0 + row) * width + x0) * 3
            dst = row * crop_w * 3
            cropped[dst:dst + crop_w * 3] = rgb[src:src + crop_w * 3]
        return crop_w, crop_h, cropped, str(path), rect

    @staticmethod
    def _save_marked(width: int, height: int, rgb: bytes, points, shot_path: str) -> str:
        """把命中点画在截图上另存一份，失败时可直接看出点到了哪里。"""
        try:
            from .captcha_cv import encode_png

            canvas = bytearray(rgb)
            for nx, ny in points:
                cx = int(nx * width)
                cy = int(ny * height)
                for dx in range(-7, 8):
                    for dy in range(-7, 8):
                        if abs(dx) < 5 and abs(dy) < 5:
                            continue
                        px, py = cx + dx, cy + dy
                        if 0 <= px < width and 0 <= py < height:
                            base = (py * width + px) * 3
                            canvas[base:base + 3] = b"\xff\x00\x00"
            target = str(shot_path).replace(".png", "-marked.png")
            with open(target, "wb") as handle:
                handle.write(encode_png(width, height, canvas))
            return target
        except Exception as exc:
            return "标注失败：" + str(exc)

    def _try_cv_click(self, engine, popup_selector: str) -> bool:
        """离线识别：颜色 + 轮廓判断图形，不需要账号和网络。"""
        if not self._ensure_captcha_image(engine):
            return False
        instruction = self._captcha_instruction(engine, popup_selector)
        if not instruction:
            self._emit("info", "离线识别：页面没有可用的题目文字，跳过。")
            return False
        try:
            from .captcha_cv import COLOR_NAMES, SHAPE_NAMES, analyze, match_points, parse_instruction
        except Exception as exc:
            self._emit("info", "离线识别模块不可用：" + str(exc))
            return False

        time.sleep(0.25)  # 等弹层动画停下来，避免坐标漂移
        blobs = []
        crop_w = crop_h = 0
        shot_path = ""
        rect = {}
        for attempt in range(2):
            captured = self._capture_captcha_rgb(engine)
            if not captured:
                return False
            crop_w, crop_h, crop_rgb, shot_path, rect = captured
            try:
                blobs = analyze(crop_w, crop_h, crop_rgb)
            except Exception as exc:
                self._emit("info", "离线识别跳过：" + str(exc))
                return False
            if len(blobs) >= 2:
                break
            if attempt == 0:
                # 图片可能还没绘制完，等一拍重截一次再判断。
                self._emit("info", "离线识图：截图里图形太少，0.6 秒后重截一次。")
                time.sleep(0.6)
        try:
            targets = parse_instruction(instruction)
            points = match_points(blobs, targets, crop_w, crop_h) if targets else []
        except Exception as exc:
            self._emit("info", "离线识别跳过：" + str(exc))
            return False
        summary = "、".join(
            COLOR_NAMES.get(blob.color, blob.color) + SHAPE_NAMES.get(blob.shape, blob.shape)
            + "(" + str(int(blob.cx)) + "," + str(int(blob.cy)) + " " + str(blob.width) + "x" + str(blob.height) + ")"
            for blob in blobs[:6]
        )
        self._emit(
            "info",
            "离线识图：找到 " + str(len(blobs)) + " 个图形（" + summary + "）"
            + "，区域 " + str(crop_w) + "x" + str(crop_h) + "，截图 " + shot_path,
        )
        if not points:
            self._emit("warn", "离线识图：题目“" + instruction + "”没有对上图形，换下一种策略。")
            return False
        marked_path = self._save_marked(crop_w, crop_h, crop_rgb, points, shot_path)
        self._emit(
            "info",
            "离线识图命中 " + str(len(points)) + " 个目标，开始点击（标注图 " + marked_path + "）。",
        )
        try:
            for nx, ny in points:
                x = rect.get("x", 0) + nx * rect.get("w", 0)
                y = rect.get("y", 0) + ny * rect.get("h", 0)
                self._emit("info", "点击坐标 (" + str(round(x, 1)) + ", " + str(round(y, 1)) + ")")
                self._click_point(engine, x, y)
                time.sleep(0.4)
        except StopRequested:
            raise
        except Exception as exc:
            self._emit("warn", "按离线坐标点击失败：" + str(exc))
            return False
        ok = self._popup_gone(engine, popup_selector, self.settle + 2.0)
        if ok:
            self._emit("success", "离线识图：验证通过。")
        else:
            self._emit("warn", "离线识图：点击后验证框仍在，换图重试。")
        return ok

    def _try_vision_click(self, engine, popup_selector: str) -> bool:
        if not self.vision_enabled:
            return False
        rect = self._rect_client(engine, SHUMEI_IMAGE_SELECTOR)
        if not rect:
            return False
        if not self._ensure_captcha_image(engine):
            return False
        # 题目文字通常在提示条里；读不到就整块截屏，让模型自己从图里读。
        instruction = self._captcha_instruction(engine, popup_selector)
        image, rect = self._capture_captcha_image(engine, popup_selector)
        if not instruction:
            panel_rect = self._rect_client(engine, popup_selector)
            path = screenshot_dir() / ("captcha-panel-" + file_stamp() + ".png")
            try:
                if panel_rect and engine.element_screenshot(popup_selector, str(path)):
                    image, rect = path.read_bytes(), panel_rect
            except Exception:
                pass
        if not image:
            self._emit("warn", "没有拿到验证码图片，视觉识别跳过。")
            return False
        width, height = self._image_size(image)
        if instruction:
            self._emit("info", "页面题目文字：" + instruction)
        points = self._ask_vision(image, instruction, width or int(rect["w"]), height or int(rect["h"]))
        if not points:
            self._emit("warn", "视觉模型没有给出可点击坐标。")
            return False
        self._emit("info", "视觉模型给出 " + str(len(points)) + " 个点击点，开始点击。")
        try:
            for nx, ny in points:
                self._click_point(engine, rect["x"] + nx * rect["w"], rect["y"] + ny * rect["h"])
                time.sleep(0.45)
        except StopRequested:
            raise
        except Exception as exc:
            self._emit("warn", "按视觉坐标点击失败：" + str(exc))
            return False
        return self._popup_gone(engine, popup_selector, self.settle + 2.0)

    def _try_ocr_click(self, engine, popup_selector: str) -> bool:
        """文字点选兜底：ddddocr 检测 + 分类，按题目里的字序点击。"""
        if not self.auto_ocr or not self.enabled:
            return False
        try:
            import ddddocr  # type: ignore
        except Exception:
            return False
        image, rect = self._capture_captcha_image(engine, popup_selector)
        if not image or not rect:
            return False
        try:
            detector = ddddocr.DdddOcr(det=True, show_ad=False)
            boxes = detector.detection(image) or []
        except Exception as exc:
            self._emit("info", "OCR 检测不可用：" + str(exc))
            return False
        if not boxes:
            return False
        instruction = ""
        try:
            instruction = str(engine.get_text(SHUMEI_TIPS_SELECTOR) or "").strip()
        except Exception:
            pass
        if not instruction:
            self._emit("info", "OCR 点选：页面没有题目文字，跳过。")
            return False
        wanted = [ch for ch in instruction if ch.strip() and not ch.isascii()]
        if not wanted:
            return False
        try:
            ocr = self._load_ocr()
            if ocr is None:
                return False
            import io

            from PIL import Image  # type: ignore
        except Exception:
            return False
        try:
            pil = Image.open(io.BytesIO(image)).convert("RGB")
        except Exception:
            return False
        hits: List[Tuple[int, float, float]] = []
        used = set()
        for box in boxes[:12]:
            try:
                x1, y1, x2, y2 = [int(v) for v in box]
                crop = pil.crop((x1, y1, x2, y2))
                buffer = io.BytesIO()
                crop.save(buffer, format="PNG")
                text = str(ocr.classification(buffer.getvalue()) or "")
            except Exception:
                continue
            for ch in text:
                if ch in wanted and ch not in used:
                    used.add(ch)
                    hits.append((wanted.index(ch), (x1 + x2) / 2.0, (y1 + y2) / 2.0))
                    break
        hits.sort(key=lambda item: item[0])
        if not hits:
            self._emit("info", "OCR 点选：没有匹配到题目中的字。")
            return False
        scale_x = rect["w"] / max(1, pil.width)
        scale_y = rect["h"] / max(1, pil.height)
        self._emit("info", "OCR 点选命中 " + str(len(hits)) + " 个目标。")
        for _, cx, cy in hits:
            engine.click_at(rect["x"] + cx * scale_x, rect["y"] + cy * scale_y)
            time.sleep(0.4)
        return self._popup_gone(engine, popup_selector, self.settle + 2.0)

    def attempt(self, engine, popup_selector: str, attempts: int | None = None) -> bool:
        """自动尝试处理当前验证；成功（验证框消失）返回 True。"""
        total = max(1, int(attempts or self.attempts))
        for index in range(1, total + 1):
            engine.check_stop()
            if not self._wait_popup(engine, popup_selector, timeout=5.0):
                self._emit("info", "自动验证：当前没有可见的验证弹层。")
                return False
            self._emit("info", "自动验证：第 " + str(index) + "/" + str(total) + " 次尝试。")
            solved = False
            if self.external_enabled:
                solved = self.solve(engine, popup_selector, index)
            if not solved and self.mode == "auto" and self.auto_cv:
                solved = self._try_cv_click(engine, popup_selector)
            if not solved and self.mode == "auto" and self.vision_enabled:
                solved = self._try_vision_click(engine, popup_selector)
            if not solved and self.mode == "auto" and self.auto_ocr:
                solved = self._try_ocr_click(engine, popup_selector)
            if not solved and self.mode == "auto" and self.auto_slider:
                solved = self._try_slider(engine, popup_selector)
            if solved or self._popup_gone(engine, popup_selector, self.settle):
                self._emit("success", "自动验证通过。")
                return True
            if index < total:
                self._emit("info", "自动验证未通过，" + str(int(self.retry_interval)) + " 秒后重试。")
                time.sleep(self.retry_interval)
        self._emit("warn", "自动验证未通过，已尝试 " + str(total) + " 次。")
        return False
