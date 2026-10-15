# -*- coding: utf-8 -*-
"""轮台绿电制氢项目定容测算：全流程。

在 模型/ 目录下运行：
    python run_all.py               全流程；优化缓存中已有结果的算例直接读取
    python run_all.py --rerun       不读优化缓存，全部重新计算
    python run_all.py --only doc    只运行其中一步：clean / check / opt / dispatch / doc
"""
import os
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CACHE                                 # noqa: E402

RERUN = "--rerun" in sys.argv
BASE_ENV = {"PVMODE": "trk", "GP": "real", "PCS": "1", "PYTHONIOENCODING": "utf-8"}
# 推荐配置，依次为光伏 kWp、申报需量 kW、电解槽 Nm³/h、储氢罐 Nm³、增压机 Nm³/h、2 小时储能 kWh、4 小时储能 kWh：
# 光伏 1170MWp，申报需量 366MW，电解槽 124000Nm³/h，储氢罐 53 台（单台可用储氢量 1985.2kg），
# 增压机 6000kg/h，2 小时储能 40MWh，4 小时储能 500MWh
XREC = "1170000,366000,124000,1170613,66755.4,40000,500000"


def run(desc, args, extra=None):
    env = dict(os.environ, **BASE_ENV)
    env.update(extra or {})
    print("\n" + "=" * 78)
    print("%s：python %s" % (desc, " ".join(args)))
    print("=" * 78, flush=True)
    t0 = time.time()
    r = subprocess.run([sys.executable] + args, cwd=HERE, env=env)
    if r.returncode != 0:
        raise SystemExit("%s 失败（返回码 %d）" % (desc, r.returncode))
    print("%s 完成，用时 %.0f 秒" % (desc, time.time() - t0), flush=True)


def use_cache(desc, name):
    if not RERUN and os.path.exists(os.path.join(CACHE, name)):
        print("\n%s：读取优化缓存 %s" % (desc, name), flush=True)
        return True
    return False


def capacity(tag):
    """已完成算例的容量：光伏、申报需量、电解槽、储氢罐、增压机、2 小时储能、4 小时储能。"""
    return np.load(os.path.join(CACHE, "_bd10y_%s_容量.npy" % tag), allow_pickle=True).item()["x"]


def joined(x):
    return ",".join(repr(float(v)) for v in x)


def bd10y(desc, tag, extra):
    if not use_cache(desc, "_bd10y_%s_容量.npy" % tag):
        run(desc, ["bd10y.py", "10", "3,6,9"], extra)


def opt():
    bd10y("十年联合定容：基准", "trk", {"WORKERS": "5", "PROBE": "1"})
    bd10y("十年联合定容：不配置储能", "trk_nobatt", {"NOBATT": "1", "WORKERS": "5", "PROBE": "1"})
    x_base, x_nob = capacity("trk"), capacity("trk_nobatt")
    # 储能造价上浮：从不配置储能的最优点起步，预载前两个算例的割平面（割平面只刻画运行费，与造价无关）
    cuts = ";".join(os.path.join(CACHE, "_bd10y_%s_割平面.npy" % t) for t in ("trk", "trk_nobatt"))
    for bx in ("1.1", "1.2", "1.3"):
        bd10y("储能造价上浮至基准的 %s 倍" % bx, "trk_bx" + bx,
              {"BATTX": bx, "WORKERS": "10", "PROBE": "0", "RHO0": "0.05", "GAP": "1e-4",
               "PENCUT": "1.25", "CGMIN": "364000", "CUTS": cuts, "X0": joined(x_nob)})
    # 申报需量不可削减：锁定为不配置储能方案的申报需量，从基准最优点起步
    bd10y("申报需量不可削减", "trk_cgfix",
          {"CGFIX": repr(float(x_nob[1])), "WORKERS": "5", "PROBE": "0", "RHO0": "0.2", "GAP": "1e-4",
           "X0": joined(x_base)})


def dispatch():
    if not use_cache("推荐配置十年逐时调度", "_dispatch_10y_trk_推荐.npz"):
        run("推荐配置十年逐时调度", ["dispatch_10y.py"], {"EPSREG": "1e-5", "XREC": XREC, "TAGOUT": "_推荐"})


def clean():
    run("光伏逐时出力系数", ["mk_wp_ninja.py"])
    run("储+氢分时电价", ["mk_price.py", "--tou"])


def check():
    run("模型校核：与《绿电直连定容测算》框架单年算例对比", ["lp_multi.py", "check"])


def doc():
    run("系统构成图、求解流程图", ["fig_schematic.py"])
    run("典型工况图", ["fig_typical.py"])
    run("十年逐时调度底稿", ["mk_dispatch_xlsx.py"])
    run("定容测算报告", ["mk_report_docx.py"])


STEPS = dict(clean=clean, check=check, opt=opt, dispatch=dispatch, doc=doc)


def main():
    steps = [sys.argv[sys.argv.index("--only") + 1]] if "--only" in sys.argv else list(STEPS)
    t0 = time.time()
    for s in steps:
        STEPS[s]()
    print("\n全部完成，用时 %.1f 分钟。" % ((time.time() - t0) / 60))


if __name__ == "__main__":
    main()
