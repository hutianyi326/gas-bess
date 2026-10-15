# -*- coding: utf-8 -*-
"""报告示意图：图1 系统构成与能量流向，图2 模型求解流程。

输出：报告/插图/图1_系统构成.png、图2_求解流程.png
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Polygon

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import FIG                                   # noqa: E402
os.makedirs(FIG, exist_ok=True)
plt.rcParams["font.sans-serif"] = ["SimHei"]     # sans-serif 列表只取第一个可用字体，不做逐字回落
plt.rcParams["axes.unicode_minus"] = False

INK = "#1F2A36"
ELEC = "#1F4E79"
H2 = "#2E7D5B"
FILL = "#EEF3F8"
FILL_H = "#EAF4EE"


def box(ax, cx, cy, w, h, text, fill=FILL, edge=INK, size=11, bold=False):
    ax.add_patch(FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                                boxstyle="round,pad=0.25,rounding_size=0.8",
                                fc=fill, ec=edge, lw=1.1))
    ax.text(cx, cy, text, ha="center", va="center", fontsize=size, color=INK,
            fontweight="bold" if bold else "normal", linespacing=1.5)


def arrow(ax, x0, y0, x1, y1, color, both=False, lw=1.6, ls="-"):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="<|-|>" if both else "-|>", color=color, lw=lw,
                                linestyle=ls, shrinkA=0, shrinkB=0, mutation_scale=13))


def fig1():
    fig, ax = plt.subplots(figsize=(7.8, 4.4), dpi=300)       # 版心宽 150 mm 时字号约 8 磅
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(0, 96); ax.set_ylim(0, 56); ax.axis("off")
    # 母线
    ax.add_patch(FancyBboxPatch((33.6, 4.5), 1.8, 46.5, boxstyle="square,pad=0", fc=INK, ec=INK))
    ax.text(34.5, 53.2, "交流母线（并网点）", ha="center", va="center", fontsize=11, color=INK)
    # 电源侧
    box(ax, 14, 40, 16, 8, "光伏电站")
    box(ax, 14, 18, 16, 8, "公共电网")
    arrow(ax, 22.4, 40, 33.6, 40, ELEC)
    arrow(ax, 22.4, 18, 33.6, 18, ELEC)
    ax.text(28, 41.4, "光伏利用", ha="center", fontsize=9.5, color=ELEC)
    ax.text(28, 19.4, "下网", ha="center", fontsize=9.5, color=ELEC)
    ax.text(27.6, 15.2, "≤申报需量", ha="center", fontsize=8.5, color=ELEC)
    arrow(ax, 14, 44.3, 14, 50.5, ELEC, ls="--", lw=1.2)
    ax.text(15.2, 49.5, "弃光", ha="left", fontsize=9.5, color=ELEC)
    # 负荷侧（电）
    box(ax, 52, 46, 16, 8, "电化学储能")
    box(ax, 52, 34, 16, 8, "炼化装置负荷")
    box(ax, 52, 22, 16, 8, "电解槽", fill=FILL_H)
    box(ax, 52, 9, 16, 8, "增压机", fill=FILL_H)
    arrow(ax, 35.4, 46, 43.6, 46, ELEC, both=True)
    ax.text(39.5, 47.4, "充/放电", ha="center", fontsize=9, color=ELEC)
    for yy in (34, 22, 9):
        arrow(ax, 35.4, yy, 43.6, yy, ELEC)
    # 氢气侧
    box(ax, 82, 22, 16, 8, "下游用氢", fill=FILL_H)
    box(ax, 82, 9, 16, 8, "储氢罐", fill=FILL_H)
    arrow(ax, 60.4, 22, 73.6, 22, H2)
    ax.text(67, 23.4, "直供", ha="center", fontsize=9.5, color=H2)
    arrow(ax, 52, 17.7, 52, 13.3, H2)
    arrow(ax, 60.4, 9, 73.6, 9, H2)
    ax.text(67, 10.4, "储氢", ha="center", fontsize=9.5, color=H2)
    arrow(ax, 82, 13.3, 82, 17.7, H2)
    ax.text(83.2, 15.5, "释氢", ha="left", va="center", fontsize=9.5, color=H2)
    # 图例
    ax.plot([66, 71], [49, 49], color=ELEC, lw=1.8); ax.text(72, 49, "电力流", va="center", fontsize=10)
    ax.plot([66, 71], [44.5, 44.5], color=H2, lw=1.8); ax.text(72, 44.5, "氢气流", va="center", fontsize=10)
    fig.savefig(os.path.join(FIG, "图1_系统构成.png"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_process():
    """系统构成及工艺流程：发电—并网—整流—电解制氢—分离纯化—直供/储氢—加氢装置用氢。"""
    fig, ax = plt.subplots(figsize=(8.6, 4.6), dpi=300)
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(0, 124); ax.set_ylim(0, 64); ax.axis("off")
    GREY = "#5A6570"
    ax.add_patch(FancyBboxPatch((23.7, 5.0), 1.6, 52.0, boxstyle="square,pad=0", fc=INK, ec=INK))
    ax.text(24.5, 60.2, "110kV并网点（交流母线）", ha="center", va="center", fontsize=10.5, color=INK)
    # 电源侧
    box(ax, 10, 50, 15, 7, "光伏电站", size=11)
    box(ax, 10, 34, 15, 7, "公共电网", size=11)
    box(ax, 10, 18, 15, 7, "电化学储能", size=11)
    arrow(ax, 17.9, 50, 23.7, 50, ELEC)
    arrow(ax, 17.9, 34, 23.7, 34, ELEC)
    arrow(ax, 17.9, 18, 23.7, 18, ELEC, both=True)
    ax.text(20.8, 51.4, "发电", ha="center", fontsize=8.5, color=ELEC)
    ax.text(20.8, 35.4, "购电", ha="center", fontsize=8.5, color=ELEC)
    ax.text(20.8, 19.4, "充放电", ha="center", fontsize=8.5, color=ELEC)
    # 用电侧
    box(ax, 37.1, 50, 12, 7, "炼化装置\n用电负荷", size=10)
    box(ax, 37.1, 34, 12, 7, "整流变压器\n及整流柜", size=10)
    arrow(ax, 25.3, 50, 30.9, 50, ELEC)
    arrow(ax, 25.3, 34, 30.9, 34, ELEC)
    # 制氢工艺链
    box(ax, 54.9, 34, 12, 7, "碱性电解槽", fill=FILL_H, size=10)
    box(ax, 72.7, 34, 12, 7, "气液分离", fill=FILL_H, size=10)
    box(ax, 90.5, 34, 12, 7, "氢气纯化", fill=FILL_H, size=10)
    box(ax, 110.8, 34, 17, 7, "浆态床加氢\n解构改质装置", fill=FILL_H, size=10)
    arrow(ax, 43.3, 34, 48.7, 34, ELEC)
    arrow(ax, 61.1, 34, 66.5, 34, H2)
    arrow(ax, 78.9, 34, 84.3, 34, H2)
    arrow(ax, 96.7, 34, 102.1, 34, H2)
    ax.text(99.4, 35.4, "直供", ha="center", fontsize=9, color=H2)
    ax.text(54.9, 39.4, "1.6MPa、90℃", ha="center", fontsize=8.5, color=GREY)
    ax.text(90.5, 39.4, "1.4MPa、99.999%", ha="center", fontsize=8.5, color=GREY)
    ax.text(110.8, 41.6, "替代外购氢气5.91万t/a", ha="center", fontsize=8.5, color=GREY)
    # 储氢支路
    box(ax, 90.5, 16, 12, 7, "增压机", fill=FILL_H, size=10)
    box(ax, 110.8, 16, 17, 7, "储氢罐", fill=FILL_H, size=10)
    arrow(ax, 90.5, 30.3, 90.5, 19.7, H2)
    ax.text(89.1, 25, "富余氢气", ha="right", va="center", fontsize=9, color=H2)
    arrow(ax, 96.7, 16, 102.1, 16, H2)
    arrow(ax, 110.8, 19.7, 110.8, 30.3, H2)
    ax.text(112.2, 25, "补充供氢", ha="left", va="center", fontsize=9, color=H2)
    ax.text(110.8, 10.8, "1.6MPa", ha="center", fontsize=8.5, color=GREY)
    # 增压机用电
    ax.plot([25.3, 90.5, 90.5], [8.0, 8.0, 11.0], color=ELEC, lw=1.2, ls="--")
    ax.annotate("", xy=(90.5, 12.3), xytext=(90.5, 10.6),
                arrowprops=dict(arrowstyle="-|>", color=ELEC, lw=1.2, mutation_scale=11))
    ax.text(57, 9.3, "增压机用电", ha="center", fontsize=8.5, color=ELEC)
    # 图例
    ax.plot([96, 101], [58.5, 58.5], color=ELEC, lw=1.8)
    ax.text(102, 58.5, "电力流", va="center", fontsize=9.5)
    ax.plot([96, 101], [54, 54], color=H2, lw=1.8)
    ax.text(102, 54, "氢气流", va="center", fontsize=9.5)
    fig.savefig(os.path.join(FIG, "图1_系统构成.png"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig2():
    fig, ax = plt.subplots(figsize=(6.6, 6.2), dpi=300)       # 插图宽 125 mm 时字号约 8 磅
    ax.set_position([0, 0, 1, 1])
    ax.set_xlim(0, 100); ax.set_ylim(0, 92); ax.axis("off")
    CX, W = 56, 80                                   # 框中心与宽度，左侧留出回路位置
    box(ax, CX, 85, W, 9, "输入数据\n光伏逐时出力、节点电价、炼化装置用电负荷、用氢需求、设备技术经济参数", size=10.5)
    box(ax, CX, 67, W, 12, "主问题：容量决策\n光伏、储能、电解槽、储氢罐、增压机、申报需量\n"
                          "目标：投资年化＋需量电费＋各年运行费下界最小", size=10.5)
    box(ax, CX, 46, W, 12, "子问题：年度逐时运行优化（10个年度并行求解）\n"
                          "给定容量，优化光伏出力、电网购电、储能充放电、制氢与储氢\n"
                          "输出：各年运行费及其对各容量的边际值", size=10.5)
    cx, cy, hw, hh = CX, 27.5, 19, 5.6               # 判断框
    ax.add_patch(Polygon([[cx - hw, cy], [cx, cy + hh], [cx + hw, cy], [cx, cy - hh]], closed=True,
                         fc="white", ec=INK, lw=1.1))
    ax.text(cx, cy, "上下界间隙≤收敛阈值", ha="center", va="center", fontsize=10.5, color=INK)
    box(ax, CX, 9, W, 9, "输出结果\n十年最优容量、逐时调度、年度电量平衡、成本构成", size=10.5)
    arrow(ax, CX, 80.2, CX, 73.3, INK, lw=1.3)
    arrow(ax, CX, 60.7, CX, 52.3, INK, lw=1.3)
    ax.text(CX + 2, 56.5, "容量方案", fontsize=10, va="center")
    arrow(ax, CX, 39.7, CX, 33.3, INK, lw=1.3)
    arrow(ax, CX, 21.7, CX, 13.8, INK, lw=1.3)
    ax.text(CX + 2, 17.8, "是", fontsize=10, va="center")
    # 否：沿左侧回到主问题
    ax.plot([cx - hw, 7, 7], [cy, cy, 67], color=INK, lw=1.3)
    arrow(ax, 7, 67, 15.6, 67, INK, lw=1.3)
    ax.text(cx - hw - 2, cy + 1.6, "否", fontsize=10, ha="right", va="bottom")
    ax.text(5.2, 47, "生成割平面，更新主问题", fontsize=10, ha="center", va="center", rotation=90)
    fig.savefig(os.path.join(FIG, "图2_求解流程.png"), bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    fig_process()
    fig2()
    print("已输出 图1_系统构成.png、图2_求解流程.png →", FIG)
