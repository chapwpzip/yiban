# -*- coding: utf-8 -*-
"""把本地工作区同步到 GitHub（chapwpzip/yiban），并更新仓库描述、topics、发布 Release。

用法：
    python tools/push_v130.py --token ghp_xxx [--dry-run]

只上传清单内的文件；用 git blob sha 比对，未变化的文件不动。
所有请求体按 UTF-8 编码，避免中文文件名 / 提交信息乱码。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

OWNER = "chapwpzip"
REPO = "yiban"
BRANCH = "main"
API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VERSION = "1.3.0"
TAG = "v" + VERSION
ZIP_PATH = r"D:\桌面\YibanAutoPoster-v1.3.0.zip"

DESCRIPTION = (
    "易班网薪每日自动刷 / 易班微社区自动发帖工具（Yiban AutoPoster）：Windows 桌面版，Python + Selenium/CDP，"
    "自动登录、自动生成内容、自动发帖并检测结果，帮助每日稳定积累易班网薪（活跃/积分）。"
    "关键词：易班 yiban 网薪 wangxin 刷网薪 每日任务 微社区 weishequ 自动发帖 发帖工具 发帖机 yiban.cn"
)
HOMEPAGE = "https://github.com/chapwpzip/yiban/releases"
TOPICS = [
    "yiban", "yiban-autoposter", "yiban-community", "yiban-wangxin", "wangxin",
    "weishequ", "e-class", "daily-task", "auto-post", "poster", "selenium", "cdp",
    "tkinter", "python", "windows", "captcha", "captcha-solver", "automation", "chinese",
]

FILES = [
    "README.md",
    "README.txt",
    "LICENSE",
    ".gitignore",
    "build.bat",
    "requirements.txt",
    "requirements-build.txt",
    "main.py",
    "yiban_auto/__init__.py",
    "yiban_auto/efui.py",
    "yiban_auto/gui.py",
    "yiban_auto/flow.py",
    "yiban_auto/content.py",
    "yiban_auto/captcha.py",
    "yiban_auto/captcha_cv.py",
    "yiban_auto/config.py",
    "yiban_auto/util.py",
    "yiban_auto/selftest.py",
    "yiban_auto/sysmouse.py",
    "yiban_auto/verify.py",
    "yiban_auto/secure_store.py",
    "yiban_auto/browser/__init__.py",
    "yiban_auto/browser/base.py",
    "yiban_auto/browser/cdp.py",
    "yiban_auto/browser/selenium_engine.py",
    "assets/yiban.ico",
    "assets/captcha-sample.png",
    "assets/captcha-sample.jpg",
    "assets/fonts/Saira.ttf",
    "assets/fonts/Saira-OFL.txt",
    "docs/screenshot-main.png",
    "docs/screenshot-presets.png",
    "docs/screenshot-captcha.png",
    "presets/正能量预置内容.txt",
    "presets/正能量预置内容.json",
    "presets/正能量预置内容2000.txt",
    "presets/正能量预置内容2000.json",
    "presets/古诗4000-读后感触.txt",
    "presets/古诗词文言文2000.txt",
    "presets/意林风-短文鉴赏300篇.txt",
    "presets/穿越致富100章.txt",
    "presets/抽象语录400句.txt",
    "tools/push_v130.py",
]


def request(method, url, token, body=None, raw=False, timeout=90):
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "yiban-autoposter-push",
    }
    if token:
        headers["Authorization"] = "token " + token
    if body is not None:
        data = body if raw else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/octet-stream" if raw else "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload.decode("utf-8") or "{}"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"[HTTP {exc.code}] {method} {url}\n{text}")


def git_blob_sha(path):
    data = open(path, "rb").read()
    header = b"blob %d\0" % len(data)
    return hashlib.sha1(header + data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    token = args.token.strip()
    if not token and not args.dry_run:
        raise SystemExit("缺少 token：--token ghp_xxx")

    # 1. 远端现状
    _, ref = request("GET", f"{API}/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", token)
    head = ref["object"]["sha"]
    _, tree = request("GET", f"{API}/repos/{OWNER}/{REPO}/git/trees/{head}?recursive=1", token)
    remote = {item["path"]: item["sha"] for item in tree.get("tree", []) if item["type"] == "blob"}

    changed, added, missing = [], [], []
    for rel in FILES:
        local = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(local):
            missing.append(rel)
            continue
        sha = git_blob_sha(local)
        if rel not in remote:
            added.append((rel, sha))
        elif remote[rel] != sha:
            changed.append((rel, sha))

    print(f"HEAD {head[:8]} · 远端 {len(remote)} 文件")
    print(f"新增 {len(added)}：{', '.join(p for p, _ in added) or '-'}")
    print(f"修改 {len(changed)}：{', '.join(p for p, _ in changed) or '-'}")
    if missing:
        print(f"本地缺失（跳过）：{', '.join(missing)}")
    if args.dry_run:
        return

    # 2. 建 blob + tree
    entries = []
    for rel, _ in added + changed:
        local = os.path.join(ROOT, rel.replace("/", os.sep))
        data = open(local, "rb").read()
        b64 = base64.b64encode(data).decode("ascii")
        _, blob = request(
            "POST", f"{API}/repos/{OWNER}/{REPO}/git/blobs", token,
            {"content": b64, "encoding": "base64"},
        )
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  blob {rel} {blob['sha'][:8]}")

    if not entries:
        print("没有文件变化，跳过提交。")
        new_commit = head
    else:
        _, new_tree = request(
            "POST", f"{API}/repos/{OWNER}/{REPO}/git/trees", token,
            {"base_tree": head, "tree": entries},
        )
        message = (
            f"v{VERSION}：终末地风格界面重做 + 搜索关键词优化\n\n"
            "· 界面按 Endfield 风格重做（纸色面板、切角按钮、单一强调色、深色命令栏与日志区）\n"
            "· 新增 yiban_auto/efui.py 自绘部件\n"
            "· README 增加中文/拼音关键词，便于搜索到本项目\n"
        )
        _, commit = request(
            "POST", f"{API}/repos/{OWNER}/{REPO}/git/commits", token,
            {"message": message, "tree": new_tree["sha"], "parents": [head]},
        )
        new_commit = commit["sha"]
        request("PATCH", f"{API}/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", token,
                {"sha": new_commit, "force": True})
        print(f"提交 {new_commit[:8]}")

    # 3. 仓库描述 / topics / 主页（搜索可见性）
    request("PATCH", f"{API}/repos/{OWNER}/{REPO}", token,
            {"description": DESCRIPTION, "homepage": HOMEPAGE, "has_issues": True})
    status, _ = request("PUT", f"{API}/repos/{OWNER}/{REPO}/topics", token, {"names": TOPICS})
    print("仓库描述与 topics 已更新", status)

    # 4. Release
    body = (
        "## 易班网薪每日自动工具 v1.3.0（Yiban AutoPoster）\n\n"
        "每天自动登录易班、自动生成内容、自动在微社区发帖并检测结果，用来稳定积累**易班网薪**与活跃度。\n\n"
        "关键词：易班 yiban 网薪 wangxin 刷网薪 每日任务 微社区 weishequ 自动发帖 发帖工具 发帖机\n\n"
        "### 本次更新\n"
        "- 界面按终末地（Endfield）风格重做：纸色底 + 白面板 + 1px 细线、单一强调色、切角按钮、深色命令栏与日志区\n"
        "- 标题/正文字数改成等宽 `实际 / 上限` 计数，越界黄底提示\n"
        "- 功能与 settings.json 完全不变，可直接覆盖旧版 exe\n\n"
        "### 下载\n"
        "- `YibanAutoPoster-v1.3.0.zip`：exe + README + settings.json + 9 套预置文案 + 使用说明 docx\n\n"
        "### 使用方法\n"
        "解压到普通目录 → 双击 `YibanAutoPoster.exe` → 填本人易班账号 → 点「开始发帖」。\n\n"
        "### 界面预览\n"
        "![主界面](https://raw.githubusercontent.com/chapwpzip/yiban/main/docs/screenshot-main.png)\n\n"
        "![预置内容编辑器](https://raw.githubusercontent.com/chapwpzip/yiban/main/docs/screenshot-presets.png)\n\n"
        "![验证码设置](https://raw.githubusercontent.com/chapwpzip/yiban/main/docs/screenshot-captcha.png)\n"
    )
    try:
        _, existing = request("GET", f"{API}/repos/{OWNER}/{REPO}/releases/tags/{TAG}", token)
    except SystemExit:
        existing = None
    if existing:
        _, rel = request("PATCH", f"{API}/repos/{OWNER}/{REPO}/releases/{existing['id']}", token,
                         {"name": f"易班微社区自动发帖工具 v{VERSION}", "body": body})
        print("Release 已存在，更新说明", rel["id"])
    else:
        _, rel = request("POST", f"{API}/repos/{OWNER}/{REPO}/releases", token, {
            "tag_name": TAG,
            "target_commitish": new_commit,
            "name": f"易班微社区自动发帖工具 v{VERSION}",
            "body": body,
            "draft": False,
            "prerelease": False,
        })
        print("Release 已创建", rel["id"])

    asset_name = f"YibanAutoPoster-v{VERSION}.zip"
    if os.path.exists(ZIP_PATH):
        existing_assets = {a["name"]: a["id"] for a in rel.get("assets", [])}
        if asset_name in existing_assets:
            request("DELETE", f"{API}/repos/{OWNER}/{REPO}/releases/assets/{existing_assets[asset_name]}", token)
        upload_url = rel["upload_url"].split("{")[0] + f"?name={urllib.parse.quote(asset_name)}"
        with open(ZIP_PATH, "rb") as fh:
            data = fh.read()
        req = urllib.request.Request(
            upload_url, data=data, method="POST",
            headers={
                "Authorization": "token " + token,
                "Content-Type": "application/zip",
                "Content-Length": str(len(data)),
                "User-Agent": "yiban-autoposter-push",
            },
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            up = json.loads(resp.read().decode("utf-8"))
        print(f"附件已上传 {up['name']} {up['size'] / 1048576:.2f} MB")
    else:
        print("未找到本地 zip：", ZIP_PATH)

    print("完成 ·", f"https://github.com/{OWNER}/{REPO}/releases/tag/{TAG}")


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (供 upload_url 使用)
    main()
