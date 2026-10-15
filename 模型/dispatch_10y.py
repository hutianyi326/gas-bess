# -*- coding: utf-8 -*-
"""十年逐时调度复算。

在给定容量上逐年求解单年运行问题，导出 10×8760 逐时调度，供报告作图与调度底稿使用。
各年输入与 bd10y 的子问题完全一致（天气年、检修、能耗衰减接续、贴现权重）。

容量默认取十年联合最优（优化缓存/_bd10y_trk_容量.npy）；
XREC="光伏kWp,申报需量kW,电解槽Nm3/h,罐Nm3,增压机Nm3/h,2h储能kWh,4h储能kWh" 指定其他容量（如推荐配置），
TAGOUT 为输出文件名后缀。EPSREG=1e-5 给弃光小时的零成本多解加极小成本（对年运行费的影响单独扣回）。

输出：优化缓存/_dispatch_10y_trk<TAGOUT>.npz（含逐时调度、容量 x、各年运行费 f 与极小成本项 epsy）
用法：EPSREG=1e-5 python dispatch_10y.py
"""
import multiprocessing as mp
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CACHE                                 # noqa: E402
OFFYRS, Y = [2, 5, 8], 10
KEYS = ["光伏可发", "光伏利用", "弃光", "下网", "储能充电", "储能放电", "储能电量",
        "电解槽", "产氢", "罐料位", "进罐", "增压", "炼化", "取氢", "电价", "检修", "超需量"]


def solve_year(args):
    yi, x, env = args
    os.environ.update(env)
    sys.path.insert(0, HERE)
    import highspy
    import lp_multi as M
    import bd10y as BD
    M.P["pvmode"] = env["PVMODE"]
    inp = BD.year_inputs(M, yi, OFFYRS, Y)
    lp, meta = M.build_lp(*inp, 1, [M.BATT_2H, M.BATT_4H], fixcap=BD.fixvals(x),
                          sub=True, log=False)
    h = highspy.Highs()
    M.solver_options(h, crossover="on", solver="ipm", log=False)
    h.passModel(lp)
    t0 = time.time()
    h.run()
    st = h.modelStatusToString(h.getModelStatus())
    xv = np.array(h.getSolution().col_value)
    f = h.getInfo().objective_function_value
    B, I, T = meta["B"], meta["I"], 8760

    def v(k):
        return xv[B[k]:B[k] + T].copy()

    alpha, gp, D_u, dh2, off, m_t, w = inp
    pk = M.P["pump_kwh_kg"] / M.P["npk"]
    d = {"光伏可发": alpha * x[0], "光伏利用": v("Xs"), "弃光": v("cur"), "下网": v("Xg"),
         "储能充电": v("inj_2h") + v("inj_4h"), "储能放电": v("rel_2h") + v("rel_4h"),
         "储能电量": v("S_2h") + v("S_4h"), "电解槽": v("Pel"), "产氢": v("q"),
         "罐料位": v("Sh2"), "进罐": v("fin"), "增压": v("fin") * pk, "炼化": D_u,
         "取氢": dh2, "电价": gp, "检修": off.astype(float), "超需量": v("ov")}
    return yi, st, f, float(xv[I["sg0"]]), float(xv[I["sa0"]]), d, time.time() - t0


def capex10(x):
    """十年合计投资年化与需量容量费（元），与 bd10y 主问题同一口径。"""
    sys.path.insert(0, HERE)
    import lp_multi as M
    L, P = M.P["life"], M.P
    capf = P["td"] * P["lf"] * P["hours_month"]
    c = np.array([Y * P["pv_capex"] * M.am(L["pv"]), Y * 12 * (P["cap_old"] + capf),
                  Y * P["el_capex"] * M.am(L["el"]), Y * P["tank"] / P["npk"] * M.am(L["tank"]),
                  Y * P["pump"] / P["npk"] * M.am(L["pump"]),
                  Y * M.BATT_2H["cost_e"] * M.am(L["batt"]), Y * M.BATT_4H["cost_e"] * M.am(L["batt"])])
    return c * np.asarray(x, dtype=float)


def main():
    cap = np.load(os.path.join(CACHE, "_bd10y_trk_容量.npy"), allow_pickle=True).item()
    x = cap["x"]
    rec = bool(os.environ.get("XREC"))
    if rec:
        x = np.array([float(v) for v in os.environ["XREC"].split(",")])
    tagout = os.environ.get("TAGOUT", "")
    eps = float(os.environ.get("EPSREG", 0.0))
    env = {"PVMODE": "trk", "GP": "real", "PCS": "1", "EPSREG": str(eps)}
    os.environ.update(env)
    ctx = mp.get_context("spawn")
    with ctx.Pool(int(os.environ.get("WORKERS", 5))) as pool:
        res = pool.map(solve_year, [(yi, x, env) for yi in range(Y)])
    res.sort(key=lambda r: r[0])
    arr = {k: np.vstack([r[5][k] for r in res]) for k in KEYS}
    pk = 2.0 / 11.1259
    f = np.array([r[2] for r in res])
    epsy = np.array([eps * (arr["电解槽"][i].sum() + arr["储能充电"][i].sum() + arr["储能放电"][i].sum()
                            + pk * arr["进罐"][i].sum()) for i in range(Y)])
    ref = pd.read_csv(os.path.join(CACHE, "_bd10y_trk_逐年.csv"), index_col=0)
    print("年  状态     用时  子问题目标(元)   超需量kWh  绿电缺口  弃光超限  下网GWh%s  功率平衡残差kW"
          % ("" if rec else "(复算/分解)"))
    for r in res:
        yi, st, fy, sg, sa, d, sec = r
        bal = d["光伏利用"] + d["下网"] + d["储能放电"] - d["电解槽"] - d["增压"] - d["储能充电"] - d["炼化"]
        print("%2d  %-8s %4.0fs  %14.0f  %9.3f  %8.3f  %8.3f  %8.2f%s  %.2e"
              % (yi + 1, st, sec, fy, d["超需量"].sum(), sg, sa, d["下网"].sum() / 1e6,
                 "" if rec else " / %8.2f" % ref.loc[yi + 1, "下网GWh"], np.abs(bal).max()))
    inv = capex10(x)
    tot = inv.sum() + (f - epsy).sum()
    print("\n容量 x = %s" % np.round(x, 1).tolist())
    print("十年运行费（扣除极小成本项 %.1f 万元）%.1f 万元；十年投资与需量费 %.1f 万元；年均总成本 %.1f 万元"
          % (epsy.sum() / 1e4, (f - epsy).sum() / 1e4, inv.sum() / 1e4, tot / 1e5))
    dst = os.path.join(CACHE, "_dispatch_10y_trk%s.npz" % tagout)
    np.savez_compressed(dst, x=x, f=f, epsy=epsy, inv=inv, **arr)
    print("已存", os.path.basename(dst))


if __name__ == "__main__":
    main()
