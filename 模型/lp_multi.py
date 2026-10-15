# -*- coding: utf-8 -*-
"""轮台绿电制氢 · 多年逐时联合定容（精简实现）

公式逐条照搬《绿电直连定容测算》框架 core/optimizer.py 的 case 7（业主自投降本），
含本项目加装层：电解槽切平面包络、储氢罐、泵体、1192 附加容量费随申报需量进 LP、分资产年化。

为什么另写：框架用 pulp 逐条建约束对象，单年约 1.5 GB，10 年（87600 小时）要 15 GB 以上，
本机可用内存放不下。这里把同一组方程直接组装成稀疏矩阵交给同一个 HiGHS，内存约十分之一。

一致性检验  python lp_multi.py check
    单年 2023、电池能量下限 400 MWh（框架把下限当存量、不计电芯投资，这里照做），
    目标值与各项容量须与框架 F400c 吻合。

10 年全周期  python lp_multi.py 10y [nobatt]      储能只有 2 h（0.78 元/Wh）与 4 h（0.70 元/Wh）两个选项
    在单年公式上新增：
    1. 天气：2019–2023 逐时出力重复两遍（业主定：更早年份气候已变，参考价值不大）
    2. 电解槽能耗逐时衰减：电堆直流比电耗按 1.008^(t/8760) 连续上升，辅助功耗不衰减；
       第 10 年末大修恢复（在窗口末端，窗口内不出现）
    3. 全厂检修：第 3、6、9 年各停 30 天（默认 4 月整月，业主未给月份），
       期间炼化与制氢负荷为 0、下游不取氢、电解槽不受 25% 下限
    4. 650 号文三约束按年考核（每年各自满足）
    5. 各年运行费按 6% 贴现，权重归一到均值 1；投资按分资产寿命年化后逐年计入
    6. 电池循环寿命约束按年数放大（700 次/年 × 年数）
"""
import os
import sys
import time

import highspy
import numpy as np
import pandas as pd
import scipy.sparse as sp

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CACHE, INP                            # noqa: E402

HPM = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]) * 24
INF = highspy.kHighsInf

# ── 参数（业主 2026-09 定案口径）──
P = dict(
    pv_capex=2600.0,                      # 光伏 2.6 元/Wp（直流），不另加跟踪支架
    dcac=1.2,                             # 容配比：1.2 Wp 组件对应 1 W 逆变器，出力按交流容量削顶
    pvmode=os.environ.get("PVMODE", "fix"),   # fix 固定 35°／trk 单轴跟踪
    # 储能造价不在这里：见 BATT_2H / BATT_4H（2 h 0.78、4 h 0.70 元/Wh，业主定案）
    eff=0.92,                             # 充放各 92%，往返 84.6%
    max_cyc=700,                          # 电池年吞吐上限（次/年）
    el_capex=12000.0,                     # 电解槽 元/(Nm³/h)
    tank=3000.0, pump=30000.0, pump_kwh_kg=2.0, npk=11.1259,
    a0=4.5467, b0=0.4533, aux=0.1776,     # 比电耗 e(x)=a0+b0·x（直流，5.0@100%）+ 辅助
    lo=0.25, hi=1.20, reta=0.96, tstep=0.10, trate=0.50,
    # 可研第 28 页：年操作时间 8000 h、外购氢气 5.91 万 t/a、全厂年用电 284.404 GWh
    #   ⇒ 运行时取氢 5.91e7 kg ÷ 8000 h ÷ 0.08988 = 82194 Nm³/h，炼化负荷 35.55 MW
    annual_h2_kg=30_000_000.0,             # 各年固定用氢 3 万吨，检修期需求分摊至非检修小时
    dh2=30_000_000.0 / 8760.0 * 11.1259, refinery=0.0,
    # cap_old = 最大需量电价 30.4 元/kW·月（国网新疆 2026-03 代购电价表 110 kV 两部制；
    #   同表按变压器容量计费是 19 元/kVA·月，本项目按需量计费故取 30.4）
    # lf：新疆 110 kV 及以上工商业两部制用户平均负荷率公布值（1192 号文容量电费参数）
    cap_old=30.4, td=0.0815, lf=0.4785, hours_month=730,
    rate=0.06, om=0.02, life=dict(pv=25, batt=15, tank=25, pump=20, el=20),
    # 投资年化口径：aftertax = 业主给的税后→税前 θ_k；simple = CRF+运维（仅校核框架用）
    ann="aftertax", tax=0.25, dep_years=15, residual=0.05,
    green=0.0, absorb=0.60, tcap=900_000.0, pv_max=0.0, b_max=4e6, dd_floor=0.7,
    deg=0.008,
    # 节点电价口径：real = 若羌 2025-07~2026-06 真实逐时（默认）；prof = 旧的月×时平均 profile。
    #   月×时平均会把日内价差压掉约 25%、把"地板小时"占比从 60% 抹到 30%，
    #   系统性低估电解槽/罐/电池这类柔性资源的价值，故默认改用真实逐时。
    gpmode=os.environ.get("GP", "real"),
    # 变流器口径：True = inj≤P、rel≤P、inj+rel≤P（物理口径，默认）；
    #   False = 框架原写法 inj≤P·η²、无互斥（仅 check 校核时用）。
    pcs=(os.environ.get("PCS", "1") == "1"),
    # 辅机恒定占比 φ：概算电耗表电解槽外 740 kW/20 MW（碱液泵、冷冻水机、纯化脱氧/干燥、
    #   冷却塔、纯水机、空压机）。现默认 0 = 全部按产量比例摊进比电耗截距（框架原口径）；
    #   φ>0 时把其中 φ 份改成"非检修小时按额定容量恒定"，(1−φ) 份仍按产量摊。
    #   φ=1 时 25% 负荷下辅机功率是原来的 4 倍，正好压在夜间基荷的边际上。
    auxfix=float(os.environ.get("AUXFIX", 0.0)),
)

