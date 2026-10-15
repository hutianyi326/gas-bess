# -*- coding: utf-8 -*-
"""轮台绿电制氢 · 10 年全周期联合定容（按年分解）

为什么分解：10 年逐时联合 LP（271 万行）用 HiGHS 内点法解不动——"多年 + 检修"会停滞，
不含检修时求解时间也随年数超线性增长（1 年 101 s、2 年 469 s）。10 年共用的只有 7 个容量，
各年之间只通过这 7 个数耦合，正适合按年分解（Benders）：
  主问题：7 个容量 + 投资与需量费 + 各年运行费的割平面下界（很小的 LP）
  子问题：每年一个单年逐时模型（lp_multi.build_lp，已与框架单年算例校核一致），容量固定，
          只算当年电费，并给出当年电费对每个容量的边际（对偶值）
  主问题按割平面修正容量，信赖域防振荡，反复迭代。
  上界 = 已评估方案中总成本最低者；下界 = 去掉信赖域的主问题最优值；两者之差为最优性间隙。

与 10 年联合 LP 的差别：储能与罐子料位按年首尾闭环（跨年不结转），电池循环寿命按年约束。

用法：python bd10y.py [年数，默认 10] [检修年，默认 3,6,9；传 none 不设检修]
环境变量：PVMODE=fix|trk，WORKERS=并行进程数
"""
import multiprocessing as mp
import os
import sys
import time

import highspy
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CACHE, project_cache_file             # noqa: E402
YEARS_ALL = [2019, 2020, 2021, 2022, 2023] * 2          # 业主定：近 5 年重复两遍
CAPS = ["Cs", "Cg", "Qel", "Hc", "Pmp", "E_2h", "E_4h"]  # 主问题变量；P = E / 时长
DUR = {"E_2h": 2.0, "E_4h": 4.0}
INF = highspy.kHighsInf


def year_inputs(M, yi, offyrs, Y):
    y = YEARS_ALL[yi]
    alpha, gp = M.pv_year(y), M.nodal_year()
    t = np.arange(8760)
    apr = (t >= M.HPM[:3].sum()) & (t < M.HPM[:4].sum())               # 4 月整月 = 30 天
    off = apr if yi in offyrs else np.zeros(8760, dtype=bool)
    D_u = np.where(off, 0.0, M.P["refinery"])
    dh2 = M.hydrogen_demand(off)
    m_t = (1 + M.P["deg"]) ** ((t + yi * 8760) / 8760.0)               # 能耗逐时衰减（接续前几年）
    disc = (1 + M.P["rate"]) ** -(np.arange(Y) + 1.0)
    w = disc / disc.mean()                                              # 贴现权重，10 年均值 1
    return alpha, gp, D_u, dh2, off, m_t, np.full(8760, w[yi])


def fixvals(x):
    """主问题变量 → 子问题固定值（储能功率 = 能量 / 时长）。"""
    d = {k: float(x[i]) for i, k in enumerate(CAPS)}
    d["P_2h"] = d["E_2h"] / DUR["E_2h"]
    d["P_4h"] = d["E_4h"] / DUR["E_4h"]
    return d


