"""
14 · Скальпинг: импульсная минутка — перереакция или продолжение?   Запуск:  python models/14-scalp-impulse/model.py

Импульс: минутка размером ≥ 3 ATR(1m) с телом ≥ 70% свечи, с 9:45 до 15:30 NY.
Две противоположные гипотезы (обе заданы до теста):
    ПРОТИВ   — рынок перереагировал: вход против импульса на закрытии, стоп за экстремумом свечи + 0.25 ATR, цель 1.5R;
    ПО       — импульс продолжится: вход по импульсу, стоп за серединой свечи − 0.1 ATR, цель 1.5R.
Выход по времени через 15 минут. Общие правила скальпа — в qm/scalp.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np  # noqa: E402

from qm.scalp import load, report  # noqa: E402


def signals(D, mode="fade", k=3.0, body=0.7, T=1.5, max_bars=15):
    o, h, l, c, atr, mins = D["o"], D["h"], D["l"], D["c"], D["atr"], D["min"]
    out = []
    for i in range(1, len(c)):
        a = atr[i - 1]                                         # ATR до этой свечи: сама свеча не должна раздувать порог
        rng = h[i] - l[i]
        if np.isnan(a) or not 585 <= mins[i] <= 930 or rng < k * a or abs(c[i] - o[i]) < body * rng:
            continue
        up = c[i] > o[i]
        if mode == "fade":
            d = -1 if up else 1
            stop = h[i] + 0.25 * a if up else l[i] - 0.25 * a
        else:
            d = 1 if up else -1
            stop = (h[i] + l[i]) / 2 - d * 0.1 * a
        R = abs(c[i] - stop)
        out.append(dict(i=i, dir=d, stop=stop, target=c[i] + d * T * R, max_bars=max_bars))
    return out


if __name__ == "__main__":
    data = {s: load(s) for s in ["QQQ", "SPY"]}
    for mode, nm in [("fade", "ПРОТИВ импульса (перереакция)"), ("follow", "ПО импульсу (продолжение)")]:
        report(f"14 · Импульсная минутка: {nm}", data, lambda D, m=mode: signals(D, m),
               [("импульс ≥ 4 ATR (база 3)", lambda D, m=mode: signals(D, m, k=4.0)),
                ("импульс ≥ 2 ATR", lambda D, m=mode: signals(D, m, k=2.0)),
                ("цель 1R (база 1.5R)", lambda D, m=mode: signals(D, m, T=1.0)),
                ("выход через 30 мин (база 15)", lambda D, m=mode: signals(D, m, max_bars=30))])
        print()