EQUIP_COST_X = float(os.environ.get("EQUIP_COST_X", "1.0"))
if not np.isfinite(EQUIP_COST_X) or EQUIP_COST_X <= 0:
    raise ValueError("EQUIP_COST_X 必须为有限正数")
for _cost_key in ("pv_capex", "el_capex", "tank", "pump"):
    P[_cost_key] *= EQUIP_COST_X  # 设备造价敏感性：不改变储能造价、需量费及技术参数


def am(n):
    """分资产年化系数 θ_k。

    aftertax：θ_k = ρ_k·CRF(r*, n_k)/(1−T) + ω_k，ρ_k = 1 − PV_dep − PV_sal
      PV_dep：直线折旧（残值率 5%，折旧年限 min(15, n_k)）的抵税额按 r* 折现占投资比；
      PV_sal：第 n_k 年末残值按 r* 折现占投资比。把税后回收要求还原成税前收入口径。
    simple：CRF(r*, n_k) + ω_k（框架 annualize_mode='simple' 的写法，只在校核时用）。
    """
    r = P["rate"]
    crf = r / (1 - (1 + r) ** (-n))
    if P["ann"] != "aftertax":
        return crf + P["om"]
    d = min(P["dep_years"], n)
    pv_dep = sum((1 - P["residual"]) / d * P["tax"] / (1 + r) ** y for y in range(1, d + 1))
    pv_sal = P["residual"] / (1 + r) ** n
    return (1 - pv_dep - pv_sal) * crf / (1 - P["tax"]) + P["om"]


FEE_SUM = 0.046060 + 0.006856 + 0.013424        # 系统运行费 + 政府基金 + 线损


def nodal_year():
    """8760 点，日历序（index 0 = 1 月 1 日 0 时）。

    gpmode="real"：储+氢_分时电价_2026.csv，由 mk_price.py --tou 生成，
      谷/平/峰为 0.11/0.28/0.45 元/kWh，直接使用，不叠加原节点杂费。
    gpmode="prof"：早期的月×时平均电价 _若羌节点_月时profile.csv（仅 check 校核用）。
    """
    if P.get("gpmode", "real") == "real":
        s = pd.read_csv(os.path.join(INP, "储+氢_分时电价_2026.csv"), index_col=0).iloc[:, 0].values
        assert len(s) == 8760
        return np.asarray(s, dtype=float)  # 日历序；用户给定电价直接使用，不叠加旧节点杂费
    mh = pd.read_csv(os.path.join(INP, "_若羌节点_月时profile.csv"), index_col=0)
    mh.columns = [int(c) for c in mh.columns]
    for m in (4, 5):
        if m not in mh.index:
            mh.loc[m] = mh.loc[3] + (mh.loc[6] - mh.loc[3]) * ((m - 3) / 3.0)
    mh = mh.sort_index()
    mo = np.repeat(np.arange(1, 13), HPM)
    hr = np.concatenate([np.arange(h) % 24 for h in HPM])
    return np.array([mh.loc[mo[t], hr[t]] for t in range(len(mo))]) + FEE_SUM


_CF = None


def pv_year(y):
    global _CF
    if _CF is None:
        _CF = pd.read_csv(os.path.join(INP, "轮台_风光小时出力_ninja.csv"),
                          index_col=0, parse_dates=True)
    s = _CF.loc[_CF.index.year == y, "光伏CF_单轴跟踪" if P["pvmode"] == "trk" else "光伏CF_固定35度"]
    if len(s) == 8784:                        # 闰年去 2 月 29 日
        s = s[~((s.index.month == 2) & (s.index.day == 29))]
    assert len(s) == 8760, "%d 年小时点 %d" % (y, len(s))
    a = np.clip(np.nan_to_num(s.values.astype(float)), 0, None)
    return np.minimum(a, 1.0 / P["dcac"]) if P.get("dcac") else a      # 容配比削顶


