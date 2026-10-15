# -*- coding: utf-8 -*-
"""生成《轮台绿电制氢项目定容测算报告》（Word，公文格式，原生公式）。

输入：优化缓存/_bd10y_trk_容量.npy（理论最优）、_bd10y_trk_nobatt_容量.npy（不配储能）、
      _bd10y_trk_bx*_容量.npy（储能造价上浮）、_bd10y_trk_cgfix_容量.npy（申报需量不可削减）、
      _dispatch_10y_trk_推荐.npz（推荐配置十年逐时调度，经 dispatch_io 读取）、典型工况统计.json；
      报告/插图/*.png
输出：报告/轮台绿电制氢项目定容测算报告.docx
用法：python mk_report_docx.py
"""
import json
import os
import sys

import numpy as np
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt
from latex2mathml.converter import convert
from lxml import etree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dispatch_io                                      # noqa: E402

from paths import CACHE, DOC, FIG                       # noqa: E402

DST = os.path.join(DOC, "轮台绿电制氢项目定容测算报告.docx")


def office_xsl():
    """Microsoft Office 自带的 MathML→OMML 转换表；环境变量 MML2OMML 可指定路径。"""
    cands = [os.environ.get("MML2OMML", "")]
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
               os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        for sub in (r"root\Office16", "Office16", r"root\Office15", "Office15"):
            cands.append(os.path.join(pf, "Microsoft Office", sub, "MML2OMML.XSL"))
    for f in cands:
        if f and os.path.exists(f):
            return etree.XSLT(etree.parse(f))
    raise SystemExit("未找到 Microsoft Office 的 MML2OMML.XSL；请安装 Office，或用环境变量 MML2OMML 指定该文件路径")


XSL = office_xsl()
MNS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NPK, DH2 = 11.1259, 5.91e7 / 8000.0 * 11.1259
TANK_KG = 1985.2                                        # 2000m³、1.6→0.4MPa 单罐可用储氢量
WEATHER = [2019, 2020, 2021, 2022, 2023] * 2
FS, HT, KT, ZS, SONG, TNR = "仿宋", "黑体", "楷体", "华文中宋", "宋体", "Times New Roman"
CENTER, LEFT, RIGHT, JUST = (WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.LEFT,
                             WD_ALIGN_PARAGRAPH.RIGHT, WD_ALIGN_PARAGRAPH.JUSTIFY)

PPR_SEQ = ["pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
           "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
           "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
           "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
           "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
           "sectPr", "pPrChange"]
TBLPR_SEQ = ["tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize",
             "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar",
             "tblLook", "tblCaption", "tblDescription"]
TRPR_SEQ = ["cnfStyle", "divId", "gridBefore", "gridAfter", "wBefore", "wAfter", "cantSplit", "trHeight",
            "tblHeader", "tblCellSpacing", "jc", "hidden", "ins", "del", "trPrChange"]
TW = Mm(156)


# ───────────────────────────── 排版工具 ─────────────────────────────
def set_child(parent, seq, tag, attrs):
    el = parent.find(qn("w:" + tag))
    if el is None:
        el = OxmlElement("w:" + tag)
        later = set(seq[seq.index(tag) + 1:])
        for ch in parent:
            if isinstance(ch.tag, str) and etree.QName(ch).localname in later:
                ch.addprevious(el)
                break
        else:
            parent.append(el)
    for k, v in attrs.items():
        el.set(qn("w:" + k), v)
    return el


def font(run, east=FS, size=16, bold=False, latin=TNR):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = latin
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    for k in ("ascii", "hAnsi", "cs"):
        rf.set(qn("w:" + k), latin)
    rf.set(qn("w:eastAsia"), east)
    return run


def fmt(p, align=JUST, indent=None, line=28, rule="exact", before=0, after=0, keep_next=False):
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    if rule == "exact":
        pf.line_spacing_rule, pf.line_spacing = WD_LINE_SPACING.EXACTLY, Pt(line)
    elif rule == "atleast":
        pf.line_spacing_rule, pf.line_spacing = WD_LINE_SPACING.AT_LEAST, Pt(line)
    else:
        pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.first_line_indent = Pt(indent) if indent else None
    pf.keep_with_next = keep_next
    p.alignment = align
    return p


def para(doc, text="", east=FS, size=16, bold=False, align=JUST, indent=True, **kw):
    p = fmt(doc.add_paragraph(), align, size * 2 if indent else None, **kw)
    if text:
        font(p.add_run(text), east, size, bold)
    return p


def outline(p, lvl):
    set_child(p._p.get_or_add_pPr(), PPR_SEQ, "outlineLvl", {"val": str(lvl)})


def h1(doc, t):
    outline(para(doc, t, HT, 16, keep_next=True), 0)


def h2(doc, t):
    outline(para(doc, t, KT, 16, keep_next=True), 1)


def h3(doc, t):
    outline(para(doc, t, FS, 16, bold=True, keep_next=True), 2)


def omml(latex, display, size=None):
    mml = convert(latex, display="block" if display else "inline")
    root = XSL(etree.fromstring(mml.encode("utf-8"))).getroot()
    if size:
        for r in root.iter("{%s}r" % MNS):
            rpr = r.find(qn("w:rPr"))
            if rpr is None:
                rpr = OxmlElement("w:rPr")
                mr = r.find("{%s}rPr" % MNS)
                if mr is not None:
                    mr.addnext(rpr)
                else:
                    r.insert(0, rpr)
            for tag in ("sz", "szCs"):
                e = rpr.find(qn("w:" + tag))
                if e is None:
                    e = OxmlElement("w:" + tag)
                    rpr.append(e)
                e.set(qn("w:val"), str(int(size * 2)))
    return root


def mixed(doc, parts, indent=True):
    """正文中夹行内公式：parts 由字符串与 ("m", latex) 组成。"""
    p = fmt(doc.add_paragraph(), JUST, 32 if indent else None, 28, "atleast")
    for part in parts:
        if isinstance(part, tuple):
            p._p.append(omml(part[1], False))
        else:
            font(p.add_run(part), FS, 16)
    return p


def spacer(doc, h=6):
    fmt(doc.add_paragraph(), CENTER, None, h, "exact")


