# -*- coding: utf-8 -*-
"""报告图：典型工况（夏季光伏大发、冬季光伏偏弱、全厂检修），各取连续 3 天。

读 优化缓存/_dispatch_10y_trk_推荐.npz，输出 报告/插图/图3~图5 与 优化缓存/典型工况统计.json。
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CACHE, FIG                            # noqa: E402
os.makedirs(FIG, exist_ok=True)
plt.rcParams["font.sans-serif"] = ["SimHei"]     # sans-serif 列表只取第一个可用字体，不做逐字回落
plt.rcParams["axes.unicode_minus"] = False
NPK = 11.1259
WEATHER = [2019, 2020, 2021, 2022, 2023] * 2
CAL = pd.date_range("2001-01-01", periods=8760, freq="h")

C = dict(grid="#7F8FA6", pv="#E3A92B", dis="#4F74AE", cur="#E3A92B", load="#6B737B",
         el="#3E8E68", ch="#9CC0E6", cmp="#B9D3AE", dem="#A93226", price="#1F2A36",
         soc="#4F74AE", tank="#3E8E68")


def window(z, yi, months, score, pick):
    """在指定月份内找连续 3 天、使 score(逐时数组切片) 取极值的起点小时。"""
    best, t_best = None, None
    days = [d for d in range(365) if CAL[d * 24].month in months]
    for d in days:
        if d + 3 > 365 or any(CAL[(d + k) * 24].month not in months for k in range(3)):
            continue
        s = score(z, yi, slice(d * 24, d * 24 + 72))
        if best is None or (s > best if pick == "max" else s < best):
            best, t_best = s, d * 24
    return t_best


def stats(z, yi, t0, cap):
    s = slice(t0, t0 + 72)
    g = lambda k: z[k][yi][s]
    Q, Cg = cap["电解槽Nm3h"], cap["申报需量MW"] * 1e3
    lf = g("产氢") / Q
    hr = CAL[t0:t0 + 72].hour.values
    day = (hr >= 10) & (hr < 18)
    night = (hr >= 20) | (hr < 8)
    run = g("检修") < 0.5
    price = g("电价")
    return dict(
        年=yi + 1, 天气年=WEATHER[yi], 起=CAL[t0].strftime("%m-%d"), 止=CAL[t0 + 71].strftime("%m-%d"),
        起月=int(CAL[t0].month), 起日=int(CAL[t0].day), 止月=int(CAL[t0 + 71].month), 止日=int(CAL[t0 + 71].day),
        光伏最大出力MW=float(g("光伏可发").max() / 1e3), 光伏可发MWh=float(g("光伏可发").sum() / 1e3),
        光伏利用MWh=float(g("光伏利用").sum() / 1e3), 弃光MWh=float(g("弃光").sum() / 1e3),
        下网MWh=float(g("下网").sum() / 1e3), 下网最大MW=float(g("下网").max() / 1e3),
        下网达申报需量小时=int((g("下网") >= 0.999 * Cg).sum()),
        夜间下网均值MW=float(g("下网")[night].mean() / 1e3),
        储能充电MWh=float(g("储能充电").sum() / 1e3), 储能放电MWh=float(g("储能放电").sum() / 1e3),
        电解槽用电MWh=float(g("电解槽").sum() / 1e3), 炼化MWh=float(g("炼化").sum() / 1e3),
        负荷率白天均值=float(lf[day & run].mean()) if (day & run).any() else 0.0,
        负荷率夜间均值=float(lf[night & run].mean()) if (night & run).any() else 0.0,
        负荷率最小=float(lf[run].min()) if run.any() else 0.0,
        负荷率最大=float(lf[run].max()) if run.any() else 0.0,
        罐存起t=float(g("罐料位")[0] / NPK / 1e3), 罐存止t=float(g("罐料位")[-1] / NPK / 1e3),
        罐存最小t=float(g("罐料位").min() / NPK / 1e3), 罐存最大t=float(g("罐料位").max() / NPK / 1e3),
        电价白天均值=float(price[day].mean()), 电价夜间均值=float(price[night].mean()),
        电价最低=float(price.min()), 电价最高=float(price.max()),
        检修小时=int((~run).sum()),
        检修期下网MWh=float(g("下网")[~run].sum() / 1e3), 检修期储能充电MWh=float(g("储能充电")[~run].sum() / 1e3),
        检修期弃光MWh=float(g("弃光")[~run].sum() / 1e3),
        白天下网均值MW=float(g("下网")[day & run].mean() / 1e3) if (day & run).any() else 0.0,
        白天储能充电MWh=float(g("储能充电")[day].sum() / 1e3),
        夜间储能放电MWh=float(g("储能放电")[night].sum() / 1e3))


def plot(z, yi, t0, cap, title, fname):
    s = slice(t0, t0 + 72)
    g = lambda k: z[k][yi][s] / 1e3
    x = pd.date_range("2001-%02d-%02d" % (CAL[t0].month, CAL[t0].day), periods=72, freq="h")
    fig, axs = plt.subplots(3, 1, figsize=(7.8, 6.4), dpi=300, sharex=True,
                            gridspec_kw=dict(height_ratios=[3.2, 1.0, 1.25], hspace=0.12))
    ax = axs[0]
    sup = [("电网购电", g("下网"), C["grid"]), ("光伏出力", g("光伏利用"), C["pv"]),
           ("储能放电", g("储能放电"), C["dis"])]
    base = np.zeros(72)
    for lab, v, col in sup:
        ax.fill_between(x, base, base + v, step="post", color=col, lw=0, label=lab, alpha=0.95)
        base = base + v
    ax.fill_between(x, base, base + g("弃光"), step="post", facecolor="white", edgecolor=C["cur"],
                    hatch="////", lw=0.0, label="弃光")
    dem = [("炼化装置用电", g("炼化"), C["load"]), ("电解槽用电", g("电解槽"), C["el"]),
           ("储能充电", g("储能充电"), C["ch"]), ("增压机用电", g("增压"), C["cmp"])]
    base = np.zeros(72)
    for lab, v, col in dem:
        ax.fill_between(x, -base, -(base + v), step="post", color=col, lw=0, label=lab, alpha=0.95)
        base = base + v
    ax.axhline(cap["申报需量MW"], color=C["dem"], ls="--", lw=1.1, label="申报需量")
    ax.axhline(0, color="#333333", lw=0.6)
    ax.set_ylabel("电力（MW）\n电源侧为正、用电侧为负", fontsize=10)
    ax.grid(axis="y", color="#DDDDDD", lw=0.6)
    ax.legend(ncol=5, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, 1.22), frameon=False)
    ax.set_title(title, fontsize=12, pad=38)
    ax2 = axs[1]
    ax2.step(x, z["电价"][yi][s], where="post", color=C["price"], lw=1.1)
    ax2.set_ylabel("购电电价\n（元/kWh）", fontsize=10)
    ax2.grid(axis="y", color="#DDDDDD", lw=0.6)
    ax3 = axs[2]
    ax3.plot(x, g("储能电量"), color=C["soc"], lw=1.3, label="储能剩余电量（MWh）")
    ax3.set_ylabel("储能剩余\n电量（MWh）", fontsize=10)
    ax3.set_ylim(0, max(cap["储能2h_MWh"] + cap["储能4h_MWh"], 1) * 1.05)
    ax4 = ax3.twinx()
    ax4.plot(x, z["罐料位"][yi][s] / NPK / 1e3, color=C["tank"], lw=1.3, ls="-.", label="储氢罐储氢量（t）")
    ax4.set_ylabel("储氢罐\n储氢量（t）", fontsize=10)
    ax4.set_ylim(0, cap["罐t"] * 1.05)
    h1, l1 = ax3.get_legend_handles_labels(); h2, l2 = ax4.get_legend_handles_labels()
    ax3.legend(h1 + h2, l1 + l2, fontsize=9, loc="upper right", ncol=2, frameon=False)
    ax3.grid(axis="y", color="#DDDDDD", lw=0.6)
    ax3.xaxis.set_major_locator(mdates.HourLocator(byhour=[0]))
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%m月%d日"))
    ax3.xaxis.set_minor_locator(mdates.HourLocator(byhour=[6, 12, 18]))
    for a in axs:
        a.set_xlim(x[0], x[-1] + pd.Timedelta("1h"))
        for d in range(1, 3):
            a.axvline(x[0] + pd.Timedelta(days=d), color="#BBBBBB", lw=0.6)
    fig.savefig(os.path.join(FIG, fname), bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    import sys
    sys.path.insert(0, HERE)
    import dispatch_io
    z = dispatch_io.load(report=True)
    x = z["x"]                                        # 调度文件对应的容量（报告默认为推荐配置）
    cap = dict(申报需量MW=x[1] / 1e3, 电解槽Nm3h=x[2], 罐t=x[3] / NPK / 1e3,
               储能2h_MWh=x[5] / 1e3, 储能4h_MWh=x[6] / 1e3)
    Cg = cap["申报需量MW"] * 1e3
    def pv_sum(z, yi, s):
        """光伏可发电量；夜间均价低于 0.20 元/kWh（整段贴地板价）的时段不作为典型日。"""
        hr = CAL[s].hour.values
        night = (hr >= 20) | (hr < 8)
        return z["光伏可发"][yi][s].sum() if z["电价"][yi][s][night].mean() >= 0.20 else -1.0
    cap_hours = lambda z, yi, s: (z["下网"][yi][s] >= 0.999 * Cg).sum() * 1e9 - z["光伏可发"][yi][s].sum()
    yi_s = 4
    t_s = window(z, yi_s, [6, 7, 8], pv_sum, "max")
    t_w = window(z, yi_s, [11, 12, 1, 2], cap_hours, "max")
    yi_m = 2
    t_m = int(np.where((CAL.month == 3) & (CAL.day == 31) & (CAL.hour == 0))[0][0])
    out = {}
    for key, yi, t0, title, fn in (
            ("夏季", yi_s, t_s, "夏季光伏大发工况", "图3_夏季典型工况.png"),
            ("冬季", yi_s, t_w, "冬季光伏偏弱工况", "图4_冬季典型工况.png"),
            ("检修", yi_m, t_m, "全厂检修工况", "图5_检修典型工况.png")):
        plot(z, yi, t0, cap, "", fn)
        out[key] = stats(z, yi, t0, cap)
        print(key, json.dumps(out[key], ensure_ascii=False))
    json.dump(out, open(os.path.join(CACHE, "典型工况统计.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