PEN_OV, PEN_G, PEN_A = 10000.0, 10.0, 10.0     # 分解子问题松弛罚价（元/kWh），最优解处应全为 0


def build_lp(alpha, gp, D_u, dh2_t, off, m_t, w_t, Y, batts, fixcap=None, sub=False, log=True):
    """组装 LP，返回 (HighsLp, meta)。

    batts：储能资产列表，每项 dict(name, dur_min, dur_max, cost_e 元/kWh, cost_p 元/kW,
      cb0 能量下限（框架口径视为存量、不计电芯投资，仅校核用）, fix=(能量kWh, 功率kW) 锁死)。
    fixcap：容量固定值 dict（Cs、Cg、Qel、Hc、Pmp、E_<名>、P_<名>），按年分解时由主问题给定。
    sub=True：按年分解的子问题。容量投资与需量费不进目标（它们在主问题里），并加三类松弛
      保证任何容量下都可行：下网超申报需量 ov、绿电占比缺口 sg、弃光超限 sa，各按罚价计。
    """
    T = len(alpha)
    t = np.arange(T)
    cyc_year = os.environ.get("CYC") == "year" and T % 8760 == 0 and T > 8760
    if cyc_year:                    # 每年首尾闭环：年末一小时的下一步回到本年年初
        tn = t + 1
        last = (t + 1) % 8760 == 0
        tn[last] = t[last] - 8759
    else:
        tn = (t + 1) % T
    names = ["Xs", "cur", "Xg", "Pel", "q", "Sh2", "fin"] + (["ov"] if sub else [])
    for bt in batts:
        names += ["inj_" + bt["name"], "rel_" + bt["name"], "S_" + bt["name"]]
    B = {k: i * T for i, k in enumerate(names)}
    nsc = len(names) * T
    scal = ["Cs", "Cg", "Qel", "Hc", "Pmp"]
    for bt in batts:
        scal += ["E_" + bt["name"], "P_" + bt["name"]]
    green_enabled = P["green"] > 0.0
    extra = ((["sg%d" % y for y in range(Y)] if green_enabled else [])
             + ["sa%d" % y for y in range(Y)]) if sub else []
    I = {k: nsc + i for i, k in enumerate(scal + extra)}
    ncol = nsc + len(scal) + len(extra)
    split = int(os.environ.get("SPLIT", "0"))
    if split and T > split:         # 容量分段复制，消稠密列
        nblk = -(-T // split)
        blk = t // split
        CP = {}
        for k in scal:
            CP[k] = ncol + np.arange(nblk)
            ncol += nblk
    else:
        CP = None

    def cap(k):
        return CP[k][blk] if CP is not None else I[k]

    def col(k):
        return B[k] + t

    # 列界
    lb = np.zeros(ncol)
    ub = np.full(ncol, INF)
    ub[B["Pel"] + t[off]] = 0.0
    ub[B["q"] + t[off]] = 0.0
    ub[I["Cs"]] = P["pv_max"]
    lb[I["Cg"]], ub[I["Cg"]] = P["dd_floor"] * float(np.mean(D_u)), P["tcap"]
    lb[I["Qel"]], ub[I["Qel"]] = P["dh2"], 3.0 * P["dh2"]
    ub[I["Hc"]] = 240.0 * P["dh2"]
    if os.environ.get("BOUNDS") == "1":     # 逐时变量有限上界（物理最大值）
        qmax = P["hi"] * 3.0 * P["dh2"]
        pmax = (P["a0"] * float(np.max(m_t)) + P["aux"] + P["b0"] * float(np.max(m_t)) * P["hi"]) \
            * P["hi"] * 3.0 * P["dh2"] / P["reta"]
        ub[col("Xs")] = alpha * P["pv_max"]
        ub[col("cur")] = alpha * P["pv_max"]
        ub[col("Xg")] = P["tcap"]
        ub[col("Pel")] = np.minimum(ub[col("Pel")], pmax)
        ub[col("q")] = np.minimum(ub[col("q")], qmax)
        ub[col("Sh2")] = 240.0 * P["dh2"]
        ub[col("fin")] = qmax
        ub[I["Pmp"]] = qmax
        for bt in batts:
            ub[col("inj_" + bt["name"])] = P["tcap"]
            ub[col("rel_" + bt["name"])] = P["tcap"]
            ub[col("S_" + bt["name"])] = P["b_max"]
    for bt in batts:
        iE, iP = I["E_" + bt["name"]], I["P_" + bt["name"]]
        lb[iE], ub[iE] = bt.get("cb0", 0.0), P["b_max"]
        ub[iP] = P["tcap"]
        if bt.get("fix") is not None:
            lb[iE] = ub[iE] = bt["fix"][0]
            lb[iP] = ub[iP] = bt["fix"][1]
    if CP is not None:                      # 副本自由，由等式绑定
        for k in scal:
            lb[CP[k]] = -INF
            ub[CP[k]] = INF
    if fixcap:
        for k, v in fixcap.items():
            lb[I[k]] = ub[I[k]] = v

    # 目标：下网电量电费（逐时，含贴现权重）+ 容量电费（含 1192 附加）+ 分资产年化投资
    c = np.zeros(ncol)
    c[col("Xg")] = w_t * gp
    offset = 0.0
    capf = P["td"] * P["lf"] * P["hours_month"]
    L = P["life"]
    if sub:
        c[col("ov")] = PEN_OV
        for y in range(Y):
            if green_enabled:
                c[I["sg%d" % y]] = PEN_G
            c[I["sa%d" % y]] = PEN_A
    else:
        c[I["Cg"]] = Y * 12 * (P["cap_old"] + capf)
        c[I["Cs"]] = Y * P["pv_capex"] * am(L["pv"])
        c[I["Hc"]] = Y * P["tank"] / P["npk"] * am(L["tank"])
        c[I["Pmp"]] = Y * P["pump"] / P["npk"] * am(L["pump"])
        c[I["Qel"]] = Y * P["el_capex"] * am(L["el"])
        for bt in batts:
            c[I["E_" + bt["name"]]] = Y * bt["cost_e"] * am(L["batt"])
            c[I["P_" + bt["name"]]] = Y * bt["cost_p"] * am(L["batt"])
            offset -= Y * bt.get("cb0", 0.0) * bt["cost_e"] * am(L["batt"])

    eps = float(os.environ.get("EPSREG", 0.0))
    if eps > 0:
        # 调度复算用：弃光小时里"电解槽高于曲线、增压机空转、储能同时充放"都是零成本多解，
        # 加 1e-5 元/kWh 量级的极小成本让求解器取物理上最简的那一个，对目标值影响可忽略
        c[col("fin")] += eps * P["pump_kwh_kg"] / P["npk"]
        c[col("Pel")] += eps
        for bt in batts:
            c[col("inj_" + bt["name"])] += eps
            c[col("rel_" + bt["name"])] += eps

    R, C, V, RL, RU = [], [], [], [], []
    nrow = [0]

    def fam(terms, lo, hi):
        k = len(np.atleast_1d(lo)) if np.ndim(lo) else T
        rows = np.arange(nrow[0], nrow[0] + k)
        for cols, vals in terms:
            cols = np.broadcast_to(np.asarray(cols), (k,))
            vals = np.broadcast_to(np.asarray(vals, dtype=float), (k,))
            nz = vals != 0
            R.append(rows[nz]); C.append(cols[nz]); V.append(vals[nz])
        RL.append(np.broadcast_to(np.asarray(lo, dtype=float), (k,)).copy())
        RU.append(np.broadcast_to(np.asarray(hi, dtype=float), (k,)).copy())
        nrow[0] += k

    def row(cols, vals, lo, hi):
        cols = np.asarray(cols)
        vals = np.broadcast_to(np.asarray(vals, dtype=float), cols.shape)
        R.append(np.full(len(cols), nrow[0])); C.append(cols); V.append(vals.copy())
        RL.append(np.array([lo], dtype=float)); RU.append(np.array([hi], dtype=float))
        nrow[0] += 1

    z, ninf, pinf = np.zeros(T), np.full(T, -INF), np.full(T, INF)
    eff = P["eff"]
    pk = P["pump_kwh_kg"] / P["npk"]
    # 功率平衡：D_u + Pel + 压缩电耗 + Σ充电 = 光伏 + 下网 + Σ放电（上网为 0）
    _phi = float(P.get("auxfix", 0.0))
    _auxk = _phi * P["aux"] / P["reta"] * (~off)        # 恒定辅机：检修期为 0
    terms = [(col("Pel"), 1), (col("fin"), pk), (col("Xs"), -1), (col("Xg"), -1)]
    if _phi > 0:
        terms.append((cap("Qel"), _auxk))
    for bt in batts:
        terms += [(col("inj_" + bt["name"]), 1), (col("rel_" + bt["name"]), -1)]
    fam(terms, -D_u, -D_u)
    fam([(col("Xs"), 1), (col("cur"), 1), (cap("Cs"), -alpha)], z, z)           # 光伏出力
    for bt in batts:                                                            # 每套储能
        nm = bt["name"]
        iE, iP = I["E_" + nm], I["P_" + nm]
        cE, cP = cap("E_" + nm), cap("P_" + nm)
        fam([(col("rel_" + nm), 1), (cP, -1)], ninf, z)                         # 放电功率
        if P.get("pcs", True):
            # PCS=1：变流器口径修正。框架原写法把充电功率限在 P·η²=0.846P（不物理），
            # 且充放两条分开写、允许同一小时既充又放（实测最优解里出现过 15 小时、最大重叠 66 MW）。
            # 改为：充电 ≤ P、放电 ≤ P，并加一条 充+放 ≤ P 让同时充放失去意义（仍是纯 LP）。
            fam([(col("inj_" + nm), 1), (cP, -1.0)], ninf, z)
            fam([(col("inj_" + nm), 1), (col("rel_" + nm), 1), (cP, -1.0)], ninf, z)
        else:
            fam([(col("inj_" + nm), 1), (cP, -eff * eff)], ninf, z)              # 充电功率
        fam([(B["S_" + nm] + tn, 1), (col("S_" + nm), -1), (col("inj_" + nm), -eff),
             (col("rel_" + nm), 1 / eff)], z, z)                                # 荷电（首末闭环）
        if cyc_year or P.get("pcs", True):                                      # 物理口径：所有小时含首小时
            fam([(col("S_" + nm), 1), (cE, -1)], ninf, z)
        else:                                                                   # S[1..T-1] ≤ E，照框架
            fam([(B["S_" + nm] + t[1:], 1), ((cE[1:] if CP is not None else iE), -1)],
                np.full(T - 1, -INF), np.zeros(T - 1))
        row(np.r_[col("rel_" + nm), iE], np.r_[np.ones(T), -P["max_cyc"] * Y], -INF, 0.0)
        row([iE, iP], [1, -bt["dur_min"]], 0.0, INF)                            # 时长下限
        row([iE, iP], [1, -bt["dur_max"]], -INF, 0.0)                           # 时长上限
    fam([(col("Xg"), 1), (cap("Cg"), -1)] + ([(col("ov"), -1)] if sub else []),
        ninf, z)                                                                # 下网 ≤ 申报需量（+超限松弛）
    iQ, iHc, iPm = cap("Qel"), cap("Hc"), cap("Pmp")
    fam([(col("q"), 1), (iQ, -np.where(off, 0.0, P["lo"]))], z, pinf)           # 电解槽下限（检修期不设）
    fam([(col("q"), 1), (iQ, -P["hi"])], ninf, z)                               # 电解槽上限
    phi = float(P.get("auxfix", 0.0))
    a_t = P["a0"] * m_t + (1.0 - phi) * P["aux"]        # 辅机恒定份移出比电耗截距
    b_t = P["b0"] * m_t
    auxk = phi * P["aux"] / P["reta"]                   # kW per (Nm³/h) 额定容量，非检修小时恒定
    fam([(col("Pel"), 1), (iQ, -(a_t * P["hi"] + b_t * P["hi"] * P["hi"]) / P["reta"])],
        ninf, z)                                                                # 额定交流功率
    fam([(col("Pel"), P["reta"]), (col("q"), -(a_t + P["lo"] * b_t))], z, pinf)  # 停机分摊段
    u = P["lo"]
    while u <= P["hi"] + 1e-9:                                                  # 切平面，步长照框架
        fam([(col("Pel"), P["reta"]), (col("q"), -(a_t + 2 * b_t * u)), (iQ, b_t * u * u)],
            z, pinf)
        u += P["tstep"]
    fam([(col("Sh2"), 1), (iHc, -1)], ninf, z)                                  # 罐容
    fam([(B["Sh2"] + tn, 1), (col("Sh2"), -1), (col("q"), -1)], -dh2_t, -dh2_t)  # 料位平衡（闭环）
    fam([(col("fin"), 1), (col("q"), -1)], -dh2_t, pinf)                        # 进罐量
    fam([(col("fin"), 1), (iPm, -1)], ninf, z)                                  # 泵体能力
    fam([(col("q"), 1), (iHc, -P["trate"])], ninf, dh2_t)                      # 进气速率
    fam([(col("q"), 1), (iHc, P["trate"])], dh2_t, pinf)                       # 出气速率
    if CP is not None:
        for k in scal:
            nb = len(CP[k])
            fam([(CP[k], 1), (np.full(nb, I[k]), -1)], np.zeros(nb), np.zeros(nb))
    g = 1 - P["green"]
    for yi in range(Y):                                                         # 650 按年考核
        tt = t[yi * 8760:(yi + 1) * 8760]
        n_ = len(tt)
        cA = [B["Xs"] + tt, B["cur"] + tt]
        vA = [np.full(n_, 1 - P["absorb"]), np.full(n_, -P["absorb"])]
        cG = [B["Pel"] + tt, B["fin"] + tt, B["Xg"] + tt]
        vG = [np.full(n_, g), np.full(n_, g * pk), -np.ones(n_)]
        if _phi > 0:                                     # 恒定辅机计入总负荷
            cG.append([I["Qel"]]); vG.append([g * float(_auxk[tt].sum())])
        if sub:
            cA.append([I["sa%d" % yi]]); vA.append([1.0])
            if green_enabled:
                cG.append([I["sg%d" % yi]]); vG.append([1.0])
        row(np.concatenate(cA), np.concatenate(vA), 0.0, INF)
        if green_enabled:
            row(np.concatenate(cG), np.concatenate(vG), -g * float(D_u[tt].sum()), INF)

    A = sp.csc_matrix((np.concatenate(V), (np.concatenate(R), np.concatenate(C))),
                      shape=(nrow[0], ncol))
    if log:
        print("模型：%d 行 × %d 列，非零 %d" % (A.shape[0], A.shape[1], A.nnz), flush=True)
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = ncol, nrow[0]
    lp.col_cost_, lp.col_lower_, lp.col_upper_ = c, lb, ub
    lp.row_lower_, lp.row_upper_ = np.concatenate(RL), np.concatenate(RU)
    lp.offset_ = offset
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.num_col_, lp.a_matrix_.num_row_ = ncol, nrow[0]
    lp.a_matrix_.start_, lp.a_matrix_.index_, lp.a_matrix_.value_ = A.indptr, A.indices, A.data
    meta = dict(T=T, Y=Y, names=names, B=B, scal=scal, extra=extra, I=I, batts=batts)
    return lp, meta


def solver_options(h, crossover="on", solver="ipm", time_limit=None, log=True):
    h.setOptionValue("output_flag", bool(log))
    for k, v in (("solver", solver), ("run_crossover", crossover), ("presolve", "on"),
                 ("parallel", "on"), ("primal_feasibility_tolerance", 1e-9),
                 ("dual_feasibility_tolerance", 1e-9)):
        h.setOptionValue(k, v)
    if time_limit:
        h.setOptionValue("time_limit", float(time_limit))
    for kv in filter(None, os.environ.get("HOPT", "").split(",")):     # 额外求解器参数
        k, v = kv.split("=")
        v = int(v) if v.lstrip("-").isdigit() else (
            float(v) if v.replace(".", "").replace("-", "").replace("e", "").isdigit() else v)
        h.setOptionValue(k, v)


def extract(h, meta, log=True, t0=None):
    st = h.modelStatusToString(h.getModelStatus())
    sol = h.getSolution()
    x = np.array(sol.col_value)
    obj = h.getInfo().objective_function_value
    if log and t0 is not None:
        print("求解 %s，%.0f 秒，目标 %.1f 万元" % (st, time.time() - t0, obj / 1e4), flush=True)
    T, B, I = meta["T"], meta["B"], meta["I"]
    res = dict(status=st, obj=obj, T=T, Y=meta["Y"], batts=[bt["name"] for bt in meta["batts"]],
               bcost={bt["name"]: (bt["cost_e"], bt["cost_p"]) for bt in meta["batts"]})
    for k in meta["scal"] + meta["extra"]:
        res[k] = x[I[k]]
    for k in meta["names"]:
        res[k] = x[B[k]:B[k] + T]
    res["rel"] = sum((res["rel_" + n] for n in res["batts"]), np.zeros(T))
    res["Cb"] = sum(res["E_" + n] for n in res["batts"])
    res["Pb"] = sum(res["P_" + n] for n in res["batts"])
    return res


def build_and_solve(alpha, gp, D_u, dh2_t, off, m_t, w_t, Y, batts,
                    crossover="on", solver="ipm", time_limit=None, log=True):
    lp, meta = build_lp(alpha, gp, D_u, dh2_t, off, m_t, w_t, Y, batts, log=log)
    h = highspy.Highs()
    solver_options(h, crossover, solver, time_limit, log)
    h.passModel(lp)
    t0 = time.time()
    h.run()
    return extract(h, meta, log, t0)


FEE = dict(系统运行费=0.046060, 政府基金=0.006856, 线损=0.013424)


def summarize(r, gp, D_u, alpha):
    """逐年汇总（电费为不贴现原值，便于对账）。夜间 = 光伏出力为 0 的小时。"""
    fee = 0.0 if P["gpmode"] == "real" else sum(FEE.values())
    rows = []
    for yi in range(r["Y"]):
        s = slice(yi * 8760, (yi + 1) * 8760)
        xg, pv, cur = r["Xg"][s], r["Xs"][s], r["cur"][s]
        load = D_u[s] + r["Pel"][s]
        nt = alpha[s] <= 0
        xn = xg[nt]
        rows.append(dict(
            年=yi + 1, 光伏发电GWh=pv.sum() / 1e6, 弃光GWh=cur.sum() / 1e6,
            下网GWh=xg.sum() / 1e6, 下网电费万元=float((xg * gp[s]).sum()) / 1e4,
            夜间下网GWh=xn.sum() / 1e6,
            夜间节点价=float((xn * (gp[s][nt] - fee)).sum() / max(xn.sum(), 1)),
            夜间电量价合计=float((xn * gp[s][nt]).sum() / max(xn.sum(), 1)),
            当年下网峰MW=xg.max() / 1e3, 产氢亿Nm3=r["q"][s].sum() / 1e8,
            电解槽GWh=r["Pel"][s].sum() / 1e6,
            绿电占比pct=(1 - xg.sum() / load.sum()) * 100,
            储能放电GWh=r["rel"][s].sum() / 1e6))
    return pd.DataFrame(rows).set_index("年")


def cap_table(r):
    d = dict(光伏MWp=r["Cs"] / 1e3, 光伏MWac=r["Cs"] / 1e3 / (P.get("dcac") or 1.0))
    for n in r["batts"]:
        d["储能%s_MWh" % n] = r["E_" + n] / 1e3
        d["储能%s_MW" % n] = r["P_" + n] / 1e3
    d.update(电解槽Nm3h=r["Qel"], 罐t=r["Hc"] / P["npk"] / 1e3, 泵体kgh=r["Pmp"] / P["npk"],
             申报需量MW=r["Cg"] / 1e3, 目标万元=r["obj"] / 1e4)
    return d


def components(r, gp, w_t):
    """年均成本拆解（万元/年），各项之和 = 目标值 / 年数。"""
    L, Y = P["life"], r["Y"]
    capf = P["td"] * P["lf"] * P["hours_month"]
    c = {"下网电量电费": float((w_t * gp * r["Xg"]).sum()) / Y,
         "需量容量费": r["Cg"] * 12 * (P["cap_old"] + capf),
         "光伏": r["Cs"] * P["pv_capex"] * am(L["pv"])}
    for n in r["batts"]:
        ce, cp = r["bcost"][n]
        c["储能" + n] = (r["E_" + n] * ce + r["P_" + n] * cp) * am(L["batt"])
    c.update({"电解槽": r["Qel"] * P["el_capex"] * am(L["el"]),
              "罐体": r["Hc"] / P["npk"] * P["tank"] * am(L["tank"]),
              "泵体": r["Pmp"] / P["npk"] * P["pump"] * am(L["pump"])})
    c = {k: v / 1e4 for k, v in c.items()}
    c["合计"] = sum(c.values())
    return c


def night_stats(r, gp, alpha):
    """夜间（光伏出力为 0 的小时）下网电价构成。"""
    n = alpha <= 0
    xg = r["Xg"][n]
    fees = {} if P["gpmode"] == "real" else FEE
    node = gp[n] - sum(fees.values())
    w = xg.sum()
    d = {"夜间下网GWh": w / 1e6, "夜间下网均值MW": xg.mean() / 1e3,
         "节点电价(加权)": float((xg * node).sum() / w)}
    d.update(fees)
    d["夜间下网电量价合计"] = d["节点电价(加权)"] + sum(fees.values())
    return d


OLD = dict(pv_capex=2880.0, dcac=None, pvmode="trk", ann="simple",
           gpmode="prof", pcs=False,                              # 校核必须用框架原口径
           dh2=6.57e8 / 8760.0, refinery=284.404e6 / 8760.0)      # 框架 F400c 的口径
_BX = float(os.environ.get("BATTX", 1.0))      # 储能造价倍率（敏感性用，两档同比例）
BATT_2H = dict(name="2h", dur_min=2.0, dur_max=2.0, cost_e=780.0 * _BX, cost_p=0.0)   # 0.78 元/Wh
BATT_4H = dict(name="4h", dur_min=4.0, dur_max=4.0, cost_e=700.0 * _BX, cost_p=0.0)   # 0.70 元/Wh


def hydrogen_demand(off):
    """单年取氢曲线：检修期为零，非检修小时均摊固定年用氢量。"""
    active_hours = int(np.count_nonzero(~off))
    if active_hours == 0:
        raise ValueError("全年检修无法满足固定年用氢量")
    hourly = P["annual_h2_kg"] * P["npk"] / active_hours
    return np.where(off, 0.0, hourly)


def one_year_inputs(y=2023):
    alpha, gp = pv_year(y), nodal_year()
    return (alpha, gp, np.full(8760, P["refinery"]), np.full(8760, P["dh2"]),
            np.zeros(8760, dtype=bool), np.ones(8760), np.ones(8760))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "check":
        # 与框架 F400c 同口径：单年 2023、无衰减、无检修、无贴现、单套储能 2–8 h、能量下限 400 MWh
        P.update(OLD)
        alpha, gp, D_u, dh2, off, m1, w1 = one_year_inputs()
        bt = dict(name="B", dur_min=2.0, dur_max=8.0, cost_e=640.0, cost_p=200.0, cb0=400_000.0)
        r = build_and_solve(alpha, gp, D_u, dh2, off, m1, w1, 1, [bt])
        got = cap_table(r)
        ref = [("光伏MW", 1202.7765, got["光伏MWp"]), ("电池MWh", 400.0, got["储能B_MWh"]),
               ("电池MW", 130.7268, got["储能B_MW"]), ("电解槽Nm3h", 128706.143, got["电解槽Nm3h"]),
               ("罐t", 126.063, got["罐t"]), ("申报需量MW", 259.4041, got["申报需量MW"]),
               ("目标万元", 110773.4, got["目标万元"])]
        print("%-10s %14s %14s" % ("", "框架 F400c", "精简实现"))
        for k, a, b in ref:
            print("%-10s %14.3f %14.3f" % (k, a, b))
        return

    if mode == "sweep":
        # 单年（2023）4 h 储能容量扫描：每一档把储能锁死，其余容量与调度全部重新优化
        alpha, gp, D_u, dh2, off, m1, w1 = one_year_inputs()
        sizes = [None] + [float(x) for x in (sys.argv[2:] or ["0", "100", "200", "400", "800"])]
        rows, nights = [], []
        for e in sizes:
            bt = dict(BATT_4H, fix=None if e is None else (e * 1e3, e * 1e3 / 4.0))
            r = build_and_solve(alpha, gp, D_u, dh2, off, m1, w1, 1, [bt], log=False)
            lab = "自由" if e is None else "%gMWh" % e
            row_ = dict(档=lab, 状态=r["status"])
            row_.update(cap_table(r))
            row_.update(components(r, gp, w1))
            rows.append(row_)
            nt = night_stats(r, gp, alpha); nt["档"] = lab
            nights.append(nt)
        df = pd.DataFrame(rows).set_index("档")
        print("【4 h 储能容量扫描，单年 2023，%s，万元/年】" % ("固定35°" if P["pvmode"] == "fix" else "单轴跟踪"))
        print(df.round(1).T.to_string())
        print("\n【夜间下网电价构成，元/kWh】")
        print(pd.DataFrame(nights).set_index("档").round(4).T.to_string())
        df.to_csv(os.path.join(CACHE, "_sweep_%s.csv" % P["pvmode"]), encoding="utf-8-sig")
        return

    # ── 10 年全周期：储能只有 2 h（0.78 元/Wh）与 4 h（0.70 元/Wh）两个选项 ──
    years = ([2019, 2020, 2021, 2022, 2023] * 2)[:int(os.environ.get("NY", 10))]
    Y = len(years)
    nobatt = len(sys.argv) > 2 and sys.argv[2] == "nobatt"
    batts = [] if nobatt else [BATT_2H, BATT_4H]
    alpha = np.concatenate([pv_year(y) for y in years])
    gp = np.tile(nodal_year(), Y)
    T = 8760 * Y
    t = np.arange(T)
    yr = t // 8760
    hy = t % 8760
    apr = (hy >= HPM[:3].sum()) & (hy < HPM[:4].sum())                  # 4 月整月 = 30 天
    offyrs = [int(x) - 1 for x in os.environ.get("OFFYRS", "3,6,9").split(",") if x.strip()]
    off = np.isin(yr, offyrs) & apr                                      # 默认第 3、6、9 年
    if os.environ.get("NY"):
        print("测试规模：前 %d 年（%s）" % (Y, years), flush=True)
    D_u = np.where(off, 0.0, P["refinery"])
    dh2 = np.concatenate([hydrogen_demand(off[yi * 8760:(yi + 1) * 8760]) for yi in range(Y)])
    m_t = (1 + P["deg"]) ** (t / 8760.0)                                 # 能耗逐时衰减
    disc = (1 + P["rate"]) ** -(np.arange(Y) + 1.0)
    w_y = disc / disc.mean()                                             # 贴现权重，均值 1
    w_t = w_y[yr]
    tag = "10y_%s%s" % (P["pvmode"], "_nobatt" if nobatt else "")
    r = build_and_solve(alpha, gp, D_u, dh2, off, m_t, w_t, Y, batts,
                        crossover=os.environ.get("XOVER", "off"),
                        solver=os.environ.get("SOLVER", "ipm"))
    print("\n【10 年共用的容量】（%s）" % ("固定35°" if P["pvmode"] == "fix" else "单轴跟踪"))
    for k, v in cap_table(r).items():
        print("  %-14s %12.2f" % (k, v))
    print("\n【年均成本拆解，万元/年（运行费按 6%% 贴现权重）】")
    for k, v in components(r, gp, w_t).items():
        print("  %-10s %12.1f" % (k, v))
    capf = P["td"] * P["lf"] * P["hours_month"]
    print("\n需量电价 %.2f 元/kVA·月 = 现行 30.40 + 1192 附加 %.2f；折 %.1f 元/kW·年"
          % (30.4 + capf, capf, (30.4 + capf) * 12))
    s = summarize(r, gp, D_u, alpha)
    print("\n【逐年】")
    print(s.round(3).to_string())
    np.save(os.path.join(CACHE, "_multi_%s.npy" % tag),
            {k: v for k, v in r.items()}, allow_pickle=True)
    s.to_csv(os.path.join(CACHE, "_multi_%s_逐年.csv" % tag), encoding="utf-8-sig")
    print("\n已存 _multi_%s.npy / _multi_%s_逐年.csv" % (tag, tag))


if __name__ == "__main__":
    main()
