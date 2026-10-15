"""固定现有容量、冷启动复核储能与年度绿电约束；不覆盖定容缓存。"""
import os
os.environ.update(PVMODE="trk", GP="real", PCS="1", EPSREG="0")
import json
import numpy as np
import highspy
import lp_multi as M
import bd10y as BD
from paths import CACHE


def solve(x, disabled, yi=9):
    lp, meta = M.build_lp(*BD.year_inputs(M, yi, [2, 5, 8], 10), 1,
                         [M.BATT_2H, M.BATT_4H], fixcap=BD.fixvals(x), sub=True, log=False)
    if disabled:
        lower, upper = list(lp.row_lower_), list(lp.row_upper_)
        lower[-1], upper[-1] = -highspy.kHighsInf, highspy.kHighsInf
        lp.row_lower_, lp.row_upper_ = lower, upper
    h = highspy.Highs()
    M.solver_options(h, log=False, time_limit=180)
    h.setOptionValue("threads", 1)
    h.passModel(lp)
    h.run()
    status = h.modelStatusToString(h.getModelStatus())
    if status != "Optimal":
        raise RuntimeError(status)
    sol = h.getSolution()
    v = np.asarray(sol.col_value)
    d = np.asarray(sol.col_dual)
    B, I = meta['B'], meta['I']
    def total(k):
        return float(v[B[k]:B[k] + 8760].sum())
    return dict(cost=h.getInfo().objective_function_value, status=status,
                charge_kWh=total('inj_2h') + total('inj_4h'),
                discharge_kWh=total('rel_2h') + total('rel_4h'),
                green_slack_kWh=float(v[I['sg0']]),
                over_kWh=total('ov'),
                dual_E4=float(d[I['E_4h']] + d[I['P_4h']] / 4))


if __name__ == '__main__':
    saved = np.load(os.path.join(CACHE, '_bd10y_trk_容量.npy'), allow_pickle=True).item()
    x = saved['x'].copy()
    x[1] *= 1.002
    results = []
    for disabled in (False, True):
        for add in (0, 1, 50000):
            trial = x.copy()
            trial[6] += add
            r = dict(disable_green=disabled, added_E4_kWh=add, **solve(trial, disabled))
            results.append(r)
            print(json.dumps(r), flush=True)
    with open(os.path.join(CACHE, '储能经济性约束复核.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
