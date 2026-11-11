# -*- coding: utf-8 -*-
"""帖子内容生成：模板化与 OpenAI 兼容 LLM。"""
from __future__ import annotations

import json
import random
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import TEMPLATE_CATEGORIES
from .util import normalize_space, truncate, unique_suffix


class ContentError(RuntimeError):
    pass


@dataclass
class GeneratedPost:
    title: str
    body: str
    mode: str
    category: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


_DAILY_GREETINGS = [
    "最近还好吗？日子过得有点快，记得给自己留点喘气的时间。",
    "天气说变就变，出门前看一眼窗外，别冻着自己。",
    "今天也要好好吃饭，别随便对付一口。",
    "不管今天顺不顺，晚上都早点休息。",
    "没什么特别的事，就是来打个招呼，祝你今天心情不错。",
    "忙起来容易忘了喝水，看到这条就去喝一口吧。",
    "如果今天有点累，就先歇一会儿，事情做不完也没关系。",
    "日子普通也没关系，平平稳稳的就挺好。",
]
_FOOD_LINES = [
    "今天吃了碗热乎的面，吃完整个人都暖起来了。",
    "中午吃得太饱，晚上打算清淡一点。",
    "今天吃到一样挺合胃口的东西，心情跟着好了不少。",
    "最近胃口不错，三餐都按时吃了，算是小小的进步。",
    "刚吃完一顿简单的饭，没什么特别，但吃得很满足。",
    "今天喝到一杯热饮，甜度刚好，整个人都松快了些。",
    "有时候一顿好吃的，就能把普通的一天变得不错。",
]
_LIFE_LINES = [
    "这两天过得挺普通，没什么大事，平平稳稳就很好。",
    "把该做的事慢慢做完，心里会踏实一点。",
    "偶尔什么都不想，发一会儿呆，也算休息。",
    "最近在试着早点睡，虽然还不习惯，但白天精神确实好一些。",
    "有些事急不来，慢慢做反而更顺。",
    "今天没什么安排，随便走走停停，感觉也不错。",
    "日子大多是重复的，但偶尔也有小小的开心。",
    "收拾了一下手边的东西，地方清爽了，人也轻松一点。",
]
_DAILY_TITLES = [
    "随便聊两句",
    "今天也辛苦了",
    "记得好好吃饭",
    "最近还好吗",
    "一点日常小事",
    "说点日常废话",
    "今天过得还行",
    "来打个招呼",
    "普通的一天",
    "天气转凉了",
]
_POEM_TITLES = [
    "读一首诗再出发",
    "山色入窗来",
    "晚风与旧书",
    "春水初生时",
    "一蓑烟雨任平生",
    "把月光收进书页",
    "人间有味是清欢",
    "行到水穷处",
]
_POEMS = [
    ("行到水穷处，坐看云起时。", "王维《终南别业》"),
    ("竹杖芒鞋轻胜马，谁怕？一蓑烟雨任平生。", "苏轼《定风波》"),
    ("山重水复疑无路，柳暗花明又一村。", "陆游《游山西村》"),
    ("人间有味是清欢。", "苏轼《浣溪沙》"),
    ("海内存知己，天涯若比邻。", "王勃《送杜少府之任蜀州》"),
    ("长风破浪会有时，直挂云帆济沧海。", "李白《行路难》"),
    ("采菊东篱下，悠然见南山。", "陶渊明《饮酒》"),
    ("会当凌绝顶，一览众山小。", "杜甫《望岳》"),
]

_RANDOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _title(base: str, suffix: str = "") -> str:
    base = normalize_space(base)
    if suffix:
        base = f"{base}｜{suffix}"
    if len(base) > 30:
        base = base[:30]
    if len(base) < 5:
        base = (base + "随笔记录")[:5]
    return base


def _join_paragraphs(paragraphs: List[str]) -> str:
    lines = [p.strip() for p in paragraphs if p and p.strip()]
    return "\n\n".join(lines)


