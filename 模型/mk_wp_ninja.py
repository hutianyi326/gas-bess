# -*- coding: utf-8 -*-
"""renewables.ninja 逐时出力 → 北京时逐时容量因子。

读 原始数据/renewables.ninja/{pv_fix,pv_trk,wind}_2014~2023.json（UTC 时间），
输出 处理后输入/轮台_风光小时出力_ninja.csv（北京时间；光伏固定 35°、光伏单轴跟踪、风电三列）。
单年文件转北京时间后从 1 月 1 日 08:00 开始，年初 8 小时由上一年文件补齐，故 2015 年起各年完整。
用法：python mk_wp_ninja.py
"""
import io
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import INP, NINJA                            # noqa: E402
YEARS = list(range(2014, 2024))       # 十年测算取 2019–2023 重复两遍
COLS = {"pv_fix": "光伏CF_固定35度", "pv_trk": "光伏CF_单轴跟踪", "wind": "风电CF"}


def load(name, y):
    p = os.path.join(NINJA, "%s_%d.json" % (name, y))
    d = json.load(io.open(p, encoding="utf-8"))["data"]
    s = pd.Series({pd.Timestamp(int(k), unit="ms", tz="UTC"): v["electricity"]
                   for k, v in d.items()}).sort_index()
    # 接口给 UTC，转北京时（新疆结算按北京时）
    return s.tz_convert("Asia/Shanghai").tz_localize(None)


frames = []
for y in YEARS:
    d = pd.DataFrame({c: load(n, y) for n, c in COLS.items()})
    frames.append(d)
r = pd.concat(frames).sort_index()
r = r[~r.index.duplicated()]
r.index.name = "时间"
dst = os.path.join(INP, "轮台_风光小时出力_ninja.csv")
r.to_csv(dst, encoding="utf-8-sig", float_format="%.5f")

print("站址 41.7766, 84.2504（轮台县城）  renewables.ninja / MERRA-2  北京时")
print("小时点 %d，%s 至 %s\n" % (len(r), r.index[0].date(), r.index[-1].date()))
y = r.groupby(r.index.year).sum().round(0)
y.columns = [c.replace("CF", "年利用小时") for c in y.columns]
print(y.to_string())
print("\n%d—%d 年均值：光伏固定 %.0f 小时、单轴跟踪 %.0f 小时、风电 %.0f 小时"
      % (YEARS[0], YEARS[-1], r["光伏CF_固定35度"].sum() / len(YEARS),
         r["光伏CF_单轴跟踪"].sum() / len(YEARS),
         r["风电CF"].sum() / len(YEARS)))
m = r.groupby(r.index.month).sum() / len(YEARS)
print("\n多年平均逐月利用小时：")
print(m.round(0).to_string())
print("\n冬夏比（7 月／12 月）：光伏 %.2f、风电 %.2f"
      % (m.iloc[6, 0] / m.iloc[11, 0], m.iloc[6, 2] / m.iloc[11, 2]))
