# -*- coding: utf-8 -*-
"""发帖结果的旁路复核：用浏览器当前 Cookie 直接请求 HTTP 页面。

主流程始终走浏览器，这里只做“第二只眼睛”：
    · 浏览器显示成功时，再用同一份 Cookie 拉一次“我的帖子”页面，
      如果能在页面里找到这篇帖子的标题，作为额外的成功证据；
    · 找不到也不能判定失败 —— 该页面可能是前端渲染，首屏 HTML 里没有正文。
"""
from __future__ import annotations

import gzip
import re
import urllib.error
import urllib.request
from typing import Any, Dict
from urllib.parse import urlparse


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
)


def _decode(raw: bytes, encoding: str) -> str:
    try:
        if "gzip" in (encoding or "").lower():
            raw = gzip.decompress(raw)
    except Exception:
        pass
    for charset in ("utf-8", "gb18030"):
        try:
            return raw.decode(charset)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch(url: str, cookies: Dict[str, str], timeout: float = 12.0) -> Dict[str, Any]:
    if not cookies:
        return {"ok": False, "status": 0, "text": "", "note": "没有可复用的 Cookie"}
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return {"ok": False, "status": 0, "text": "", "note": "复核地址不合法"}
    header = "; ".join(name + "=" + value for name, value in cookies.items())
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cookie": header,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            text = _decode(raw, response.headers.get("Content-Encoding", ""))
            return {"ok": True, "status": int(response.status), "text": text, "note": ""}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "text": "", "note": "HTTP " + str(exc.code)}
    except Exception as exc:
        return {"ok": False, "status": 0, "text": "", "note": str(exc)}


def verify_published_post(engine, url: str, title: str, timeout: float = 12.0) -> Dict[str, Any]:
    """返回 {checked, found, note}；checked=False 表示没有做（缺 Cookie/请求失败）。"""
    title = (title or "").strip()
    if not title:
        return {"checked": False, "found": None, "note": "标题为空，跳过 HTTP 复核"}
    try:
        cookies = engine.get_cookies()
    except Exception as exc:
        return {"checked": False, "found": None, "note": "取 Cookie 失败：" + str(exc)}
    if not cookies:
        return {"checked": False, "found": None, "note": "浏览器没有可复用的 Cookie"}
    result = fetch(url, cookies, timeout=timeout)
    if not result.get("ok"):
        return {"checked": False, "found": None, "note": "HTTP 复核未完成（" + str(result.get("note") or "请求失败") + "）"}
    text = str(result.get("text") or "")
    # 标题可能被 HTML 转义或包在标签里，宽松匹配一次。
    compact = re.sub(r"\s+", "", text)
    found = title in text or re.sub(r"\s+", "", title) in compact
    status = result.get("status")
    if found:
        note = "已用 Cookie 请求 " + url + "（HTTP " + str(status) + "），页面中找到该标题"
    else:
        note = "已用 Cookie 请求 " + url + "（HTTP " + str(status) + "），首屏 HTML 未找到标题（前端渲染，不作为失败依据）"
    return {"checked": True, "found": found, "note": note}
