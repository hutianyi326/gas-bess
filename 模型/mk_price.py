# -*- coding: utf-8 -*-
"""节点电价处理：原始数据/若羌2507-2606节点电价.xlsx → 逐时电价 + 月×时均值。

Sheet1 为 15 分钟电价，时段标签为区间结束（00:15 指 00:00–00:15），按区间起点归入小时；
Sheet2 为小时电价，"时点"列 2025-12-31 前写作 1~24（时点 h = 时钟 h−1），2026-01-01 起写作 "0时"~"23时"。
两表重叠时段逐点核对一致后取 Sheet2；2025-10-01~09 两表均缺，按该月其余日同时刻均值补齐。

输出（处理后输入/）：
  若羌节点_逐时_2507-2606.csv    8760 点逐时节点电价（元/kWh，不含杂费）
  若羌节点_月时profile_修正.csv   12×24 月×时均值（元/kWh，不含杂费）
用法：python mk_price.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import INP, RAW                              # noqa: E402
SRC = os.path.join(RAW, "若羌2507-2606节点电价.xlsx")
WIN = ("2025-07-01", "2026-06-30 23:00")           # 文件名 2507-2606 即此窗口


def load_sheet1():
    """15 min 现货出清价（发电侧，若羌一期光伏电站节点），标签为区间结束。"""
    d = pd.read_excel(SRC, "Sheet1")
    d.columns = ["unit", "date", "time", "p"]
    d = d[d["unit"] != "平均"].copy()               # "平均"行与本单元逐点相同，去重
    d["date"] = pd.to_datetime(d["date"])
    lab = d["time"].astype(str)
    end = d["date"] + pd.to_timedelta(lab.str.replace("24:00", "00:00") + ":00") \
        + pd.to_timedelta(np.where(lab == "24:00", 1, 0), "D")
    d["st"] = end - pd.Timedelta("15min")           # 区间起点
    return d.groupby(d["st"].dt.floor("h"))["p"].mean()


def load_sheet2():
    """小时日前均价。时点列两种写法都要认。"""
    d = pd.read_excel(SRC, "Sheet2")
    d.columns = ["date", "h", "pmwh", "pkwh"]
    d["date"] = pd.to_datetime(d["date"])
    lab = d["h"].astype(str)
    isN = lab.str.endswith("时")
    hour = np.where(isN, pd.to_numeric(lab.str.rstrip("时"), errors="coerce"),
                    pd.to_numeric(d["h"], errors="coerce") - 1)
    d["ts"] = d["date"] + pd.to_timedelta(hour, unit="h")
    return d.set_index("ts")["pmwh"].sort_index(), int(isN.sum())


def main():
    if "--tou" in sys.argv:
        import csv
        from datetime import datetime, timedelta
        dst = os.path.join(INP, "储+氢_分时电价_2026.csv")
        start = datetime(2026, 1, 1)
        with open(dst, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["时间", "电价元每kWh", "时段"])
            for i in range(8760):
                dt = start + timedelta(hours=i)
                if 11 <= dt.hour < 18:
                    period, price = "谷", 0.11
                elif 8 <= dt.hour < 10 or 19 <= dt.hour < 24:
                    period, price = "峰", 0.45
                else:
                    period, price = "平", 0.28
                writer.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), "%.2f" % price, period])
        print("分时电价：%s；8760 小时，全年采用同一峰平谷划分" % dst)
        return
    global np, pd
    import numpy as np
    import pandas as pd
    h1 = load_sheet1()
    s2, n_new = load_sheet2()
    ov = s2.index.intersection(h1.index)
    diff = (s2.reindex(ov) - h1.reindex(ov)).abs()
    print("【口径校验】Sheet2 与 Sheet1 小时聚合重叠 %d 点，最大差 %.4f 元/MWh（两种时点写法各 %d/%d 行）"
          % (diff.notna().sum(), diff.max(), n_new, len(s2) - n_new))
    assert diff.max() < 0.01, "两表口径对不上，先查时点定义"

    full = pd.date_range(*WIN, freq="h")
    s = s2.reindex(full) / 1000.0
    miss = s.isna()
    print("【缺口】窗口 %s~%s 共 %d 点，缺 %d 点：%s"
          % (WIN[0], WIN[1][:10], len(full), int(miss.sum()),
             sorted({str(d) for d in full[miss].date}) if miss.any() else "无"))
    if miss.any():                                  # 用该月其余日的同时刻均值回填
        tmp = pd.DataFrame({"p": s.values}, index=full)
        fill = tmp.groupby([tmp.index.month, tmp.index.hour])["p"].transform("mean")
        s = s.fillna(pd.Series(fill.values, index=full))
    assert s.notna().all()

    s.index.name = "时间"
    s.to_csv(os.path.join(INP, "若羌节点_逐时_2507-2606.csv"), header=["节点电价元每kWh"],
             encoding="utf-8-sig", float_format="%.6f")
    df = pd.DataFrame({"p": s.values}, index=full)
    prof = df.groupby([df.index.month, df.index.hour])["p"].mean().unstack()
    prof.index.name = "mo"
    prof.to_csv(os.path.join(INP, "若羌节点_月时profile_修正.csv"), encoding="utf-8-sig",
                float_format="%.6f")

    print("\n【QC】均值 %.4f，min %.4f，max %.4f，地板(40 元/MWh)占比 %.1f%%"
          % (s.mean(), s.min(), s.max(), (s <= 0.0401).mean() * 100))
    mo = df.groupby(df.index.month)["p"].agg(["mean", "min", "max"])
    mo["地板占比%"] = df.groupby(df.index.month)["p"].apply(lambda v: (v <= 0.0401).mean() * 100)
    print(mo.round(4).to_string())
    g = df.groupby(df.index.date)["p"]
    top = g.apply(lambda v: np.sort(v.values)[-4:].mean() - np.sort(v.values)[:4].mean())
    print("\n日内极差均值 %.4f、日 top4−bottom4 均价差 %.4f 元/kWh（= 4 h 储能单日套利空间上限）"
          % ((g.max() - g.min()).mean(), top.mean()))
    ph = np.array([prof.loc[t.month, t.hour] for t in full])
    print("同一年月×时平均后：日极差 %.4f、top4−bottom4 %.4f —— 平均化抹掉的部分正是储能收益来源"
          % (pd.Series(ph, full).groupby(full.date).agg(lambda v: v.max() - v.min()).mean(),
             pd.Series(ph, full).groupby(full.date).apply(
                 lambda v: np.sort(v.values)[-4:].mean() - np.sort(v.values)[:4].mean()).mean()))


if __name__ == "__main__":
    main()
