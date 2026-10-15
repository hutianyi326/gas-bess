# -*- coding: utf-8 -*-
"""读取十年逐时调度并做报告口径整理。

线性规划在"有弃光的小时"存在零成本多解：电解槽用电可以高于比电耗曲线、增压机可以空转、
储能可以同一小时既充又放，三者都只是把本该弃掉的光伏电量耗掉，不影响目标值与任何容量。
报告与底稿按物理口径还原：
  1. 电解槽用电取比电耗曲线（切线族包络）给出的最小值；
  2. 增压机用电只计实际进罐氢气 max(产氢−用氢, 0)；
  3. 储能同一小时既充又放时，按存储电量变化量还原为单向充电或放电（存储电量轨迹不变）。
被空耗的电量改记为弃光。整理后逐时功率平衡仍严格成立。
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CACHE, project_cache_file             # noqa: E402
NPK, ETA, RECT = 11.1259, 0.92, 0.96
A0, B0, AUX, DEG = 4.5467, 0.4533, 0.1776, 0.008
LO, HI, TSTEP = 0.25, 1.20, 0.10
PK = 2.0 / NPK                                    # 增压电耗 kWh/Nm³


def envelope(q, Q, yi):
    """与模型同一组切线（含低负荷割线）给出的电解槽最小交流用电，kW。"""
    t = np.arange(q.shape[-1])
    a = A0 * (1 + DEG) ** ((t + yi * 8760) / 8760.0) + AUX
    b = B0 * (1 + DEG) ** ((t + yi * 8760) / 8760.0)
    p = (a + LO * b) * q                          # 低负荷割线
    u = LO
    while u <= HI + 1e-9:
        p = np.maximum(p, (a + 2 * b * u) * q - b * u * u * Q)
        u += TSTEP
    return np.maximum(p, 0.0) / RECT


def default_path():
    """报告默认读推荐配置的调度；DISPATCH_FILE 可指定其他文件。"""
    if os.environ.get("DISPATCH_FILE"):
        return project_cache_file(os.environ["DISPATCH_FILE"])
    rec = os.path.join(CACHE, "_dispatch_10y_trk_推荐.npz")
    return rec if os.path.exists(rec) else os.path.join(CACHE, "_dispatch_10y_trk.npz")


def load(path=None, report=False):
    z = np.load(project_cache_file(path or default_path()))
    meta = {k: z[k] for k in ("x", "f", "epsy", "inv") if k in z.files}
    d = {k: z[k].astype(float) for k in z.files if k not in meta}
    x = z["x"]
    Q = float(x[2])
    burn = np.zeros_like(d["电解槽"])
    # 1. 电解槽
    pel_min = np.vstack([envelope(d["产氢"][yi], Q, yi) for yi in range(d["产氢"].shape[0])])
    ex_el = np.maximum(d["电解槽"] - pel_min, 0.0)
    d["电解槽"] = d["电解槽"] - ex_el
    burn += ex_el
    # 2. 增压机
    fin_eff = np.minimum(d["进罐"], np.maximum(d["产氢"] - d["取氢"], 0.0))
    ex_cp = (d["进罐"] - fin_eff) * PK
    d["进罐"], d["增压"] = fin_eff, fin_eff * PK
    burn += ex_cp
    # 3. 储能同时充放
    inj, rel = d["储能充电"], d["储能放电"]
    delta = ETA * inj - rel / ETA
    both = (inj > 1e-6) & (rel > 1e-6)
    inj2 = np.where(both, np.where(delta >= 0, delta / ETA, 0.0), inj)
    rel2 = np.where(both, np.where(delta < 0, -delta * ETA, 0.0), rel)
    ex_es = (inj - inj2) - (rel - rel2)
    d["储能充电"], d["储能放电"] = inj2, rel2
    burn += ex_es
    # 空耗电量依次从光伏利用（改记弃光）、储能放电中扣回，仍不够才从下网扣；
    # 调度复算已加极小成本（EPSREG）消除多解，正常情况下三项均应接近 0
    from_pv = np.minimum(burn, d["光伏利用"])
    rest = burn - from_pv
    from_dis = np.minimum(rest, d["储能放电"])
    from_grid = rest - from_dis
    d["光伏利用"] -= from_pv
    d["弃光"] += from_pv
    d["储能放电"] -= from_dis
    d["下网"] -= from_grid
    bal = d["光伏利用"] + d["下网"] + d["储能放电"] - d["电解槽"] - d["增压"] - d["储能充电"] - d["炼化"]
    if report:
        print("【调度整理】电解槽超出曲线 %.3f GWh｜增压机空转 %.3f GWh｜储能同时充放 %.3f GWh"
              "｜改记弃光 %.3f GWh｜扣储能放电 %.3f GWh｜扣下网 %.6f GWh｜同时充放小时 %d"
              % (ex_el.sum() / 1e6, ex_cp.sum() / 1e6, ex_es.sum() / 1e6, from_pv.sum() / 1e6,
                 from_dis.sum() / 1e6, from_grid.sum() / 1e6, int(both.sum())))
        print("【调度整理】功率平衡最大残差 %.2e kW；弃光电量 整理前 %.2f GWh → 整理后 %.2f GWh（十年合计）"
              % (np.abs(bal).max(), z["弃光"].sum() / 1e6, d["弃光"].sum() / 1e6))
    d.update(meta)
    return d


if __name__ == "__main__":
    load(report=True)
