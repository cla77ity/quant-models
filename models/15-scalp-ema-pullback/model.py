"""
15 · Скальпинг: откат к EMA по тренду.   Запуск:  python models/15-scalp-ema-pullback/model.py

Тренд — на 5-минутках: EMA20 выше EMA50 (по закрытым 5m-свечам) → только лонги, ниже → только шорты.
Вход — на минутках: предыдущая минутка коснулась EMA20(1m), текущая закрылась за хаем (лоем) предыдущей и по ту
сторону EMA20 → вход на закрытии. Стоп за экстремумом двух свечей отката − 0.1 ATR, цель 1.5R, выход через 30 минут,
сигналы с 9:45 до 15:30 NY. Общие правила скальпа — в qm/scalp.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np  # noqa: E402

from qm.scalp import ema, load, report  # noqa: E402


def trend_5m(D):
    """Тренд по закрытым 5m-свечам, собранным из минуток: +1 / −1 / 0 на каждую минутку (без заглядывания вперёд)."""
    t, c = D["t"], D["c"]
    bucket = t // 300_000
    closes, idx = [], []                                      # закрытие каждой 5m-свечи и минутка, когда оно стало известно
    for i in range(1, len(t)):
        if bucket[i] != bucket[i - 1]:
            closes.append(c[i - 1]); idx.append(i)            # 5m-свеча закрылась — известно с минутки i
    e20, e50 = ema(np.array(closes), 20), ema(np.array(closes), 50)
    tr = np.zeros(len(t))
    for n, i in enumerate(idx):
        if n >= 50:
            tr[i:] = 1 if e20[n] > e50[n] else -1
    return tr


def signals(D, n_ema=20, T=1.5, max_bars=30):
    if "_tr" not in D:
        D["_tr"] = trend_5m(D)
    tr, h, l, c, atr, mins = D["_tr"], D["h"], D["l"], D["c"], D["atr"], D["min"]
    e = ema(c, n_ema)
    out = []
    for i in range(2, len(c)):
        if not 585 <= mins[i] <= 930 or np.isnan(atr[i]) or tr[i] == 0:
            continue
        if tr[i] > 0 and l[i - 1] <= e[i - 1] and c[i] > h[i - 1] and c[i] > e[i]:
            stop = min(l[i - 1], l[i]) - 0.1 * atr[i]
            out.append(dict(i=i, dir=1, stop=stop, target=c[i] + T * (c[i] - stop), max_bars=max_bars))
        elif tr[i] < 0 and h[i - 1] >= e[i - 1] and c[i] < l[i - 1] and c[i] < e[i]:
            stop = max(h[i - 1], h[i]) + 0.1 * atr[i]
            out.append(dict(i=i, dir=-1, stop=stop, target=c[i] - T * (stop - c[i]), max_bars=max_bars))
    return out


if __name__ == "__main__":
    data = {s: load(s) for s in ["QQQ", "SPY"]}
    report("15 · Откат к EMA20 по тренду 5m (NQ/ES, минутки)", data, signals,
           [("EMA9 вместо EMA20", lambda D: signals(D, n_ema=9)),
            ("EMA50", lambda D: signals(D, n_ema=50)),
            ("цель 2R (база 1.5R)", lambda D: signals(D, T=2.0)),
            ("выход через 15 мин (база 30)", lambda D: signals(D, max_bars=15))])
