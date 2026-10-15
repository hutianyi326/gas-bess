# -*- coding: utf-8 -*-
"""从 renewables.ninja 取轮台站址的风光小时出力（MERRA-2 再分析）。

token 从环境变量 NINJA_TOKEN 读取。

带 token 的限频是 50 次/小时，一年一次请求，逐年落盘，重跑不重复请求。
MERRA-2 网格约 0.5°×0.625°，县域内选点差异落在同一格内，取轮台县城坐标。
接口时间戳是 UTC，清洗时转北京时（新疆结算亦按北京时）。
"""
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import NINJA as RAW                          # noqa: E402

TOKEN = os.environ.get("NINJA_TOKEN")
if not TOKEN:
    raise SystemExit("请先设置环境变量 NINJA_TOKEN（renewables.ninja 账号 token）")
os.makedirs(RAW, exist_ok=True)

LAT, LON = 41.7766, 84.2504      # 轮台县城；拉依苏在同一 MERRA-2 网格内
YEARS = [int(y) for y in (sys.argv[1:] or range(2014, 2024))]
BASE = "https://www.renewables.ninja/api/data/"

JOBS = {
    "pv_fix": ("pv", dict(dataset="merra2", capacity=1, system_loss=0.1,
                          tracking=0, tilt=35, azim=180)),
    "pv_trk": ("pv", dict(dataset="merra2", capacity=1, system_loss=0.1,
                          tracking=1, tilt=35, azim=180)),
    "wind": ("wind", dict(dataset="merra2", capacity=1, height=100,
                          turbine="Vestas V136 3450")),
}


def get(name, kind, params, year, tries=3):
    dst = os.path.join(RAW, "%s_%d.json" % (name, year))
    if os.path.exists(dst) and os.path.getsize(dst) > 100000:
        print("已存在，跳过 %s %d" % (name, year))
        return
    p = dict(params, lat=LAT, lon=LON, format="json",
             date_from="%d-01-01" % year, date_to="%d-12-31" % year)
    url = BASE + kind + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={
        "Authorization": "Token " + TOKEN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                body = r.read().decode("utf-8")
            d = json.loads(body)
            assert len(d["data"]) > 8000, "点数不足 %d" % len(d["data"])
            io.open(dst, "w", encoding="utf-8").write(body)
            print("已取 %s %d：%d 点" % (name, year, len(d["data"])))
            return
        except Exception as e:
            print("  %s %d 第 %d 次失败：%s" % (name, year, i + 1, e))
            if i < tries - 1:
                time.sleep(30)
    raise SystemExit("取数失败 %s %d" % (name, year))


for y in YEARS:
    for name, (kind, params) in JOBS.items():
        get(name, kind, params, y)
        time.sleep(2)
print("原始 JSON 位于", RAW)