def _daily_paragraphs(topic: str) -> List[str]:
    """日常寒暄：短句、少细节，不写具体地点。"""
    food_topic = any(k in topic for k in ("吃", "饭", "美食", "早餐", "午餐", "晚餐", "夜宵", "零食"))
    weather_topic = any(k in topic for k in ("天气", "降温", "下雨", "冷", "热"))
    paragraphs: List[str] = []

    if food_topic or random.random() < 0.55:
        paragraphs.append(random.choice(_FOOD_LINES))

    greetings = list(_DAILY_GREETINGS)
    if weather_topic:
        weather_lines = [x for x in greetings if "天气" in x or "冻" in x]
        if weather_lines:
            line = random.choice(weather_lines)
            paragraphs.append(line)
            greetings.remove(line)

    paragraphs.append(random.choice(greetings))
    if random.random() < 0.7:
        paragraphs.append(random.choice(_LIFE_LINES))

    if len(paragraphs) < 2:
        for line in random.sample(_DAILY_GREETINGS, len(_DAILY_GREETINGS)):
            if line not in paragraphs:
                paragraphs.append(line)
                break

    unique: List[str] = []
    for paragraph in paragraphs:
        if paragraph not in unique:
            unique.append(paragraph)
    return unique[:3]


def _generate_template(category: str, topic: str = "") -> GeneratedPost:
    if category not in TEMPLATE_CATEGORIES:
        category = "日常寒暄"
    suffix = unique_suffix(4)
    topic = normalize_space(topic)

    if category == "古诗词":
        poem, source = random.choice(_POEMS)
        title = _title(random.choice(_POEM_TITLES), suffix)
        body = _join_paragraphs([
            f"「{poem}」",
            f"——{source}",
            "读到这里，会觉得古人的句子很短，却总能在忙碌里留出一点空隙。",
            "愿我们赶路的时候，也能偶尔停下来看看云。",
        ])
        return GeneratedPost(title, body, "template", category)

    if category == "生活随感":
        title = _title(random.choice([
            "把生活调成静音",
            "今天的小小整理",
            "给明天留一点余地",
            "慢一点也没关系",
            "日常里的微光",
        ]), suffix)
        body = _join_paragraphs(random.sample(_LIFE_LINES, 2) + [random.choice(_DAILY_GREETINGS)])
        return GeneratedPost(title, body, "template", category)

    if category == "随机字母数字":
        token = "".join(random.choice(_RANDOM_ALPHABET) for _ in range(28))
        title = _title("内容测试", suffix)
        body = _join_paragraphs([
            "这是一条由自动化工具生成的随机内容测试。",
            f"随机串：{token}",
            "提示：随机字母数字内容缺乏实际信息，可能被学校或平台管理员核查，请仅在合法、必要的测试场景使用。",
        ])
        return GeneratedPost(title, body, "template", category)

    # 日常寒暄（默认）：只说模糊的日常，不写具体地点和精确细节。
    title_base = random.choice(_DAILY_TITLES)
    if topic and len(topic) >= 5:
        title_base = topic[:20]
    title = _title(title_base, suffix)
    body = _join_paragraphs(_daily_paragraphs(topic))
    return GeneratedPost(title, body, "template", category)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        text = re.sub(r"^" + fence + r"[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*" + fence + r"$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise ContentError("LLM API 地址为空，请在设置中填写 OpenAI 兼容接口地址。")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _build_llm_prompt(topic: str) -> str:
    return (
        "你是易班微社区的一名普通学生。请写一条随手发的日常动态，不是作文，也不是客服回复。\n"
        f"主题：{topic}\n\n"
        "要求：\n"
        "1. 口气自然、口语化，像学生自己随手写的，不要模板腔、AI 腔、排比句或强行升华。\n"
        "2. 正文 60-180 字，分 2-3 段，每段一两句，短句为主。\n"
        "3. 可以写日常寒暄、天气、今天吃了什么、普通的心情。\n"
        "4. 不要编造具体地点、店名、人名、精确时间、金额、数量、课程名或活动名；不要写食堂、操场、图书馆、宿舍楼、教学楼、校门口等具体地点。\n"
        "5. 如果写吃的，只写吃了什么和感受，不写在哪吃、和谁吃、花了多少钱。\n"
        "6. 标题 5-16 个字，口语化，不夸张。\n"
        "7. 内容积极、合法，不涉及政治敏感、广告、引流、隐私、抄袭或违反社区规范的信息。\n"
        "8. 只输出 JSON，不要 Markdown 代码块，格式：{\"title\":\"标题\",\"body\":\"正文\"}。\n"
    )


def _generate_llm(topic: str, llm: Dict[str, Any]) -> GeneratedPost:
    api_key = str(llm.get("api_key") or "").strip()
    if not api_key:
        raise ContentError("LLM 模式需要 API Key，请先在“设置”中填写。")
    model = str(llm.get("model") or "").strip()
    if not model:
        raise ContentError("LLM 模式需要模型名称，请先在“设置”中填写。")
    topic = normalize_space(topic) or "随手发一条日常"
    prompt = _build_llm_prompt(topic)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个说话自然、有点口语化的普通学生，不写模板腔和空泛金句。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(llm.get("temperature", 0.8)),
        "max_tokens": 1000,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _endpoint(str(llm.get("base_url") or "")),
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "YibanAutoPoster/1.3.0",
        },
    )
    timeout = int(llm.get("timeout_seconds") or 90)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise ContentError(f"LLM 接口返回 HTTP {exc.code}：{detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ContentError(f"LLM 接口连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise ContentError("LLM 接口请求超时。") from exc
    try:
        outer = json.loads(body)
        llm_text = outer["choices"][0]["message"]["content"]
    except Exception as exc:
        raise ContentError(f"LLM 返回格式无法解析：{truncate(body, 300)}") from exc
    obj = _extract_json_object(llm_text)
    if not obj:
        title_m = re.search(r"标题[：:]\s*(.+)", llm_text)
        body_m = re.search(r"正文[：:]\s*([\s\S]+)", llm_text)
        if title_m and body_m:
            obj = {"title": title_m.group(1), "body": body_m.group(1)}
        else:
            raise ContentError(f"LLM 没有返回可解析的标题和正文：{truncate(llm_text, 300)}")
    title = normalize_space(str(obj.get("title") or ""))
    body_text = str(obj.get("body") or "").strip()
    body_text = re.sub(r"<br\s*/?>", "\n", body_text, flags=re.I)
    body_text = re.sub(r"</p\s*>", "\n", body_text, flags=re.I)
    body_text = re.sub(r"<[^>]+>", "", body_text)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
    if len(title) < 5:
        title = _title(title or (topic[:16] + "随笔"))
    if len(title) > 30:
        title = title[:30]
    if not body_text:
        raise ContentError("LLM 返回的正文为空。")
    if len(body_text) > 10000:
        body_text = body_text[:10000]
    return GeneratedPost(title, body_text, "llm", "LLM", {"model": model, "topic": topic})


def _generate_preset(preset: Optional[Dict[str, Any]], index: int = 0) -> GeneratedPost:
    """手动预置：按顺序或随机取一条，标题不足 5 字时按规则补齐。"""
    items = (preset or {}).get("items") or []
    cleaned = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        title = normalize_space(str(entry.get("title") or ""))
        body_text = str(entry.get("body") or "").strip()
        if not body_text:
            continue
        cleaned.append((title, body_text))
    if not cleaned:
        raise ContentError("预置内容为空：请在“预置内容”里至少填写一条标题和正文。")
    pick = str((preset or {}).get("pick") or "sequential").lower()
    if pick == "random":
        position = random.randrange(len(cleaned))
    else:
        position = max(0, index) % len(cleaned)
    title, body_text = cleaned[position]
    if not title:
        title = body_text.splitlines()[0][:16]
    title = _title(title)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()
    if len(body_text) > 10000:
        body_text = body_text[:10000]
    return GeneratedPost(title, body_text, "preset", "手动预置",
                         {"index": position, "total": len(cleaned), "pick": pick})


def _strip_preset_marker(line: str) -> str:
    """去掉「【1】」「1.」「- 」这类序号前缀。"""
    text = line.strip()
    text = re.sub(r"^[【\[]\s*\d+\s*[】\]]\s*", "", text)
    text = re.sub(r"^\d+\s*[.、)）]\s*", "", text)
    text = re.sub(r"^[-*·]\s+", "", text)
    return text.strip()


_PRESET_SKIP_PREFIXES = ("易班微社区自动发帖工具", "导入方法", "格式：", "====", "----")


def parse_preset_text(text: str) -> List[Dict[str, str]]:
    """把「空行分隔、首行标题」的纯文本解析成预置内容列表。"""
    blocks: List[str] = []
    buffer: List[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if buffer:
                blocks.append("\n".join(buffer).strip())
                buffer = []
            continue
        if not buffer and line.strip().startswith(_PRESET_SKIP_PREFIXES):
            continue
        buffer.append(line)
    if buffer:
        blocks.append("\n".join(buffer).strip())

    items: List[Dict[str, str]] = []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        first = _strip_preset_marker(lines[0])
        rest = "\n".join(lines[1:]).strip()
        # 首行短、且没有句末标点 → 当标题；否则整段都算正文，标题自动截取
        if rest and len(first) <= 30 and not re.search(r"[。！？；…]$", first):
            title, body = first, rest
        else:
            title, body = "", _strip_preset_marker(block)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if not body:
            continue
        items.append({"title": title, "body": body})
    return items


def load_preset_file(path: str) -> List[Dict[str, str]]:
    """读取预置内容文件：支持 .json（含 items 数组或直接是数组）与 .txt。"""
    from .util import read_text_file  # 延迟导入，避免循环依赖

    text = read_text_file(path)
    lowered = str(path).lower()
    if lowered.endswith(".json") or text.lstrip()[:1] in "{[":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ContentError("JSON 解析失败：" + str(exc)) from exc
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            raise ContentError("JSON 里没有找到 items 列表。")
        items: List[Dict[str, str]] = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            title = normalize_space(str(entry.get("title") or ""))
            body = str(entry.get("body") or "").strip()
            if body:
                items.append({"title": title, "body": body})
        if not items:
            raise ContentError("这个 JSON 里没有可用的正文。")
        return items

    items = parse_preset_text(text)
    if not items:
        raise ContentError("没有解析到任何内容：请用空行分隔每条，第一条为标题。")
    return items


def preset_to_text(items: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for index, item in enumerate(items, 1):
        title = normalize_space(str((item or {}).get("title") or ""))
        body = str((item or {}).get("body") or "").strip()
        if not body:
            continue
        lines.append("【%d】%s" % (index, title or body.splitlines()[0][:16]))
        lines.append(body)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def save_preset_file(items: List[Dict[str, Any]], path: str) -> int:
    """导出预置内容：.json 存结构化数据，.txt 存可读可编辑文本。"""
    from .util import write_text_file

    payload = [
        {"title": normalize_space(str((it or {}).get("title") or "")), "body": str((it or {}).get("body") or "").strip()}
        for it in items
        if str((it or {}).get("body") or "").strip()
    ]
    if str(path).lower().endswith(".json"):
        write_text_file(path, json.dumps({"version": 1, "items": payload}, ensure_ascii=False, indent=2))
    else:
        write_text_file(path, preset_to_text(payload))
    return len(payload)


def generate_post(
    mode: str,
    category: str = "日常寒暄",
    topic: str = "",
    llm: Optional[Dict[str, Any]] = None,
    preset: Optional[Dict[str, Any]] = None,
    index: int = 0,
) -> GeneratedPost:
    if mode == "llm":
        return _generate_llm(topic, llm or {})
    if mode == "preset":
        return _generate_preset(preset, index)
    return _generate_template(category, topic)


def extract_summary(body: str, limit: int = 200) -> str:
    text = re.sub(r"<[^>]+>", "", body or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + "…"


def validate_post(title: str, body: str) -> None:
    title = (title or "").strip()
    body = (body or "").strip()
    if not (5 <= len(title) <= 30):
        raise ContentError(f"标题必须为 5-30 个字，当前 {len(title)} 个字。")
    if not body:
        raise ContentError("正文不能为空。")
    if len(body) > 10000:
        raise ContentError(f"正文不能超过 10000 字，当前 {len(body)} 字。")
