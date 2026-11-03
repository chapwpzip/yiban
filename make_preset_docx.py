# -*- coding: utf-8 -*-
"""把 presets/正能量预置内容.json 排成一份可打印的 docx 合集。"""
import json
import os
from collections import OrderedDict

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Cm

ACCENT = RGBColor(0xC0, 0x39, 0x2B)
GRAY = RGBColor(0x66, 0x66, 0x66)

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "presets", "正能量预置内容.json")
with open(src, "r", encoding="utf-8") as f:
    items = json.load(f)["items"]

doc = Document()
style = doc.styles["Normal"]
style.font.name = "Microsoft YaHei"
style.font.size = Pt(10.5)
style.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def cn(run, name="Microsoft YaHei"):
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)


doc.add_paragraph()
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("正能量预置内容合集")
r.bold = True
r.font.size = Pt(24)
r.font.color.rgb = ACCENT
cn(r)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("易班微社区自动发帖工具 · 预置素材（共 %d 条）" % len(items))
r.font.size = Pt(11)
r.font.color.rgb = GRAY
cn(r)

p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(30)
r = p.add_run("怎么用")
r.bold = True
r.font.size = Pt(13)
r.font.color.rgb = ACCENT
cn(r)
for line in [
    "1. 打开 YibanAutoPoster.exe →「2. 内容生成」→ 生成模式选「手动预置内容」；",
    "2. 点「预置内容…」→ 左下角「导入…」→ 选同目录 presets\\正能量预置内容.txt（或 .json）；",
    "3. 问「追加还是替换」时按需要选，导入后列表里就是这 60 条；",
    "4. 「按顺序」从头开始发，「随机」每次抽一条；配合「连续篇数」可一次发多篇。",
]:
    q = doc.add_paragraph(line)
    q.paragraph_format.left_indent = Cm(0.5)
    q.runs[0].font.size = Pt(10.5)
    cn(q.runs[0])

q = doc.add_paragraph()
q.paragraph_format.left_indent = Cm(0.5)
r = q.add_run("本文件是「给人看的版本」，用于阅读、打印、挑选；导入请用 presets 目录里的 txt / json。")
r.font.size = Pt(9.5)
r.font.color.rgb = GRAY
cn(r)

groups = OrderedDict()
for it in items:
    groups.setdefault(it.get("category") or "其它", []).append(it)

doc.add_page_break()
for category, group in groups.items():
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    r = p.add_run(category + "（%d 条）" % len(group))
    r.bold = True
    r.font.size = Pt(14)
    r.font.color.rgb = ACCENT
    cn(r)
    for it in group:
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(it["title"])
        r.bold = True
        r.font.size = Pt(11.5)
        cn(r)
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(0.4)
        p.paragraph_format.space_after = Pt(6)
        r = p.add_run(it["body"])
        r.font.size = Pt(10.5)
        cn(r)

footer = doc.sections[0].footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = footer.add_run("正能量预置内容合集 · 易班微社区自动发帖工具")
r.font.size = Pt(9)
r.font.color.rgb = GRAY
cn(r)

out = os.path.join(here, "正能量预置内容合集.docx")
doc.save(out)
print("saved", out, len(items))