class Sub:
    """单年子问题：常驻内存，容量变了就改界、沿用上次的基热启动。"""

    def __init__(self, M, yi, offyrs, Y, x0):
        self.M, self.yi = M, yi
        self.inp = year_inputs(M, yi, offyrs, Y)
        self.w = float(self.inp[6][0])
        lp, meta = M.build_lp(*self.inp, 1, [M.BATT_2H, M.BATT_4H], fixcap=fixvals(x0),
                              sub=True, log=False)
        self.meta = meta
        self.idx = {k: meta["I"][k] for k in CAPS + ["P_2h", "P_4h"]}
        self.h = highspy.Highs()
        M.solver_options(self.h, crossover="on", solver="ipm", log=False)
        self.h.passModel(lp)
        self.warm = False

    def solve(self, x):
        fx = fixvals(x)
        ids = np.array([self.idx[k] for k in fx], dtype=np.int32)
        v = np.array([fx[k] for k in fx])
        self.h.changeColsBounds(len(ids), ids, v, v)
        t0 = time.time()
        if self.warm:                                   # 热启动：沿用上次的基
            self.h.setOptionValue("solver", "simplex")
            self.h.setOptionValue("presolve", "off")
        self.h.run()
        st = self.h.modelStatusToString(self.h.getModelStatus())
        if st != "Optimal":                             # 热启动失败则冷启动
            self.h.clearSolver()
            self.h.setOptionValue("solver", "ipm")
            self.h.setOptionValue("presolve", "on")
            self.h.run()
            st = self.h.modelStatusToString(self.h.getModelStatus())
        if st != "Optimal":
            raise RuntimeError("第 %d 年子问题未达到最优：%s，不能生成割平面" % (self.yi + 1, st))
        self.warm = True
        sol = self.h.getSolution()
        d = np.array(sol.col_dual)
        xv = np.array(sol.col_value)
        f = self.h.getInfo().objective_function_value
        g = np.array([d[self.idx["Cs"]], d[self.idx["Cg"]], d[self.idx["Qel"]],
                      d[self.idx["Hc"]], d[self.idx["Pmp"]],
                      d[self.idx["E_2h"]] + d[self.idx["P_2h"]] / DUR["E_2h"],
                      d[self.idx["E_4h"]] + d[self.idx["P_4h"]] / DUR["E_4h"]])
        return dict(yi=self.yi, f=f, g=g, status=st, sec=time.time() - t0,
                    stats=self.stats(xv))

    def stats(self, xv):
        """当年运行画像（电费为不贴现原值）。"""
        M, B, I = self.M, self.meta["B"], self.meta["I"]
        alpha, gp, D_u, dh2, off, m_t, w = self.inp
        T = 8760

        def v(k):
            return xv[B[k]:B[k] + T]
        xg, pv, cur, pel = v("Xg"), v("Xs"), v("cur"), v("Pel")
        rel = v("rel_2h") + v("rel_4h")
        nt = alpha <= 0
        fee = 0.0 if M.P["gpmode"] == "real" else sum(M.FEE.values())
        xn = xg[nt]
        load = D_u + pel
        return dict(
            下网GWh=xg.sum() / 1e6, 下网电费万元=float((xg * gp).sum()) / 1e4,
            夜间下网GWh=xn.sum() / 1e6,
            夜间节点价=float((xn * (gp[nt] - fee)).sum() / max(xn.sum(), 1)),
            夜间电量价合计=float((xn * gp[nt]).sum() / max(xn.sum(), 1)),
            当年下网峰MW=xg.max() / 1e3, 光伏发电GWh=pv.sum() / 1e6, 弃光GWh=cur.sum() / 1e6,
            产氢亿Nm3=v("q").sum() / 1e8, 电解槽GWh=pel.sum() / 1e6,
            绿电占比pct=((1 - xg.sum() / load.sum()) * 100 if M.P["green"] > 0 else np.nan), 储能放电GWh=rel.sum() / 1e6,
            超需量GWh=v("ov").sum() / 1e6, 绿电缺口GWh=(float(xv[I["sg0"]]) / 1e6 if "sg0" in I else 0.0),
            弃光超限GWh=float(xv[I["sa0"]]) / 1e6)


def worker(conn, yis, offyrs, Y, x0, env):
    os.environ.update(env)
    sys.path.insert(0, HERE)
    import lp_multi as M
    subs = [Sub(M, yi, offyrs, Y, x0) for yi in yis]
    conn.send("ready")
    while True:
        x = conn.recv()
        if x is None:
            break
        conn.send([s.solve(x) for s in subs])


