# -*- coding: utf-8 -*-
"""数据与结果目录。"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "原始数据")
NINJA = os.path.join(RAW, "renewables.ninja")
INP = os.path.join(ROOT, "处理后输入")
CACHE = os.path.join(ROOT, "优化缓存", "储+氢", "分时电价_2026_年用氢3万吨_无绿电约束_v2")
if os.environ.get("SIZING_CASE") == "1y":
    CACHE = os.path.join(CACHE, "单年筛选")
_equip_cost_x = float(os.environ.get("EQUIP_COST_X", "1.0"))
if _equip_cost_x != 1.0:
    CACHE = os.path.join(CACHE, "非储能设备造价倍率_%g" % _equip_cost_x)
DOC = os.path.join(ROOT, "报告")
FIG = os.path.join(DOC, "插图")
for _d in (INP, CACHE, DOC, FIG):
    os.makedirs(_d, exist_ok=True)


def project_cache_file(path):
    """显式指定的结果与割平面也必须来自本项目缓存目录。"""
    resolved = os.path.normcase(os.path.realpath(path))
    root = os.path.normcase(os.path.realpath(CACHE))
    if os.path.commonpath([root, resolved]) != root:
        raise ValueError("储+氢项目仅允许读取本项目缓存目录内的结果或割平面：%s" % CACHE)
    return resolved