def no_borders(t):
    b = set_child(t._tbl.tblPr, TBLPR_SEQ, "tblBorders", {})
    for e in ("top", "left", "bottom", "right", "insideH", "insideV"):
        x = OxmlElement("w:" + e)
        x.set(qn("w:val"), "nil")
        b.append(x)


EQN = [0]


def equation(doc, latex):
    EQN[0] += 1
    t = doc.add_table(rows=1, cols=3)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    no_borders(t)
    ws = [Mm(16), TW - Mm(32), Mm(16)]
    for i, w in enumerate(ws):
        t.columns[i].width = w
        t.rows[0].cells[i].width = w
        t.rows[0].cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    p = fmt(t.rows[0].cells[1].paragraphs[0], CENTER, None, rule="single", before=2, after=2)
    p._p.append(omml(latex, True, 14))
    q = fmt(t.rows[0].cells[2].paragraphs[0], RIGHT, None, rule="single")
    font(q.add_run("(%d)" % EQN[0]), FS, 14)
    spacer(doc, 4)


def merge_first_col(t, rows):
    """首列留空的行并入上方同类别单元格（纵向合并）。"""
    groups, start = [], None
    for i, row in enumerate(rows, 1):
        if row[0] != "":
            if start is not None and i - 1 > start:
                groups.append((start, i - 1))
            start = i
    if start is not None and len(rows) > start:
        groups.append((start, len(rows)))
    for a, b in groups:
        m = t.cell(a, 0).merge(t.cell(b, 0))
        for p in m.paragraphs[1:]:
            if not p.text.strip():
                p._p.getparent().remove(p._p)
        m.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


TBN = [0]


def table(doc, title, header, rows, widths, aligns=None, size=10.5, merge=False):
    TBN[0] += 1
    para(doc, "表%d　%s" % (TBN[0], title), HT, 12, align=CENTER, indent=False,
         rule="atleast", line=20, before=6, after=3, keep_next=True)
    t = doc.add_table(rows=1 + len(rows), cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for i, w in enumerate(widths):
        t.columns[i].width = Mm(w)
    for ri, row in enumerate([header] + rows):
        tr = t.rows[ri]
        trpr = tr._tr.get_or_add_trPr()
        set_child(trpr, TRPR_SEQ, "cantSplit", {})
        if ri == 0:
            set_child(trpr, TRPR_SEQ, "tblHeader", {})
        for ci, val in enumerate(row):
            cell = tr.cells[ci]
            cell.width = Mm(widths[ci])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            al = CENTER if (ri == 0 or not aligns) else aligns[ci]
            keep = len(rows) <= 12 and ri < len(rows)            # 短表整表不跨页，避免表题与表头孤悬页末
            p = fmt(cell.paragraphs[0], al, None, rule="single", before=1.5, after=1.5, keep_next=keep)
            for li, ln in enumerate(str(val).split("\n")):
                if li:
                    p.add_run().add_break()
                font(p.add_run(ln), HT if ri == 0 else FS, size)
    if merge:
        merge_first_col(t, rows)
    spacer(doc, 8)
    return TBN[0]


FGN = [0]


def figure(doc, path, title, width=156):
    FGN[0] += 1
    p = fmt(doc.add_paragraph(), CENTER, None, rule="single", before=6, keep_next=True)
    p.add_run().add_picture(path, width=Mm(width))
    para(doc, "图%d　%s" % (FGN[0], title), HT, 12, align=CENTER, indent=False,
         rule="atleast", line=20, after=6)
    return FGN[0]


def add_field(p, instr, east=SONG, size=14):
    for kind in ("begin", "instr", "end"):
        r = font(p.add_run(), east, size)
        if kind == "instr":
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = " %s " % instr
            r._r.append(it)
        else:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), kind)
            r._r.append(fc)


def setup(doc):
    global TW
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin, sec.bottom_margin = Mm(37), Mm(35)
    sec.left_margin, sec.right_margin = Mm(28), Mm(26)
    sec.footer_distance = Mm(24)
    TW = sec.page_width - sec.left_margin - sec.right_margin
    st = doc.styles["Normal"]
    st.font.name, st.font.size = TNR, Pt(16)
    rpr = st.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    rf.set(qn("w:eastAsia"), FS)
    ppr = st.element.get_or_add_pPr()
    set_child(ppr, PPR_SEQ, "autoSpaceDE", {"val": "0"})       # 中文与数字、字母之间不自动加空
    set_child(ppr, PPR_SEQ, "autoSpaceDN", {"val": "0"})
    dd = doc.styles.element.find(qn("w:docDefaults"))
    if dd is not None:
        pd_ = dd.find(qn("w:pPrDefault"))
        if pd_ is None:
            pd_ = OxmlElement("w:pPrDefault")
            dd.append(pd_)
        pp = pd_.find(qn("w:pPr"))
        if pp is None:
            pp = OxmlElement("w:pPr")
            pd_.append(pp)
        set_child(pp, PPR_SEQ, "autoSpaceDE", {"val": "0"})
        set_child(pp, PPR_SEQ, "autoSpaceDN", {"val": "0"})
    fp = fmt(sec.footer.paragraphs[0], CENTER, None, rule="single")
    font(fp.add_run("— "), SONG, 14)
    add_field(fp, "PAGE")
    font(fp.add_run(" —"), SONG, 14)


# ───────────────────────────── 数据 ─────────────────────────────
def npy(name):
    f = os.path.join(CACHE, name)
    return np.load(f, allow_pickle=True).item() if os.path.exists(f) else None


def load_data():
    cap = npy("_bd10y_trk_容量.npy")
    nob = npy("_bd10y_trk_nobatt_容量.npy")
    sens = {}
    for bx in ("1.1", "1.2", "1.3"):
        r = npy("_bd10y_trk_bx%s_容量.npy" % bx)
        if r is not None:
            sens[float(bx)] = r
    cgf = npy("_bd10y_trk_cgfix_容量.npy")
    d = dispatch_io.load()
    typ = json.load(open(os.path.join(CACHE, "典型工况统计.json"), encoding="utf-8"))
    return cap, nob, sens, cgf, d, typ


def f1(v):
    return "%.1f" % v


def total(res):
    return sum(res["parts"].values())


def es_of(res):
    c = res["cap"]
    return c["储能2h_MWh"] + c["储能4h_MWh"], c["储能2h_MW"] + c["储能4h_MW"]