def master(cuts, capex, lo, hi, Y, box=None):
    """min capex·x + Σ η_y  s.t. η_y ≥ f + g·(x − x^k)；box=(下界, 上界) 为信赖域。

    超需量罚项点上的割平面（年运行费高于该年中位数 PENCUT 倍）系数极大，会让主问题数值失真、
    出现下界高于上界；这类割平面仍然有效，但不参与主问题（下界只会更弱、仍然有效）。
    """
    pc = float(os.environ.get("PENCUT", 1.25))
    if cuts and pc > 0:
        med = {}
        for yi in range(Y):
            fs = [c[2] for c in cuts if c[0] == yi]
            med[yi] = float(np.median(fs)) if fs else INF
        cuts = [c for c in cuts if c[2] <= pc * med[c[0]]]
    n = len(CAPS)
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    blo, bhi = (lo, hi) if box is None else (np.maximum(lo, box[0]), np.minimum(hi, box[1]))
    h.addVars(n + Y, np.r_[blo, np.zeros(Y)], np.r_[bhi, np.full(Y, INF)])
    h.changeColsCost(n + Y, np.arange(n + Y, dtype=np.int32), np.r_[capex, np.ones(Y)])
    for (yi, xk, f, g) in cuts:              # η_y − g·x ≥ f − g·x^k
        idx = np.r_[np.arange(n), n + yi].astype(np.int32)
        h.addRow(f - float(g @ xk), INF, len(idx), idx, np.r_[-g, 1.0])
    h.run()
    sol = h.getSolution()
    return np.array(sol.col_value[:n]), h.getInfo().objective_function_value


