# -*- coding: utf-8 -*-
"""外接验证码服务模板（半成品扩展点）。

这不是识别程序，也不包含任何打码/OCR/滑块算法。它只演示主程序与外部
服务之间的 JSON 协议，方便你自己把识别逻辑接进来。

使用方式：
1. 复制本文件，在 solve() 里实现你自己的识别逻辑；
2. 用 pyinstaller 打包成 exe，或写一个 .bat 调用它；
3. 在工具的“验证码”设置里选择“外接程序”，指向 exe/bat；
4. 外接程序必须从 stdin 读取 JSON，向 stdout 输出 JSON。

输入示例见 yiban_auto/captcha.py 顶部说明。
输出示例：
    {"points": [[120, 80]], "message": "点击坐标"}

注意：points 使用截图像素坐标；多坐标会按顺序点击。
"""
from __future__ import annotations

import json
import sys


def solve(payload: dict) -> dict:
    """在这里接入你自己的识别逻辑。

    可用字段：
        payload["image_base64"]    PNG 图片的 base64
        payload["image_width"]     图片宽
        payload["image_height"]    图片高
        payload["viewport_width"]  浏览器视口宽
        payload["viewport_height"] 浏览器视口高
        payload["instruction"]     页面上的验证提示文字（可能为空）
        payload["page_url"]        当前页面地址

    返回 {"points": [[x, y], ...]}；识别失败返回 {"points": []}。
    """
    _ = payload  # 占位，避免未使用变量告警
    return {"points": [], "message": "模板尚未实现识别逻辑"}


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("输入必须是 JSON 对象")
        result = solve(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        sys.stdout.flush()
        return 0
    except Exception as exc:
        sys.stderr.write("captcha_solver_example failed: " + str(exc) + "\n")
        sys.stdout.write(json.dumps({"points": [], "message": str(exc)}, ensure_ascii=False))
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
