# -*- coding: utf-8 -*-
"""可选的账号密码本地保存（Windows DPAPI 加密）。

默认不保存密码；只有用户在界面上勾选“记住账号密码”时才写入本文件。
加密使用 Windows 自带 DPAPI（CryptProtectData），密钥绑定当前 Windows 用户，
换用户或换机器后无法解密 —— 文件被拷走也读不出明文。

文件位置：%LOCALAPPDATA%\\YibanAutoPoster\\credentials.dat
"""
from __future__ import annotations

import base64
import ctypes
import json
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Optional, Tuple

from .util import data_dir, ensure_dir, read_json, write_json_atomic


CREDENTIALS_NAME = "credentials.dat"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_MAGIC = "YIBAN-DPAPI-1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class SecureStoreError(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == "win32"


def credentials_path() -> Path:
    return ensure_dir(data_dir()) / CREDENTIALS_NAME


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _crypt32():
    if not available():
        raise SecureStoreError("当前系统不是 Windows，无法使用 DPAPI 加密保存。")
    dll = ctypes.WinDLL("crypt32", use_last_error=True)
    dll.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    dll.CryptProtectData.restype = wintypes.BOOL
    dll.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    dll.CryptUnprotectData.restype = wintypes.BOOL
    return dll


def protect(text: str, entropy: bytes = b"yiban-autoposter") -> bytes:
    data = text.encode("utf-8")
    dll = _crypt32()
    in_blob, in_buf = _blob(data)
    ent_blob, ent_buf = _blob(entropy)
    out_blob = _Blob()
    ok = dll.CryptProtectData(
        ctypes.byref(in_blob),
        "YibanAutoPoster",
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecureStoreError("DPAPI 加密失败（错误码 " + str(ctypes.get_last_error()) + "）")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def unprotect(blob: bytes, entropy: bytes = b"yiban-autoposter") -> str:
    dll = _crypt32()
    in_blob, in_buf = _blob(blob)
    ent_blob, ent_buf = _blob(entropy)
    out_blob = _Blob()
    ok = dll.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise SecureStoreError("DPAPI 解密失败（错误码 " + str(ctypes.get_last_error()) + "）")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8", errors="replace")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def save_credentials(account: str, password: str, path: Optional[Path] = None) -> Path:
    account = (account or "").strip()
    if not account or not password:
        raise SecureStoreError("账号或密码为空，未保存。")
    target = Path(path) if path else credentials_path()
    payload = {
        "magic": _MAGIC,
        "account": account,
        "secret": base64.b64encode(protect(password)).decode("ascii"),
        "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(target, payload)
    return target


def load_credentials(path: Optional[Path] = None) -> Tuple[str, str]:
    target = Path(path) if path else credentials_path()
    data = read_json(target)
    if not isinstance(data, dict) or data.get("magic") != _MAGIC:
        raise SecureStoreError("凭据文件格式不正确。")
    account = str(data.get("account") or "").strip()
    secret = str(data.get("secret") or "")
    if not account or not secret:
        raise SecureStoreError("凭据文件内容不完整。")
    password = unprotect(base64.b64decode(secret))
    return account, password


def has_credentials(path: Optional[Path] = None) -> bool:
    try:
        return (Path(path) if path else credentials_path()).exists()
    except Exception:
        return False


def clear_credentials(path: Optional[Path] = None) -> bool:
    target = Path(path) if path else credentials_path()
    try:
        if target.exists():
            target.unlink()
            return True
    except OSError:
        return False
    return False


# 供自检/调试使用：把两个函数做了失效保护的包装。
def safe_load(path: Optional[Path] = None) -> Optional[Tuple[str, str]]:
    try:
        return load_credentials(path)
    except Exception:
        return None


_ = json  # 保持导入给未来扩展使用（文件格式里可能加入更多字段）