def main():
    Y = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    oarg = sys.argv[2] if len(sys.argv) > 2 else "3,6,9"
    offyrs = [] if oarg == "none" else [int(s) - 1 for s in oarg.split(",")]
    offyrs = [o for o in offyrs if o < Y]
    pvmode = os.environ.get("PVMODE", "trk")
    tag = "bd%dy_%s%s%s%s%s" % (Y, pvmode,
                              ("_nobatt" if os.environ.get("NOBATT") == "1" else "")
                              + ("_bx%s" % os.environ["BATTX"] if os.environ.get("BATTX") else "")
                              + ("_cgfix" if os.environ.get("CGFIX") else ""),
                              "" if os.environ.get("GP", "real") == "real" else "_prof",
                              "" if os.environ.get("PCS", "1") == "1" else "_oldpcs",
                              "" if oarg == "3,6,9" else "_off" + oarg.replace(",", ""))
    sys.path.insert(0, HERE)
    import lp_multi as M
    M.P["pvmode"] = pvmode
    L, P = M.P["life"], M.P
    capf = P["td"] * P["lf"] * P["hours_month"]
    capex = np.array([                                   # 10 年合计的投资年化与需量费，元/单位
        Y * P["pv_capex"] * M.am(L["pv"]),
        Y * 12 * (P["cap_old"] + capf),
        Y * P["el_capex"] * M.am(L["el"]),
        Y * P["tank"] / P["npk"] * M.am(L["tank"]),
        Y * P["pump"] / P["npk"] * M.am(L["pump"]),
        Y * (M.BATT_2H["cost_e"] + M.BATT_2H["cost_p"] / 2) * M.am(L["batt"]),
        Y * (M.BATT_4H["cost_e"] + M.BATT_4H["cost_p"] / 4) * M.am(L["batt"])])
    dh2 = P["dh2"]
    lo = np.array([0, P["dd_floor"] * P["refinery"], dh2, 0, 0, 0, 0], dtype=float)
    hi = np.array([P["pv_max"], P["tcap"], 3 * dh2, 240 * dh2, 3.6 * dh2,
                   2 * P["tcap"], 4 * P["tcap"]], dtype=float)
    if os.environ.get("NOBATT") == "1":       # 十年无储能对照
        hi[5] = hi[6] = 0.0
    if os.environ.get("CGFIX"):               # 申报需量不可削减：锁定为给定值（kW）
        lo[1] = hi[1] = float(os.environ["CGFIX"])
    if os.environ.get("CGMIN"):               # 申报需量下限（避免主问题提出明显超限的点）
        lo[1] = max(lo[1], float(os.environ["CGMIN"]))
    floor = np.array([2e5, 5e4, 2e4, 2e5, 1e4, 2e5, 2e5])     # 信赖域最小半宽
    # 初值：3 年（2019–2021）联合最优的容量（已与分解结果互相印证），储能 0
    x0 = np.array([1228.91e3, 326.75e3, 138752.07, 140.59e3 * P["npk"], 7577.86 * P["npk"], 0, 0])
    if os.environ.get("X0"):                # 指定初值（如在已收敛的最优点单独评估一次）
        x0 = np.array([float(v) for v in os.environ["X0"].split(",")])
    x0 = np.clip(x0, lo, hi)

    nw = min(int(os.environ.get("WORKERS", 5)), Y)
    groups = [list(range(Y))[i::nw] for i in range(nw)]
    # 子进程是 spawn 新解释器，电价口径/变流器口径要显式带过去（lp_multi 在 import 时读）
    env = {k: os.environ[k] for k in ("PVMODE", "GP", "PCS", "BATTX", "AUXFIX", "EQUIP_COST_X", "SIZING_CASE") if k in os.environ}
    env["PVMODE"] = pvmode
    ctx = mp.get_context("spawn")
    pipes, procs = [], []
    t_start = time.time()
    for gi in groups:
        a, b = ctx.Pipe()
        p = ctx.Process(target=worker, args=(b, gi, offyrs, Y, x0, env))
        p.start()
        pipes.append(a); procs.append(p)
    for a in pipes:
        assert a.recv() == "ready"
    print("%d 个进程已建好 %d 个单年子问题（%s，检修年 %s），用时 %.0f 秒"
          % (nw, Y, pvmode, [o + 1 for o in offyrs], time.time() - t_start), flush=True)

    def evaluate(x):
        for a in pipes:
            a.send(x)
        out = []
        for a in pipes:
            out += a.recv()
        out.sort(key=lambda r: r["yi"])
        return out

    cuts = []
    for fcut in filter(None, os.environ.get("CUTS", "").split(";")):     # 预载同口径割平面
        dc = np.load(project_cache_file(fcut), allow_pickle=True).item()
        cuts += list(dc["cuts"])
        print("预载割平面 %d 条：%s" % (len(dc["cuts"]), os.path.basename(fcut)), flush=True)
    if cuts and os.environ.get("X0FROMCUTS") == "1":                     # 以预载割平面的主问题解作初值
        xm, lbm = master(cuts, capex, lo, hi, Y)
        print("预载割平面主问题：下界 %.1f 万元，初值 %s" % (lbm / 1e4, np.round(xm, 1).tolist()), flush=True)
        x0 = np.clip(xm, lo, hi)
    best_x, UB, best_out = None, INF, None
    xc, rho = x0.copy(), float(os.environ.get("RHO0", 0.5))   # 初值接近最优时宜取小（如 0.1）
    hist = []
    probes = []
    if os.environ.get("PROBE") == "1":              # 开局探测：各容量上下各挪 δ，先铺局部割平面
        dp = float(os.environ.get("PROBE_D", 0.02))
        for j in range(len(CAPS)):
            for sgn in (+1, -1):
                xp = x0.copy()
                xp[j] = x0[j] + sgn * dp * max(abs(x0[j]), floor[j])
                xp = np.clip(xp, lo, hi)
                if not np.allclose(xp, x0):
                    probes.append(xp)
    itmax = int(os.environ.get("ITMAX", 60))
    for it in range(1, itmax + 1 + len(probes)):
        t0 = time.time()
        if it == 1:
            xk, mval = x0.copy(), None
        elif it - 2 < len(probes):
            xk, mval = probes[it - 2], None
        else:
            half = rho * np.maximum(np.abs(xc), floor)
            xk, mval = master(cuts, capex, lo, hi, Y, box=(xc - half, xc + half))
        out = evaluate(xk)
        F = float(capex @ xk) + sum(r["f"] for r in out)
        for r in out:
            cuts.append((r["yi"], xk.copy(), r["f"], r["g"]))
        is_probe = 2 <= it < 2 + len(probes)
        if is_probe:
            if F < UB:
                UB, best_x, best_out, xc = F, xk.copy(), out, xk.copy()
            step = "探测"
        elif F < UB - 1e-6 * abs(UB if UB < INF else F):
            pred = (UB - mval) if (mval is not None and UB < INF) else None
            if pred and (UB - F) >= 0.5 * pred:
                rho = min(rho * 2.0, 2.0)
            UB, best_x, best_out, xc = F, xk.copy(), out, xk.copy()
            step = "改进"
        else:
            rho *= 0.5
            step = "未改进"
        _, LB = master(cuts, capex, lo, hi, Y)
        gap = (UB - LB) / abs(UB)
        bad = [r["yi"] + 1 for r in out if r["status"] != "Optimal"]
        pen = sum(r["stats"]["超需量GWh"] + r["stats"]["绿电缺口GWh"] + r["stats"]["弃光超限GWh"] for r in out)
        hist.append(dict(迭代=it, 本次总成本万元=F / 1e4, 上界万元=UB / 1e4, 下界万元=LB / 1e4,
                         间隙=gap, 信赖域=rho, 光伏MWp=xk[0] / 1e3, 申报需量MW=xk[1] / 1e3,
                         电解槽=xk[2], 罐t=xk[3] / P["npk"] / 1e3, 储能2hMWh=xk[5] / 1e3,
                         储能4hMWh=xk[6] / 1e3, 用时s=time.time() - t0))
        print("第%2d轮 %s：本次 %.1f 万元，上界 %.1f，下界 %.1f，间隙 %.2e，信赖域 %.3f；"
              "光伏 %.1f MWp、需量 %.1f MW、电解槽 %.0f、罐 %.1f t、储能 2h %.1f / 4h %.1f MWh；"
              "罚项 %.3f GWh；%s%.0f 秒"
              % (it, step, F / 1e4, UB / 1e4, LB / 1e4, gap, rho, xk[0] / 1e3, xk[1] / 1e3, xk[2],
                 xk[3] / P["npk"] / 1e3, xk[5] / 1e3, xk[6] / 1e3, pen,
                 ("非最优年份 %s；" % bad) if bad else "", time.time() - t0), flush=True)
        np.save(os.path.join(CACHE, "_%s_割平面.npy" % tag),
                dict(cuts=cuts, best_x=best_x, UB=UB, capex=capex, lo=lo, hi=hi), allow_pickle=True)
        if gap < -1e-5:                          # 下界高于上界只可能是主问题数值误差，不据此判收敛
            print("  下界高于上界 %.2e（主问题数值误差），继续迭代" % gap, flush=True)
        if -1e-5 <= gap < float(os.environ.get("GAP", 1e-4)) or rho < 1e-5:
            break

    # ── 报告 ──
    x = best_x
    print("\n【10 年共用的容量】（%s，间隙 %.2e）" % ("单轴跟踪" if pvmode == "trk" else "固定35°", gap))
    cap = dict(光伏MWp=x[0] / 1e3, 光伏MWac=x[0] / 1e3 / (P.get("dcac") or 1),
               储能2h_MWh=x[5] / 1e3, 储能2h_MW=x[5] / 2e3, 储能4h_MWh=x[6] / 1e3, 储能4h_MW=x[6] / 4e3,
               电解槽Nm3h=x[2], 罐t=x[3] / P["npk"] / 1e3, 泵体kgh=x[4] / P["npk"],
               申报需量MW=x[1] / 1e3)
    for k, v in cap.items():
        print("  %-12s %12.2f" % (k, v))
    parts = dict(zip(["光伏", "需量容量费", "电解槽", "罐体", "泵体", "储能2h", "储能4h"],
                     capex * x / Y / 1e4))
    parts["下网电量电费（贴现权重）"] = sum(r["f"] for r in best_out) / Y / 1e4
    print("\n【年均成本拆解，万元/年】")
    for k, v in parts.items():
        print("  %-22s %12.1f" % (k, v))
    print("  %-22s %12.1f" % ("合计", sum(parts.values())))
    # 储能临界造价（差分法）：先把申报需量上调 0.2% 确保各年都不超限，再分别加 50 MWh 的
    # 2 h、4 h 储能（其余容量不动）重算 10 年，省下的电费与需量费 ÷ 投资 → 临界造价。
    # 其余容量不重优化，所以这是储能价值的保守估计；加上重优化只会让储能价值略高。
    xb = x.copy()
    xb[1] *= 1.002
    ob = evaluate(xb)
    Fb = float(capex @ xb) + sum(r["f"] for r in ob)
    pen_b = sum(r["stats"]["超需量GWh"] + r["stats"]["绿电缺口GWh"] + r["stats"]["弃光超限GWh"] for r in ob)
    print("\n【储能临界造价（差分法，申报需量 +0.2%% 后罚项 %.4f GWh）】" % pen_b)
    for i, bt in ((5, M.BATT_2H), (6, M.BATT_4H)):
        xe = xb.copy()
        xe[i] = xb[i] + 50e3                     # 增量，不是赋值（基准已含储能时赋值会变成减容）
        oe = evaluate(xe)
        save = sum(r["f"] for r in ob) - sum(r["f"] for r in oe)      # 10 年电费（贴现权重）节省，元
        cost = capex[i] * 50e3                                          # 10 年投资，元
        print("  %s 储能 50 MWh：10 年省电费 %.0f 万元，10 年投资 %.0f 万元，"
              "临界造价 %.3f 元/Wh（现 %.2f 元/Wh）"
              % (bt["name"], save / 1e4, cost / 1e4, bt["cost_e"] * save / cost / 1000, bt["cost_e"] / 1000))
    for a in pipes:
        a.send(None)
    for p in procs:
        p.join()
    # 最优点处各容量的边际：多 1 单位容量，10 年（贴现权重）电费省多少；与 10 年投资比（无超限点）
    gsum = sum(r["g"] for r in ob)
    print("\n【需量上调 0.2% 点的对偶边际（10 年合计）】零容量处对偶可能不唯一，不代表实际增容收益；应以差分及重新定容为准")
    unit = ["kWp", "kW", "Nm³/h", "Nm³", "Nm³/h", "kWh", "kWh"]
    for i, k in enumerate(CAPS):
        print("  %-5s 每 %-5s 省电费 %10.2f ｜ 投资 %10.2f ｜ 比值 %.3f"
              % (k, unit[i], -gsum[i], capex[i], -gsum[i] / capex[i]))
    for i, bt in ((5, M.BATT_2H), (6, M.BATT_4H)):
        be = bt["cost_e"] * (-gsum[i]) / capex[i]
        print("  %s 储能：对偶折算值 %.3f 元/Wh（现 %.2f 元/Wh），仅供诊断，不能据此判断是否配置"
              % (bt["name"], be / 1000, bt["cost_e"] / 1000))
    s = pd.DataFrame([dict(年=r["yi"] + 1, 天气=YEARS_ALL[r["yi"]], **r["stats"]) for r in best_out]).set_index("年")
    print("\n【逐年】（第 %s 年 4 月检修）" % [o + 1 for o in offyrs])
    print(s.round(3).to_string())
    pd.DataFrame(hist).to_csv(os.path.join(CACHE, "_%s_迭代.csv" % tag), index=False, encoding="utf-8-sig")
    s.to_csv(os.path.join(CACHE, "_%s_逐年.csv" % tag), encoding="utf-8-sig")
    np.save(os.path.join(CACHE, "_%s_容量.npy" % tag),
            dict(x=x, cap=cap, parts=parts, UB=UB, gap=gap, capex=capex,
                 g=[r["g"] for r in best_out], f=[r["f"] for r in best_out]), allow_pickle=True)
    print("\n已存 _%s_迭代.csv / _逐年.csv / _容量.npy；总用时 %.0f 秒" % (tag, time.time() - t_start))


if __name__ == "__main__":
    main()
