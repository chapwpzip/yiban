# -*- coding: utf-8 -*-
"""离线识别数美「空间逻辑验证」（按文字找图形）。

不依赖任何第三方库和账号：
    1. 纯 Python 解码 PNG（浏览器截屏就是 PNG，不需要 Pillow）；
    2. 把“有颜色的像素”整体做连通域 —— 每个 3D 图形是一个整体，
       不按色相拆开，避免高光/阴影把同一个物体切成好几块；
    3. 用整块的平均色判断颜色，用填充率 / 宽度轮廓 / 长宽比判断形状；
    4. 解析题目里的“颜色 + 形状”，按顺序给出点击坐标。

局限：图形相互重叠、题目只写形状不写颜色、或出现规则外的图形时会失败；
调用方应把它当成免费的第一道尝试，失败就换图重试或交给视觉模型。
"""
from __future__ import annotations

import re
import struct
import zlib
from typing import Dict, List, Optional, Tuple


class DecodeError(RuntimeError):
    pass


# ---------------------------------------------------------------- PNG 解码


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(data: bytes) -> Tuple[int, int, bytearray]:
    """返回 (width, height, RGB 字节串)，只支持 8 位 PNG（浏览器截图就是）。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise DecodeError("不是 PNG")
    pos = 8
    width = height = depth = color_type = -1
    palette = b""
    idat = bytearray()
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos:pos + 4], "big")
        chunk_type = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if chunk_type == b"IHDR":
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            depth = chunk[8]
            color_type = chunk[9]
        elif chunk_type == b"PLTE":
            palette = chunk
        elif chunk_type == b"IDAT":
            idat += chunk
        elif chunk_type == b"IEND":
            break
    if width <= 0 or height <= 0:
        raise DecodeError("PNG 头不完整")
    if depth != 8:
        raise DecodeError("只支持 8 位 PNG")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise DecodeError("不支持的 PNG 颜色类型：" + str(color_type))
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise DecodeError("PNG 解压失败：" + str(exc))
    stride = width * channels
    bpp = channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        if pos >= len(raw):
            raise DecodeError("PNG 数据不完整")
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if filter_type == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif filter_type != 0:
            raise DecodeError("未知 PNG 行过滤器：" + str(filter_type))
        out[y * stride:(y + 1) * stride] = line
        prev = line

    rgb = bytearray(width * height * 3)
    if color_type == 2:
        rgb[:] = out
    elif color_type == 6:
        for i in range(width * height):
            rgb[i * 3:i * 3 + 3] = out[i * 4:i * 4 + 3]
    elif color_type == 0:
        for i in range(width * height):
            value = out[i]
            rgb[i * 3:i * 3 + 3] = bytes((value, value, value))
    elif color_type == 3:
        for i in range(width * height):
            index = out[i] * 3
            rgb[i * 3:i * 3 + 3] = palette[index:index + 3]
    elif color_type == 4:
        for i in range(width * height):
            value = out[i * 2]
            rgb[i * 3:i * 3 + 3] = bytes((value, value, value))
    return width, height, rgb


# ---------------------------------------------------------------- 颜色

COLOR_NAMES = {
    "red": "红色", "orange": "橙色", "yellow": "黄色", "green": "绿色",
    "blue": "蓝色", "purple": "紫色", "pink": "粉色", "cyan": "青色",
}

_COLOR_WORDS = {
    "红": "red", "橙": "orange", "黄": "yellow", "绿": "green",
    "蓝": "blue", "紫": "purple", "粉": "pink", "青": "cyan",
}

_SHAPE_WORDS = (
    ("圆锥", "cone"), ("锥体", "cone"), ("锥形", "cone"), ("三角锥", "cone"),
    ("四棱锥", "cone"), ("三棱锥", "cone"), ("棱锥", "cone"),
    ("圆柱", "cylinder"), ("柱体", "cylinder"), ("圆筒", "cylinder"),
    ("六棱柱", "prism"), ("五棱柱", "prism"), ("四棱柱", "prism"),
    ("三棱柱", "prism"), ("棱柱", "prism"), ("棱台", "prism"),
    ("正方体", "box"), ("立方体", "box"), ("方块", "box"), ("长方体", "box"),
    ("矩形体", "box"), ("立方", "box"),
    ("球体", "sphere"), ("圆球", "sphere"), ("球", "sphere"),
)

# 题目里的尺寸限定词
_QUALIFIERS = (("最小", "min"), ("最大", "max"))

SHAPE_NAMES = {"cone": "圆锥", "cylinder": "圆柱", "box": "方体", "sphere": "球", "prism": "棱柱"}

# 这几类外形接近，形状没认准时不作为硬条件。
_SHAPE_FAMILY = {"prism": {"prism", "cylinder", "box"}, "cylinder": {"cylinder", "prism"}, "box": {"box", "prism"}}

# 3D 明暗会让颜色偏一档（黄的暗面发橙），匹配时允许互为备选。
COLOR_ALIASES = {
    "yellow": ("orange",),
    "orange": ("yellow",),
    "red": ("pink", "orange"),
    "pink": ("red",),
}


def color_of(r: int, g: int, b: int) -> Optional[str]:
    mx = max(r, g, b)
    mn = min(r, g, b)
    delta = mx - mn
    if delta < 45 or mx < 55:
        return None
    if mx == r:
        hue = (60.0 * ((g - b) / delta)) % 360.0
    elif mx == g:
        hue = 60.0 * (((b - r) / delta) + 2.0)
    else:
        hue = 60.0 * (((r - g) / delta) + 4.0)
    if hue < 12 or hue >= 345:
        return "red"
    if hue < 45:
        return "orange"
    if hue < 70:
        return "yellow"
    if hue < 175:
        return "green"
    if hue < 200:
        return "cyan"
    if hue < 265:
        return "blue"
    if hue < 300:
        return "purple"
    return "pink"


# ---------------------------------------------------------------- 连通域


class Blob:
    __slots__ = ("color", "pixels", "x0", "y0", "x1", "y1", "cx", "cy", "area",
                 "fill", "aspect", "top_ratio", "bottom_ratio", "shape", "mean_rgb")

    def __init__(self) -> None:
        self.color = ""
        self.pixels: List[Tuple[int, int]] = []
        self.x0 = self.y0 = 10 ** 9
        self.x1 = self.y1 = -1
        self.cx = self.cy = 0.0
        self.area = 0
        self.fill = 0.0
        self.aspect = 0.0
        self.top_ratio = 0.0
        self.bottom_ratio = 0.0
        self.shape = "unknown"
        self.mean_rgb = (0, 0, 0)

    @property
    def width(self) -> int:
        return self.x1 - self.x0 + 1

    @property
    def height(self) -> int:
        return self.y1 - self.y0 + 1

    def as_dict(self) -> Dict[str, object]:
        return {
            "color": self.color,
            "shape": self.shape,
            "cx": round(self.cx, 1),
            "cy": round(self.cy, 1),
            "w": self.width,
            "h": self.height,
            "fill": round(self.fill, 3),
            "aspect": round(self.aspect, 2),
        }

    def describe(self) -> str:
        return (
            COLOR_NAMES.get(self.color, self.color)
            + SHAPE_NAMES.get(self.shape, self.shape)
            + "(" + str(int(self.cx)) + "," + str(int(self.cy)) + ")"
        )


def _neighbors(x: int, y: int, width: int, height: int):
    if x > 0:
        yield x - 1, y
    if x + 1 < width:
        yield x + 1, y
    if y > 0:
        yield x, y - 1
    if y + 1 < height:
        yield x, y + 1


def _components(mask: bytearray, width: int, height: int, min_area: int) -> List[List[Tuple[int, int]]]:
    seen = bytearray(width * height)
    blobs: List[List[Tuple[int, int]]] = []
    for start in range(width * height):
        if not mask[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        pixels: List[Tuple[int, int]] = []
        while stack:
            index = stack.pop()
            x, y = index % width, index // width
            pixels.append((x, y))
            for nx, ny in _neighbors(x, y, width, height):
                ni = ny * width + nx
                if mask[ni] and not seen[ni]:
                    seen[ni] = 1
                    stack.append(ni)
        if len(pixels) >= min_area:
            blobs.append(pixels)
    return blobs


def _row_widths(pixels: List[Tuple[int, int]]) -> Dict[int, int]:
    rows: Dict[int, int] = {}
    for _, y in pixels:
        rows[y] = rows.get(y, 0) + 1
    return rows


def edge_feature(pixels: List[Tuple[int, int]], rgb: bytearray, width: int,
                 y0: int, y1: int) -> Tuple[int, float]:
    """中间高度区域内，相邻列亮度的“台阶”数量和最大台阶。

    圆柱是平滑渐变（台阶≈0），方体有一条竖直棱，亮度会突然变化。
    """
    columns: Dict[int, List[float]] = {}
    for x, y in pixels:
        if y0 <= y <= y1:
            base = (y * width + x) * 3
            columns.setdefault(x, []).append((rgb[base] + rgb[base + 1] + rgb[base + 2]) / 3.0)
    xs = sorted(columns)
    if len(xs) < 8:
        return 0, 0.0
    lo = int(len(xs) * 0.15)
    hi = int(len(xs) * 0.85)
    interior = xs[lo:hi] or xs
    means = [sum(columns[x]) / len(columns[x]) for x in interior]
    diffs = [abs(means[i + 1] - means[i]) for i in range(len(means) - 1)]
    if not diffs:
        return 0, 0.0
    return sum(1 for d in diffs if d > 12), max(diffs)


def classify_shape(blob: Blob, rows: Dict[int, int], edge: Tuple[int, float] = (0, 0.0)) -> str:
    if not rows:
        return "unknown"
    ys = sorted(rows)
    top_y, bottom_y = ys[0], ys[-1]
    span = max(1, bottom_y - top_y)
    max_row = max(rows.values()) or 1
    band = max(1, int(span * 0.20))
    top_band = max(rows.get(y, 0) for y in range(top_y, min(bottom_y, top_y + band) + 1)) or 1
    bottom_band = max(rows.get(y, 0) for y in range(max(top_y, bottom_y - band), bottom_y + 1)) or 1
    blob.top_ratio = top_band / float(max_row)
    blob.bottom_ratio = bottom_band / float(max_row)
    fill = blob.fill
    aspect = blob.aspect

    big_steps, max_step = edge
    # 圆锥：三角形，填充率约 0.45-0.6
    if fill < 0.63:
        return "cone"
    # 球：接近圆形，没有竖直棱
    if 0.90 <= aspect <= 1.12 and fill >= 0.72 and big_steps == 0:
        return "sphere"
    # 方体：中间有一条竖直棱，相邻列亮度会突变
    if big_steps >= 1 and max_step >= 18:
        return "box"
    # 圆柱：平滑渐变、上下宽度基本一致
    return "cylinder"


def analyze(width: int, height: int, rgb: bytearray, min_area_ratio: float = 0.0012) -> List[Blob]:
    min_area = max(150, int(width * height * min_area_ratio))
    mask = bytearray(width * height)
    for index in range(width * height):
        base = index * 3
        if color_of(rgb[base], rgb[base + 1], rgb[base + 2]):
            mask[index] = 1

    blobs: List[Blob] = []
    for pixels in _components(mask, width, height, min_area):
        blob = Blob()
        blob.pixels = pixels
        xs = [p[0] for p in pixels]
        ys = [p[1] for p in pixels]
        blob.x0, blob.x1 = min(xs), max(xs)
        blob.y0, blob.y1 = min(ys), max(ys)
        blob.area = len(pixels)
        blob.cx = sum(xs) / float(blob.area)
        blob.cy = sum(ys) / float(blob.area)
        blob.fill = blob.area / float(max(1, blob.width * blob.height))
        blob.aspect = blob.width / float(max(1, blob.height))
        # 用最亮的那部分像素判断颜色：3D 图形的暗面会偏色（黄会发橙），
        # 受光面才是它的本色。
        samples = []
        for x, y in pixels:
            base = (y * width + x) * 3
            r, g, b = rgb[base], rgb[base + 1], rgb[base + 2]
            samples.append((r + g + b, r, g, b))
        samples.sort(key=lambda item: item[0], reverse=True)
        keep = samples[: max(1, int(len(samples) * 0.35))]
        total_r = sum(item[1] for item in keep)
        total_g = sum(item[2] for item in keep)
        total_b = sum(item[3] for item in keep)
        blob.mean_rgb = (
            total_r // len(keep),
            total_g // len(keep),
            total_b // len(keep),
        )
        blob.color = color_of(*blob.mean_rgb) or "unknown"
        edge = edge_feature(
            pixels, rgb, width,
            blob.y0 + int(blob.height * 0.30),
            blob.y0 + int(blob.height * 0.70),
        )
        blob.shape = classify_shape(blob, _row_widths(pixels), edge)
        blobs.append(blob)

    blobs = [b for b in blobs if b.width > 8 and b.height > 8]
    blobs.sort(key=lambda b: -b.area)
    return blobs


# ---------------------------------------------------------------- 题目解析


def parse_instruction(text: str) -> List[Dict[str, str]]:
    """把“请依次点击：红色正方体、蓝色圆锥体”拆成 [{color, shape}]。"""
    text = re.sub(r"^.*?点击\s*[：:]?", "", text or "")
    text = text.replace("依次", "").replace("请", "")
    tokens = re.split(r"[、，,;；。\s]+", text)
    result: List[Dict[str, str]] = []
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        color = ""
        shape = ""
        qualifier = ""
        for word, code in _QUALIFIERS:
            if word in token:
                qualifier = code
                break
        for word, code in _COLOR_WORDS.items():
            if word in token:
                color = code
                break
        for word, code in _SHAPE_WORDS:
            if word in token:
                shape = code
                break
        if color or shape:
            result.append({"color": color, "shape": shape, "qualifier": qualifier, "raw": token})
    return result


def match_points(blobs: List[Blob], targets: List[Dict[str, str]],
                 width: int, height: int) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    used = set()
    for target in targets:
        color = target.get("color") or ""
        shape = target.get("shape") or ""
        candidates = [b for b in blobs if id(b) not in used]
        if color:
            same_color = [b for b in candidates if b.color == color]
            if not same_color:
                for alias in COLOR_ALIASES.get(color, ()):
                    same_color = [b for b in candidates if b.color == alias]
                    if same_color:
                        break
            if same_color:
                candidates = same_color
            elif shape:
                candidates = [b for b in candidates if b.shape == shape]
        if shape:
            family = _SHAPE_FAMILY.get(shape, {shape})
            same_shape = [b for b in candidates if b.shape in family]
            if same_shape:
                candidates = same_shape
            elif color:
                # 形状判断可能不准（比如把六棱柱认成方体）：颜色对上了就先用
                pass
        if not candidates:
            return []
        qualifier = target.get("qualifier") or ""
        if qualifier == "min":
            chosen = min(candidates, key=lambda b: b.area)
        elif qualifier == "max":
            chosen = max(candidates, key=lambda b: b.area)
        else:
            chosen = max(candidates, key=lambda b: b.area)
        used.add(id(chosen))
        points.append((chosen.cx / width, chosen.cy / height))
    return points


def solve_png(data: bytes, instruction: str) -> Tuple[List[Tuple[float, float]], List[Dict[str, object]]]:
    width, height, rgb = decode_png(data)
    blobs = analyze(width, height, rgb)
    targets = parse_instruction(instruction)
    points = match_points(blobs, targets, width, height) if targets else []
    return points, [b.as_dict() for b in blobs]


def solve_file(path: str, instruction: str) -> Tuple[List[Tuple[float, float]], List[Dict[str, object]]]:
    with open(path, "rb") as handle:
        return solve_png(handle.read(), instruction)


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """最小 PNG 编码器（8 位 RGB），用于把标注图存盘排查。"""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride:(y + 1) * stride])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