def main():
    cap, nob, sens, cgf, d, typ = load_data()
    C = cap["cap"]
    # 不配置储能的参照取已算出的不配储能方案中年均总成本最低者（造价上浮后优化结果不配储能的算例也计入）
    nob = min([nob] + [r for r in sens.values() if es_of(r)[0] <= 0.5], key=total)
    TOT, TOT0 = total(cap), total(nob)
    # 推荐配置：调度文件里的容量、十年投资与需量费、各年运行费（扣除消多解的极小成本项）
    xr, inv, fr = d["x"], d["inv"], d["f"] - d["epsy"]
    TOTR = (inv.sum() + fr.sum()) / 10 / 1e4
    PR = [("购电电量电费", fr.sum()), ("光伏投资年化", inv[0]), ("容量电费", inv[1]),
          ("电解槽投资年化", inv[2]), ("储能投资年化", inv[5] + inv[6]), ("储氢罐投资年化", inv[3]),
          ("增压机投资年化", inv[4])]
    PR = [(k, v / 10 / 1e4) for k, v in PR]
    E = {k: d[k].sum(axis=1) / 1e6 for k in ("光伏可发", "光伏利用", "弃光", "下网", "储能充电", "储能放电",
                                            "电解槽", "增压", "炼化")}
    h2y = d["产氢"].sum(axis=1)                             # Nm³/年
    load = E["炼化"] + E["电解槽"] + E["增压"]
    green = 1 - E["下网"] / load
    e_es = (xr[5] + xr[6]) / 1e3
    cycles = E["储能放电"].mean() * 1e3 / e_es
    spec = E["电解槽"].sum() * 1e6 / h2y.sum()
    ref_share = TOTR * E["炼化"].sum() / load.sum()
    h2_kg = h2y.mean() / NPK
    h2_cost = (TOTR - ref_share) * 1e4 / h2_kg
    n_tank = int(round(xr[3] / NPK / TANK_KG))

    doc = Document()
    setup(doc)
    p = fmt(doc.add_paragraph(), CENTER, None, 36, "exact", after=18)
    font(p.add_run("轮台绿电制氢项目定容测算报告"), ZS, 22)

    # 一、定容背景
    h1(doc, "一、定容背景")
    para(doc, "220万吨/年塔河劣质重质原油浆态床加氢解构改质项目为新建项目，建设地点位于新疆巴音郭楞蒙古自治州"
              "轮台县拉依苏化工园区内。项目采用浆态床加氢解构工艺，年操作时间8000小时，外购氢气5.91万吨/年，"
              "年耗电量28440.4万kW·h，拟从当地电网引入2路110kV电源。可行性研究报告提出另行立项规划电解水"
              "制氢装置为本项目提供氢气。")
    para(doc, "为以可再生能源制氢替代外购氢气，拟配套建设光伏电站、电化学储能、电解水制氢装置及储氢设施，"
              "与炼化装置共用并网点，光伏余电不上网。本报告以十年全周期用能总成本最小为目标，采用2019—2023年"
              "逐时气象数据和若羌节点2025年7月—2026年6月逐时电价，对光伏、储能、电解槽、储氢罐、增压机容量"
              "及并网申报需量开展联合定容测算。")

    # 二、定容结果
    h1(doc, "二、定容结果")
    h2(doc, "（一）配置结果")
    para(doc, "十年全周期联合优化得到理论最优配置，按设备成套规格取整形成推荐配置，见表1。"
              "本章其余内容及第三、六章运行结果均按推荐配置计算。")
    table(doc, "设备配置结果",
          ["序号", "项目", "理论最优配置\n（优化计算值）", "推荐配置", "推荐配置说明"],
          [["1", "光伏电站", "%.1fMWp" % C["光伏MWp"], "%.0fMWp" % (xr[0] / 1e3),
            "交流侧%.0fMW，容配比1.2" % (xr[0] / 1e3 / 1.2)],
           ["2", "电化学储能\n（4小时系统）", "%.1fMWh/%.1fMW" % (C["储能4h_MWh"], C["储能4h_MW"]),
            "%.0fMWh/%.0fMW" % (xr[6] / 1e3, xr[6] / 4e3), "—"],
           ["3", "电化学储能\n（2小时系统）", "%.1fMWh/%.1fMW" % (C["储能2h_MWh"], C["储能2h_MW"]),
            "%.0fMWh/%.0fMW" % (xr[5] / 1e3, xr[5] / 2e3), "—"],
           ["4", "电解槽", "%.1fNm³/h" % C["电解槽Nm3h"], "%.0fNm³/h" % xr[2],
            "%d套4×1000Nm³/h碱性电解水制氢系统" % round(xr[2] / 4000)],
           ["5", "储氢罐", "可用储氢量%.1ft" % C["罐t"], "%d台" % n_tank,
            "单台2000m³、1.6MPa，合计可用储氢量%.0ft" % (xr[3] / NPK / 1e3)],
           ["6", "增压机", "%.1fkg/h" % C["泵体kgh"], "%.0fkg/h" % (xr[4] / NPK), "—"],
           ["7", "并网申报需量", "%.2fMW" % C["申报需量MW"], "%.0fMW" % (xr[1] / 1e3), "—"],
           ["8", "年均总成本", "%.1f万元" % TOT, "%.1f万元" % TOTR, "—"]],
          [12, 30, 36, 34, 44], [CENTER, LEFT, CENTER, CENTER, LEFT], 10)

    h2(doc, "（二）成本构成")
    pr = dict(PR)
    para(doc, "推荐配置年均总成本%.1f万元，其中购电电量电费、光伏投资年化、容量电费分别占%.1f%%、%.1f%%、%.1f%%，"
              "构成见表2。" % (TOTR, pr["购电电量电费"] / TOTR * 100, pr["光伏投资年化"] / TOTR * 100,
                           pr["容量电费"] / TOTR * 100))
    rows = [[str(i), k, f1(v), "%.1f%%" % (v / TOTR * 100)] for i, (k, v) in enumerate(PR, 1)]
    rows.append(["", "合计", f1(TOTR), "100.0%"])
    table(doc, "年均成本构成（推荐配置）", ["序号", "项目", "年均成本（万元）", "占比"], rows, [14, 62, 44, 36],
          [CENTER, LEFT, CENTER, CENTER])

    h2(doc, "（三）主要运行指标")
    cur_rate = E["弃光"].sum() / E["光伏可发"].sum()
    table(doc, "主要运行指标（推荐配置，十年年均）", ["序号", "指标", "数值", "单位"],
          [["1", "光伏理论发电量", f1(E["光伏可发"].mean()), "GWh"],
           ["2", "光伏实际发电量", f1(E["光伏利用"].mean()), "GWh"],
           ["3", "弃光电量（弃光率）", "%.1f（%.1f%%）" % (E["弃光"].mean(), cur_rate * 100), "GWh"],
           ["4", "电网购电量", f1(E["下网"].mean()), "GWh"],
           ["5", "储能放电量", f1(E["储能放电"].mean()), "GWh"],
           ["6", "储能等效循环次数", "%.0f" % cycles, "次/年"],
           ["7", "电解槽用电量", f1(E["电解槽"].mean()), "GWh"],
           ["8", "制氢量", "%.3f" % (h2_kg / 1e7), "万t"],
           ["9", "制氢综合电耗", "%.3f" % spec, "kWh/Nm³"],
           ["10", "绿电占比", "%.1f%%" % (100 * (1 - E["下网"].sum() / load.sum())), "—"],
           ["11", "单位制氢成本", "%.2f" % h2_cost, "元/kg"]],
          [14, 64, 42, 36], [CENTER, LEFT, CENTER, CENTER])
    para(doc, "单位制氢成本按年均总成本扣除炼化装置分摊费用后除以年均制氢量计算，炼化装置按其用电量占总用电量的"
              "比例分摊。")

    # 三、典型工况
    h1(doc, "三、典型工况")
    para(doc, "按推荐配置选取夏季光伏大发、冬季光伏偏弱和全厂检修三类工况，各取连续3天逐时调度结果（图1至图3）。"
              "图中上部为电力平衡，电源侧为正值、用电侧为负值；中部为购电电价；下部为储能剩余电量与储氢罐储氢量。")
    s, w, m = typ["夏季"], typ["冬季"], typ["检修"]

    def period(x):
        return "第%d年%d月%d日至%d月%d日" % (x["年"], x["起月"], x["起日"], x["止月"], x["止日"])

    def grid_txt(x):
        t = "电网购电最大功率%.1fMW" % x["下网最大MW"]
        if x["下网达申报需量小时"] > 0:
            t += "，%d小时达到申报需量" % x["下网达申报需量小时"]
        return t

    tank_moves = lambda x: x["罐存最大t"] - x["罐存最小t"] > 5

    h2(doc, "（一）夏季光伏大发工况")
    figure(doc, os.path.join(FIG, "图3_夏季典型工况.png"), "夏季光伏大发工况逐时调度")
    low_mid = s["电价白天均值"] < s["电价夜间均值"] - 0.05
    day_p = ["午间光伏大发" + ("、电价处于低位" if low_mid else ""),
             "电解槽平均负荷率%.0f%%" % (s["负荷率白天均值"] * 100)]
    if s["白天储能充电MWh"] > 50:
        day_p.append("储能充电")
    if tank_moves(s):
        day_p.append("富余氢气进入储氢罐")
    night_p = ["夜间%s电解槽平均负荷率降至%.0f%%" % ("电价上升，" if low_mid else "", s["负荷率夜间均值"] * 100)]
    if s["夜间储能放电MWh"] > 50:
        night_p.append("储能放电")
    if tank_moves(s):
        night_p.append("储氢罐补充供氢")
    night_p.append(grid_txt(s))
    para(doc, "%s光伏理论发电量%.0fMWh，最大出力%.0fMW，%s。%s；%s。储氢罐储氢量在%.1f～%.1ft之间变化。"
         % (period(s), s["光伏可发MWh"], s["光伏最大出力MW"],
            "无弃光" if s["弃光MWh"] < 1 else "弃光电量%.0fMWh" % s["弃光MWh"],
            "，".join(day_p), "，".join(night_p), s["罐存最小t"], s["罐存最大t"]))

    h2(doc, "（二）冬季光伏偏弱工况")
    figure(doc, os.path.join(FIG, "图4_冬季典型工况.png"), "冬季光伏偏弱工况逐时调度")
    es_txt = ("，储能充电%.0fMWh、放电%.0fMWh" % (w["储能充电MWh"], w["储能放电MWh"])
              if w["储能放电MWh"] > 50 else "")
    para(doc, "%s光伏理论发电量%.0fMWh，为夏季典型时段的%.0f%%。电解槽用电主要依靠电网购电，%s；白天电解槽平均负荷率%.0f%%，"
              "夜间%.0f%%%s，储氢罐储氢量在%.1f～%.1ft之间变化。"
         % (period(w), w["光伏可发MWh"], w["光伏可发MWh"] / s["光伏可发MWh"] * 100, grid_txt(w),
            w["负荷率白天均值"] * 100, w["负荷率夜间均值"] * 100, es_txt, w["罐存最小t"], w["罐存最大t"]))

    h2(doc, "（三）全厂检修工况")
    figure(doc, os.path.join(FIG, "图5_检修典型工况.png"), "全厂检修工况逐时调度")
    mt = "4月1日0时起炼化装置与电解槽停运，用电负荷%s为零，光伏出力%s全部弃光，储氢罐储氢量保持%.1ft" % (
        "与电网购电量均" if m.get("检修期下网MWh", 0) < 1 else "",
        "除储能充电外" if m.get("检修期储能充电MWh", 0) >= 1 else "", m["罐存止t"])
    para(doc, "%s跨越检修开始时刻。3月31日各装置正常运行，%s；%s。3天弃光电量%.0fMWh。"
         % (period(m), grid_txt(m), mt, m["弃光MWh"]))

    # 四、测算模型
    h1(doc, "四、测算模型")
    h2(doc, "（一）系统构成及工艺流程")
    figure(doc, os.path.join(FIG, "图1_系统构成.png"), "系统构成及工艺流程")
    para(doc, "光伏电站、公共电网与电化学储能接入110kV并网点，由并网点母线向炼化装置和制氢系统供电。制氢系统电力"
              "经整流变压器及整流柜转换为直流后供碱性电解槽，电解槽工作压力1.6MPa；氢气经气液分离、纯化后压力"
              "1.4MPa、纯度99.999%，直供浆态床加氢解构改质装置。制氢量大于用氢量时，富余氢气经增压机送入储氢罐；"
              "制氢量不足时，由储氢罐补充供氢。")

    h2(doc, "（二）目标函数")
    para(doc, "以十年全周期年化总成本最小为目标，包括购电电量电费、容量电费及各类设备投资年化费用：")
    equation(doc, r"\mathrm{min}\ F=\sum_{y=1}^{10}w_y\sum_{t=1}^{8760}c^{\mathrm{e}}_{t}P^{\mathrm{g}}_{y,t}"
                  r"+120c^{\mathrm{d}}C^{\mathrm{d}}+10\sum_{k}\theta_k\rho_kK_k")
    mixed(doc, ["式中，", ("m", "F"), "为十年全周期年化总成本；", ("m", "w_y"), "为第", ("m", "y"),
                "年运行费贴现权重；", ("m", r"c^{\mathrm{e}}_{t}"), "为第", ("m", "t"), "小时购电电价，为节点电价与"
                "系统运行费折价、上网环节线损电价、政府性基金及附加之和；", ("m", r"P^{\mathrm{g}}_{y,t}"),
                "为电网购电功率；", ("m", r"c^{\mathrm{d}}"), "为容量电价；", ("m", r"C^{\mathrm{d}}"), "为申报需量；",
                ("m", "K_k"), "、", ("m", r"\rho_k"), "、", ("m", r"\theta_k"),
                "分别为第", ("m", "k"), "类设备的容量、单位造价和年化系数。"])
    para(doc, "运行费贴现权重及设备年化系数按下式计算：")
    equation(doc, r"w_y=\frac{(1+r)^{-y}}{\frac{1}{10}\sum_{j=1}^{10}(1+r)^{-j}}")
    equation(doc, r"\theta_k=\frac{\left(1-PV^{\mathrm{dep}}_{k}-PV^{\mathrm{sal}}_{k}\right)"
                  r"\mathrm{CRF}\left(r,n_k\right)}{1-T}+\omega_k")
    equation(doc, r"\mathrm{CRF}\left(r,n_k\right)=\frac{r(1+r)^{n_k}}{(1+r)^{n_k}-1}")
    equation(doc, r"PV^{\mathrm{dep}}_{k}=\sum_{i=1}^{m_k}\frac{T(1-s)}{m_k(1+r)^{i}},\quad "
                  r"PV^{\mathrm{sal}}_{k}=\frac{s}{(1+r)^{n_k}}")
    mixed(doc, ["式中，", ("m", "r"), "为折现率；", ("m", "n_k"), "为设备使用寿命；", ("m", "T"), "为所得税率；",
                ("m", r"\omega_k"), "为年运维费率；", ("m", r"PV^{\mathrm{dep}}_{k}"), "、", ("m", r"PV^{\mathrm{sal}}_{k}"),
                "分别为折旧抵税额现值与期末残值现值占投资的比例；", ("m", "m_k"), "为折旧年限，取15年与设备使用寿命的"
                "较小值；", ("m", "s"), "为残值率。"])

    h2(doc, "（三）约束条件")
    h3(doc, "1.电力平衡约束")
    para(doc, "各时段电源侧供电与用电侧负荷平衡：")
    equation(doc, r"P^{\mathrm{pv}}_{t}+P^{\mathrm{g}}_{t}+P^{\mathrm{dis}}_{t}=P^{\mathrm{L}}_{t}+P^{\mathrm{el}}_{t}"
                  r"+P^{\mathrm{cp}}_{t}+P^{\mathrm{ch}}_{t}")
    mixed(doc, ["式中，", ("m", r"P^{\mathrm{pv}}_{t}"), "为光伏实际出力；", ("m", r"P^{\mathrm{dis}}_{t}"), "、",
                ("m", r"P^{\mathrm{ch}}_{t}"), "分别为储能放电、充电功率；", ("m", r"P^{\mathrm{L}}_{t}"), "为炼化装置负荷；",
                ("m", r"P^{\mathrm{el}}_{t}"), "为电解槽用电功率；", ("m", r"P^{\mathrm{cp}}_{t}"), "为增压机用电功率。"])
    h3(doc, "2.光伏出力约束")
    equation(doc, r"P^{\mathrm{pv}}_{t}+P^{\mathrm{cur}}_{t}=\alpha_tC^{\mathrm{pv}}")
    mixed(doc, ["式中，", ("m", r"\alpha_t"), "为光伏逐时出力系数，已计入容配比1.2的逆变器限幅；",
                ("m", r"C^{\mathrm{pv}}"), "为光伏装机容量；", ("m", r"P^{\mathrm{cur}}_{t}"), "为弃光功率。"])
    h3(doc, "3.电网购电功率约束")
    equation(doc, r"0\le P^{\mathrm{g}}_{t}\le C^{\mathrm{d}}")
    h3(doc, "4.电解槽运行约束")
    para(doc, "电解槽产氢量在调节范围内：")
    equation(doc, r"\lambda^{\mathrm{min}}Q\le q_t\le\lambda^{\mathrm{max}}Q")
    mixed(doc, ["式中，", ("m", "q_t"), "为产氢量；", ("m", "Q"), "为电解槽额定产氢能力；",
                ("m", r"\lambda^{\mathrm{min}}"), "、", ("m", r"\lambda^{\mathrm{max}}"), "分别为调节范围下限和上限。"])
    para(doc, "电解槽比电耗随负荷率线性变化，用电功率为：")
    equation(doc, r"\eta^{\mathrm{r}}P^{\mathrm{el}}_{t}=\left(a_t+b\frac{q_t}{Q}\right)q_t")
    mixed(doc, ["式中，", ("m", r"\eta^{\mathrm{r}}"), "为整流效率；", ("m", "a_t"), "、", ("m", "b"),
                "为比电耗系数。上式右端对", ("m", r"\left(q_t,Q\right)"), "为凸函数，采用切线族线性化，"
                "使电解槽容量作为决策变量时模型仍为线性规划："])
    equation(doc, r"\eta^{\mathrm{r}}P^{\mathrm{el}}_{t}\ge\left(a_t+2bu\right)q_t-bu^{2}Q,\quad "
                  r"u=0.25,0.35,\cdots,1.15")
    para(doc, "电解槽用电功率不超过调节上限对应功率，比电耗系数计入逐年衰减：")
    equation(doc, r"P^{\mathrm{el}}_{t}\le\frac{\left(a_t\lambda^{\mathrm{max}}+b\left(\lambda^{\mathrm{max}}\right)^{2}"
                  r"\right)Q}{\eta^{\mathrm{r}}}")
    equation(doc, r"a_t=a_0(1+\delta)^{\tau_t/8760}+a^{\mathrm{aux}}")
    mixed(doc, ["式中，", ("m", "a_0"), "为投运初期比电耗系数；", ("m", r"\delta"), "为电耗年衰减率；",
                ("m", r"\tau_t"), "为自投运起累计小时数；", ("m", r"a^{\mathrm{aux}}"), "为辅助设备功耗折算值。"])
    h3(doc, "5.储氢约束")
    equation(doc, r"S_{t+1}=S_t+q_t-d_t,\quad 0\le S_t\le H")
    equation(doc, r"\left|q_t-d_t\right|\le\beta H")
    equation(doc, r"f_t\ge q_t-d_t,\quad 0\le f_t\le C^{\mathrm{cp}},\quad P^{\mathrm{cp}}_{t}=\kappa f_t")
    mixed(doc, ["式中，", ("m", "S_t"), "为储氢罐储氢量；", ("m", "d_t"), "为加氢装置用氢量；", ("m", "H"),
                "为储氢罐可用容量；", ("m", r"\beta"), "为进出气速率上限系数；", ("m", "f_t"), "为进罐氢气量；",
                ("m", r"C^{\mathrm{cp}}"), "为增压机处理能力；", ("m", r"\kappa"), "为增压单位电耗。"])
    h3(doc, "6.储能约束")
    equation(doc, r"0\le P^{\mathrm{ch}}_{t}\le P^{\mathrm{es}},\quad 0\le P^{\mathrm{dis}}_{t}\le P^{\mathrm{es}},\quad "
                  r"P^{\mathrm{ch}}_{t}+P^{\mathrm{dis}}_{t}\le P^{\mathrm{es}}")
    equation(doc, r"E_{t+1}=E_t+\eta P^{\mathrm{ch}}_{t}-\frac{P^{\mathrm{dis}}_{t}}{\eta},\quad "
                  r"0\le E_t\le E^{\mathrm{es}}=hP^{\mathrm{es}}")
    equation(doc, r"\sum_{t=1}^{8760}P^{\mathrm{dis}}_{t}\le NE^{\mathrm{es}}")
    mixed(doc, ["式中，", ("m", r"P^{\mathrm{es}}"), "、", ("m", r"E^{\mathrm{es}}"), "分别为储能额定功率与额定容量；",
                ("m", "E_t"), "为储能剩余电量；", ("m", r"\eta"), "为充、放电效率；", ("m", "h"),
                "为储能时长，分2小时、4小时两类；", ("m", "N"), "为年吞吐次数上限。储能剩余电量与储氢罐储氢量均按年首末相等。"])
    h3(doc, "7.绿电直连政策约束")
    para(doc, "按《国家发展改革委 国家能源局关于有序推动绿电直连发展有关事项的通知》（发改能源〔2025〕650号）要求，"
              "按年度考核新能源自发自用电量占总可用发电量比例与占总用电量比例，光伏余电不上网：")
    equation(doc, r"\sum_{t=1}^{8760}P^{\mathrm{pv}}_{t}\ge\gamma^{\mathrm{s}}\sum_{t=1}^{8760}\alpha_tC^{\mathrm{pv}}")
    equation(doc, r"\sum_{t=1}^{8760}P^{\mathrm{g}}_{t}\le\left(1-\gamma^{\mathrm{gr}}\right)\sum_{t=1}^{8760}"
                  r"\left(P^{\mathrm{L}}_{t}+P^{\mathrm{el}}_{t}+P^{\mathrm{cp}}_{t}\right)")
    mixed(doc, ["式中，", ("m", r"\gamma^{\mathrm{s}}"), "为自发自用电量占总可用发电量比例下限；",
                ("m", r"\gamma^{\mathrm{gr}}"), "为自发自用电量占总用电量比例下限。"])
    h3(doc, "8.检修约束")
    equation(doc, r"P^{\mathrm{L}}_{t}=0,\quad d_t=0,\quad q_t=0,\quad t\in\Omega^{\mathrm{m}}")
    mixed(doc, ["式中，", ("m", r"\Omega^{\mathrm{m}}"), "为全厂检修时段集合。"])

    h2(doc, "（四）求解方法")
    para(doc, "十年逐时联合优化问题规模较大，各年度运行问题仅通过设备容量相互关联，采用按年分解的Benders方法求解"
              "（图%d）。主问题决策设备容量与申报需量，子问题在给定容量下分年度优化逐时运行，并以割平面形式向主问题"
              "返回年度运行费及其对各容量的边际值，迭代至上下界间隙小于0.05%%。" % (FGN[0] + 1))
    equation(doc, r"\mathrm{min}\ \mathbf{c}^{\mathrm{T}}\mathbf{x}+\sum_{y=1}^{10}\phi_y")
    equation(doc, r"\phi_y\ge f_y\left(\mathbf{x}^{(i)}\right)+\left(\mathbf{g}^{(i)}_{y}\right)^{\mathrm{T}}"
                  r"\left(\mathbf{x}-\mathbf{x}^{(i)}\right)")
    mixed(doc, ["式中，", ("m", r"\mathbf{x}"), "为设备容量与申报需量向量；", ("m", r"\mathbf{c}"),
                "为对应的年化投资与容量电费系数；", ("m", r"\phi_y"), "为第", ("m", "y"), "年运行费；",
                ("m", r"f_y\left(\mathbf{x}^{(i)}\right)"), "、", ("m", r"\mathbf{g}^{(i)}_{y}"), "分别为第",
                ("m", "i"), "次迭代子问题求得的年度运行费及其对容量的边际值。"])
    figure(doc, os.path.join(FIG, "图2_求解流程.png"), "模型求解流程", 125)

    # 五、边界条件
    h1(doc, "五、边界条件")
    para(doc, "容量电费按《关于完善价格机制促进新能源发电就近消纳的通知》（发改价格〔2025〕1192号）规定计算，"
              "电度输配电价按全省平均负荷率折算后计入容量电费，电量电价不再计入输配电价。测算边界条件见表4至表6。")
    table(doc, "电价及电费参数", ["序号", "项目", "取值", "取值依据"],
          [["1", "节点电价", "逐时取值\n年均0.1755元/kWh", "若羌节点日前出清价格\n（2025年7月—2026年6月）"],
           ["2", "系统运行费折价", "0.046060元/kWh", "业主提供"],
           ["3", "上网环节线损电价", "0.013424元/kWh", "业主提供"],
           ["4", "政府性基金及附加", "0.006856元/kWh", "业主提供"],
           ["5", "最大需量电价", "30.4元/(kW·月)", "国网新疆电力代理购电工商业\n用户电价表（110kV两部制）"],
           ["6", "电度输配电价", "0.0815元/kWh", "同上"],
           ["7", "全省平均负荷率", "47.85%", "业主提供"],
           ["8", "容量电费合计", "58.87元/(kW·月)", "发改价格〔2025〕1192号"],
           ["9", "光伏余电上网", "不上网", "业主提供"]],
          [12, 44, 42, 58], [CENTER, LEFT, CENTER, LEFT])
    table(doc, "设备技术经济参数", ["类别", "项目", "取值", "取值依据"],
          [["光伏", "单位造价", "2.6元/Wp（直流侧）", "业主提供"],
           ["", "容配比", "1.2", "业主提供"],
           ["", "支架形式", "单轴跟踪", "业主提供"],
           ["", "年等效利用小时数", "1974h（直流侧）", "2019—2023年逐时气象数据"],
           ["", "使用寿命／年运维费率", "25年／2%", "业主提供"],
           ["电解水制氢", "单位造价", "12000元/(Nm³/h)", "业主提供造价区间"],
           ["", "额定直流电耗", "5.0kWh/Nm³", "设备概算"],
           ["", "电耗特性", "4.5467＋0.4533x", "设备厂家测试数据"],
           ["", "辅助设备功耗", "0.185kWh/Nm³", "设备电耗表"],
           ["", "整流效率", "96%", "参考同类项目"],
           ["", "调节范围", "25%～120%", "业主提供"],
           ["", "电耗年衰减率", "0.8%", "业主提供"],
           ["", "使用寿命／年运维费率", "20年／2%", "业主提供"],
           ["储氢", "储氢罐单位造价", "3000元/kg", "厂家报价折算\n（2000m³、1.6MPa、450万元）"],
           ["", "进出气速率上限", "50%罐容/h", "参考同类项目"],
           ["", "增压机单位造价", "30000元/(kg·h)", "参考同类项目"],
           ["", "增压单位电耗", "2.0kWh/kg", "参考同类项目"],
           ["", "使用寿命／年运维费率", "储氢罐25年／1%\n增压机20年／2%", "业主提供"],
           ["储能", "2小时系统造价", "0.78元/Wh", "业主提供"],
           ["", "4小时系统造价", "0.70元/Wh", "业主提供"],
           ["", "充、放电效率", "各92%", "业主提供"],
           ["", "年吞吐次数上限", "700次", "参考同类项目"],
           ["", "使用寿命／年运维费率", "15年／2%", "业主提供"]],
          [24, 44, 42, 46], [CENTER, LEFT, CENTER, LEFT], merge=True)
    table(doc, "运行及财务参数", ["序号", "项目", "取值", "取值依据"],
          [["1", "加氢装置用氢量", "%.0fNm³/h" % DH2, "可研：外购氢气5.91万t/a，\n年操作时间8000h"],
           ["2", "炼化装置负荷", "35.55MW", "可研：年耗电量28440.4万kW·h，\n年操作时间8000h"],
           ["3", "全厂检修", "每3年1次，每次30天\n（第3、6、9年4月）", "业主提供"],
           ["4", "测算年限", "10年", "业主提供"],
           ["5", "气象数据", "2019—2023年逐时数据\n各使用2次", "业主提供"],
           ["6", "自发自用电量占总可用发电量比例", "不低于60%", "发改能源〔2025〕650号"],
           ["7", "自发自用电量占总用电量比例", "不低于35%", "业主提供"],
           ["8", "折现率", "6%", "业主提供"],
           ["9", "所得税率", "25%", "业主提供"],
           ["10", "折旧年限／残值率", "15年／5%", "业主提供"]],
          [12, 52, 42, 50], [CENTER, LEFT, CENTER, LEFT])

    # 六、十年全周期电量平衡
    h1(doc, "六、十年全周期电量平衡")
    maint = [2, 5, 8]
    normal = [i for i in range(10) if i not in maint]
    para(doc, "推荐配置十年逐时仿真的年度电量平衡见表7、表8。年均光伏理论发电量%.1fGWh，其中利用%.1fGWh、弃光%.1fGWh；"
              "年均电网购电量%.1fGWh。检修年（第3、6、9年）弃光电量%.1f～%.1fGWh，其余年份不超过%.1fGWh。"
         % (E["光伏可发"].mean(), E["光伏利用"].mean(), E["弃光"].mean(), E["下网"].mean(),
            E["弃光"][maint].min(), E["弃光"][maint].max(), E["弃光"][normal].max()))
    sup = lambda i: E["光伏利用"][i] + E["下网"][i] + E["储能放电"][i]
    dem = lambda i: E["炼化"][i] + E["电解槽"][i] + E["增压"][i] + E["储能充电"][i]
    rows = [["第%d年" % (i + 1), str(WEATHER[i]), f1(E["光伏可发"][i]), f1(E["光伏利用"][i]), f1(E["弃光"][i]),
             f1(E["下网"][i]), f1(E["储能放电"][i]), f1(sup(i))] for i in range(10)]
    rows.append(["年均", "—", f1(E["光伏可发"].mean()), f1(E["光伏利用"].mean()), f1(E["弃光"].mean()),
                 f1(E["下网"].mean()), f1(E["储能放电"].mean()), f1(np.mean([sup(i) for i in range(10)]))])
    table(doc, "供电侧年度电量平衡（单位：GWh）",
          ["年份", "气象\n年份", "光伏理论\n发电量", "光伏实际\n发电量", "弃光\n电量", "电网\n购电量", "储能\n放电量", "供电量\n合计"],
          rows, [16, 16, 21, 21, 20, 21, 20, 21], None, 10.5)
    rows = [["第%d年" % (i + 1), f1(E["炼化"][i]), f1(E["电解槽"][i]), f1(E["增压"][i]), f1(E["储能充电"][i]),
             f1(dem(i)), "%.1f%%" % (green[i] * 100), "%.2f" % (h2y[i] / NPK / 1e7)] for i in range(10)]
    rows.append(["年均", f1(E["炼化"].mean()), f1(E["电解槽"].mean()), f1(E["增压"].mean()), f1(E["储能充电"].mean()),
                 f1(np.mean([dem(i) for i in range(10)])), "%.1f%%" % (100 * (1 - E["下网"].sum() / load.sum())),
                 "%.2f" % (h2y.mean() / NPK / 1e7)])
    table(doc, "用电侧年度电量平衡（单位：GWh）",
          ["年份", "炼化装置\n用电量", "电解槽\n用电量", "增压机\n用电量", "储能\n充电量", "用电量\n合计", "绿电\n占比",
           "制氢量\n（万t）"],
          rows, [16, 21, 21, 20, 20, 21, 18, 19], None, 10.5)

    # 七、敏感性分析
    h1(doc, "七、敏感性分析")
    h2(doc, "（一）储能造价上浮")
    ups = sorted(sens)
    para(doc, "储能系统造价按业主提供单价取值：2小时系统0.78元/Wh、4小时系统0.70元/Wh，按额定容量计价，功率部分不单独"
              "计价；使用寿命15年，期内不更换电池，年运维费率2%%，充、放电效率均为92%%。敏感性分析以上述造价为基准，"
              "两类系统造价同比例上浮%s，其余边界条件不变，各造价水平下重新优化全部设备容量与逐时运行，并以不配置储能"
              "方案作为对照，结果见表%d。"
         % ("、".join("%.0f%%" % ((k - 1) * 100) for k in ups) if ups else "", TBN[0] + 1))

    def srow(lab, k, res, show_price=True):
        c = res["cap"]
        e, pw = es_of(res)
        tot = total(res)
        return [lab, "%.3f" % (0.78 * k) if show_price else "—", "%.3f" % (0.70 * k) if show_price else "—",
                ("%.1f/%.1f" % (e, pw)) if e > 0.5 else "0", "%.1f" % c["光伏MWp"], "%.2f" % c["申报需量MW"],
                f1(tot), "%.1f" % (tot - TOT0) if show_price else "—"]

    rows = [srow("1.00（基准）", 1.0, cap)] + [srow("%.2f" % k, k, sens[k]) for k in ups]
    rows.append(srow("不配置储能", 1.0, nob, show_price=False))
    table(doc, "储能造价上浮敏感性分析结果（理论最优配置）",
          ["造价倍数", "2小时系统\n元/Wh", "4小时系统\n元/Wh", "储能配置\nMWh/MW", "光伏\nMWp",
           "申报需量\nMW", "年均总成本\n万元", "与不配置储能\n相比 万元/年"],
          rows, [18, 17, 17, 23, 17, 18, 21, 25], None, 10)
    e0, _ = es_of(cap)
    txt = "基准造价下配置储能%.1fMWh，年均总成本较不配置储能降低%.1f万元。" % (e0, TOT0 - TOT)
    pos = [k for k in ups if es_of(sens[k])[0] > 0.5]
    zero = [k for k in ups if es_of(sens[k])[0] <= 0.5]
    for k in pos:
        txt += "造价上浮%.0f%%时，配置储能%.1fMWh，年均总成本较不配置储能降低%.1f万元。" % (
            (k - 1) * 100, es_of(sens[k])[0], TOT0 - total(sens[k]))
    if zero:
        txt += "造价上浮%.0f%%及以上时，优化结果不配置储能。" % ((min(zero) - 1) * 100)
    if ups:
        para(doc, txt)

    if cgf is not None:
        h2(doc, "（二）申报需量不可削减")
        cg0 = nob["cap"]["申报需量MW"]
        para(doc, "申报需量按不配置储能方案取%.2fMW，不随储能及其他设备配置调整，其余边界条件与基准一致，重新优化其余"
                  "设备容量与逐时运行，结果见表%d。" % (cg0, TBN[0] + 1))
        rows = []
        for lab, res in (("申报需量可削减（基准）", cap), ("申报需量不可削减", cgf), ("不配置储能", nob)):
            c = res["cap"]
            e, pw = es_of(res)
            tot = total(res)
            rows.append([lab, ("%.1f/%.1f" % (e, pw)) if e > 0.5 else "0", "%.1f" % c["光伏MWp"],
                         "%.0f" % c["电解槽Nm3h"], "%.2f" % c["申报需量MW"], f1(tot),
                         "%.1f" % (tot - TOT0) if res is not nob else "—"])
        table(doc, "申报需量不可削减情形测算结果（理论最优配置）",
              ["情形", "储能配置\nMWh/MW", "光伏\nMWp", "电解槽\nNm³/h", "申报需量\nMW",
               "年均总成本\n万元", "与不配置储能\n相比 万元/年"],
              rows, [32, 22, 17, 20, 18, 22, 25], [LEFT, CENTER, CENTER, CENTER, CENTER, CENTER, CENTER], 10)
        ec, _ = es_of(cgf)
        if ec <= 0.5:
            para(doc, "申报需量不可削减时，优化结果不配置储能，年均总成本与不配置储能方案相同。")
        else:
            para(doc, "申报需量不可削减时，配置储能%.1fMWh，年均总成本较不配置储能降低%.1f万元，较申报需量可削减情形"
                      "增加%.1f万元。" % (ec, TOT0 - total(cgf), total(cgf) - TOT))

    spacer(doc, 16)
    para(doc, "附件：轮台绿电制氢项目十年逐时调度底稿")

    doc.save(DST)
    print("已存", DST)
    print("公式 %d 个，表 %d 张，图 %d 张；推荐配置年均总成本 %.1f 万元（理论最优 %.1f）" % (EQN[0], TBN[0], FGN[0], TOTR, TOT))


if __name__ == "__main__":
    main()
