# -*- coding: utf-8 -*-
"""通用工具函数。"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path


APP_NAME = "YibanAutoPoster"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """程序所在目录：打包后是 exe 所在目录，源码运行时是项目根目录。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    """读取随程序打包的资源（PyInstaller 解包目录优先）。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base) / relative
        if candidate.exists():
            return candidate
    return app_dir() / relative


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / ("." + APP_NAME.lower())


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    p = default_data_dir()
    try:
        return ensure_dir(p)
    except OSError:
        return ensure_dir(app_dir() / "data")


def log_dir() -> Path:
    return ensure_dir(data_dir() / "logs")


def screenshot_dir() -> Path:
    return ensure_dir(data_dir() / "screenshots")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def file_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_filename(text: str, max_len: int = 60) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(text)).strip(" ._")
    return (text or "file")[:max_len]


def read_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json_atomic(path: Path, data) -> None:
    ensure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def read_text_file(path) -> str:
    """读文本，自动兼容 UTF-8 / UTF-8 BOM / GBK（Word、记事本常见的几种编码）。"""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def write_text_file(path, text: str) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8", newline="\n")


def mask_secret(value: str, keep: int = 4) -> str:
    value = value or ""
    if not value:
        return "（未设置）"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep * 2) + value[-keep:]


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def human_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    m, s = divmod(seconds, 60)
    return f"{m} 分 {s} 秒"


def unique_suffix(length: int = 4) -> str:
    import random
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(random.choice(alphabet) for _ in range(length))
