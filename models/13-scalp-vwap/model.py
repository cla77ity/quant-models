"""
13 · Скальпинг: возврат к VWAP.   Запуск из корня репозитория:  python models/13-scalp-vwap/model.py

Идея: VWAP (средняя цена сессии, взвешенная по объёму) — «справедливая цена» дня. Когда цена уходит далеко
за полосу VWAP ± 2σ и затем возвращается внутрь, ставим на продолжение возврата к самому VWAP.

Правила (заданы до теста):
    • VWAP и σ считаются с 9:30 NY по объёму минуток (σ — взвешенное отклонение типичной цены от VWAP);
    • сигналы с 10:00 (нужно 30 минут, чтобы VWAP устоялся) до 15:30;
    • минутка закрылась за полосой ±2σ, следующая закрылась обратно внутри → вход на её закрытии против выхода;
    • стоп — за крайней точкой выхода за полосу + 0.1 ATR(1m); цель — VWAP в момент входа; пропуск, если до
      цели меньше 1R; выход по времени через 60 минут. Общие правила скальпа — в qm/scalp.py.
Объём — у перпов QQQ/SPY (прокси NQ/ES): по форме дня он похож на биржевой, но это приближение.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np  # noqa: E402

from qm.scalp import load, report  # noqa: E402


def vwap_bands(D):
    """VWAP и σ с начала каждой сессии США (9:30–16:00); вне сессии — NaN."""
    tp = (D["h"] + D["l"] + D["c"]) / 3
    v, mins, dates = D["v"], D["min"], D["date"]
    vw, sd = np.full(len(tp), np.nan), np.full(len(tp), np.nan)
    pv = vv = pv2 = 0.0
    day = None
    for i in range(len(tp)):
        if not 570 <= mins[i] < 960:
            continue
        if dates[i] != day:
            day, pv, vv, pv2 = dates[i], 0.0, 0.0, 0.0
        pv += tp[i] * v[i]; vv += v[i]; pv2 += tp[i] * tp[i] * v[i]
        if vv > 0:
            m = pv / vv
            vw[i], sd[i] = m, np.sqrt(max(0.0, pv2 / vv - m * m))
    return vw, sd


def signals(D, k=2.0, min_T=1.0, max_bars=60):
    vw, sd = D.setdefault("_vwap", vwap_bands(D))
    h, l, c, atr, mins = D["h"], D["l"], D["c"], D["atr"], D["min"]
    out, run_ext, run_side = [], None, 0
    for i in range(1, len(c)):
        if np.isnan(vw[i]) or np.isnan(vw[i - 1]) or sd[i] <= 0:
            run_side = 0; continue
        up, dn = vw[i] + k * sd[i], vw[i] - k * sd[i]
        side = 1 if c[i] > up else -1 if c[i] < dn else 0
        if side != 0:                                          # цена за полосой: запоминаем крайнюю точку выхода
            run_ext = (max(run_ext, h[i]) if side == 1 else min(run_ext, l[i])) if side == run_side else (h[i] if side == 1 else l[i])
            run_side = side
            continue
        if run_side != 0 and 600 <= mins[i] <= 930 and not np.isnan(atr[i]):
            d = -run_side                                      # вернулись внутрь → против выхода
            stop = run_ext - d * 0.1 * atr[i]
            R = abs(c[i] - stop)
            if R > 0 and (vw[i] - c[i]) * d >= min_T * R:
                out.append(dict(i=i, dir=d, stop=stop, target=vw[i], max_bars=max_bars))
        run_side = 0
    return out


if __name__ == "__main__":
    data = {s: load(s) for s in ["QQQ", "SPY"]}
    report("13 · Возврат к VWAP (NQ/ES, минутки)", data, signals,
           [("полоса ±2.5σ вместо 2σ", lambda D: signals(D, k=2.5)),
            ("полоса ±1.5σ", lambda D: signals(D, k=1.5)),
            ("до цели ≥ 0.5R (база 1R)", lambda D: signals(D, min_T=0.5)),
            ("выход через 30 мин (база 60)", lambda D: signals(D, max_bars=30))])
