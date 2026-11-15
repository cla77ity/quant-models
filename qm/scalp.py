"""
qm.scalp — симулятор скальп-сделок на минутках (модели 13–15).

Сделка: вход по рынку на закрытии сигнальной минутки, стоп и цель «скобкой», выход по времени.
Правила, общие для всех скальп-моделей (заданы заранее, совместимы с проп-фирмами):
    • только основная сессия США 9:30–16:00 NY, всё закрыто к 15:55;
    • минимальное удержание 2 минуты: цель не берётся раньше (правило двух минут), стоп работает всегда;
    • стоп и цель в одной минутке = стоп (порядок внутри свечи неизвестен — считаем худшее);
    • одна позиция на инструмент, не больше 10 сделок в день;
    • издержки в базисных пунктах цены (спред + комиссия) переводятся в доли R: у скальпинга стопы короткие,
      поэтому 1 б.п. может стоить четверть R и больше — это главный враг любой скальп-модели.
"""
import numpy as np

from .intraday import Lcg, klines, ny
from .ict import atr14


def load(sym):
    """Минутки перпа SYMUSDT (QQQ/SPY = прокси NQ/ES) + время NY + ATR14 на минутках."""
    k = klines(sym, "1m")
    d, m, w = ny(k["t"])
    atr = np.array([np.nan if x is None else x for x in atr14(k["h"], k["l"], k["c"])])
    return dict(sym=sym, t=k["t"], o=k["o"], h=k["h"], l=k["l"], c=k["c"], v=k["v"], date=d, min=m, atr=atr)


def ema(x, n):
    """Экспоненциальная средняя (как в любом терминале): каждое новое значение = α·цена + (1−α)·прошлое, α = 2/(n+1)."""
    out, a = np.empty(len(x)), 2 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def walk(D, i, d, stop, target, max_bars, min_hold=2):
    """Провести сделку от закрытия минутки i. Возвращает (результат в R без издержек, индекс выхода)."""
    h, l, c, mins = D["h"], D["l"], D["c"], D["min"]
    entry = c[i]
    R = abs(entry - stop)
    T = (target - entry) * d / R
    for k in range(i + 1, min(len(c), i + 1 + max_bars)):
        if (l[k] <= stop) if d > 0 else (h[k] >= stop):
            return -1.0, k
        if k - i >= min_hold and ((h[k] >= target) if d > 0 else (l[k] <= target)):
            return T, k
        if mins[k] >= 955:                                    # 15:55 NY — закрыть всё
            return (c[k] - entry) * d / R, k
    k = min(len(c) - 1, i + max_bars)
    return (c[k] - entry) * d / R, k


def simulate(D, signals, cost_bp, max_day=10):
    """signals — список dict(i, dir, stop, target, max_bars) по времени. Применяет правила аккаунта и издержки."""
    trades, busy, per_day = [], -1, {}
    for s in signals:
        i = s["i"]
        if i <= busy or per_day.get(D["date"][i], 0) >= max_day:
            continue
        entry = D["c"][i]
        R = abs(entry - s["stop"])
        if not R > 0:
            continue
        res, k = walk(D, i, s["dir"], s["stop"], s["target"], s["max_bars"])
        trades.append(dict(sym=D["sym"], i=i, t=int(D["t"][i]), dir=s["dir"], R=R, T=(s["target"] - entry) * s["dir"] / R,
                           max_bars=s["max_bars"], gross=res, res=res - cost_bp * 1e-4 * entry / R, cost=cost_bp * 1e-4 * entry / R))
        busy = k
        per_day[D["date"][i]] = per_day.get(D["date"][i], 0) + 1
    return trades


def random_direction(data, trades, runs=200, seed=2024):
    """Тот же момент входа, тот же стоп и та же цель в R, но направление — монетка (с теми же издержками).
    Если модель не лучше — её сигнал о НАПРАВЛЕНИИ ничего не стоит."""
    rnd = Lcg(seed)
    out = []
    for _ in range(runs):
        tot = 0.0
        for tq in trades:
            D = data[tq["sym"]]
            d = 1 if rnd() < 0.5 else -1
            entry = D["c"][tq["i"]]
            res, _ = walk(D, tq["i"], d, entry - d * tq["R"], entry + d * tq["T"] * tq["R"], tq["max_bars"])
            tot += res - tq["cost"]
        out.append(tot / len(trades))
    return sorted(out)


def report(name, data, make_signals, variants, costs=(0.0, 0.4, 0.6)):
    """Общая печать для скальп-моделей: основная версия при разных издержках, по инструментам, половины периода,
    случайное направление, устойчивость к параметрам."""
    from .setups import fmt, stat
    S = lambda lst: stat([x["res"] for x in lst])
    base = {c: sorted([t for D in data.values() for t in simulate(D, make_signals(D), c)], key=lambda x: x["t"]) for c in costs}
    print(f"== {name}")
    for c in costs:
        print(f"  издержки {c:.1f} б.п.{' (без издержек)' if c == 0 else ' (CME)' if c == 0.4 else ' (CFD)'}: {fmt(S(base[c]))}")
    tr = base[0.6]
    if tr:
        print(f"  стоимость 1 б.п. в R: медиана {np.median([x['cost'] / 0.6 for x in tr]):.2f}R на сделку")
        for sym in data:
            print(f"    {sym}: {fmt(S([x for x in tr if x['sym'] == sym]))}")
        rnd = random_direction(data, tr)
        a = S(tr)
        if a:
            print(f"  случайное направление (CFD 0.6 б.п.): медиана {rnd[len(rnd) // 2]:+.2f}R, 95% {rnd[int(len(rnd) * 0.95)]:+.2f}R → "
                  f"случай не хуже модели в {sum(x >= a['avgR'] for x in rnd) / len(rnd):.0%}")
    for vname, vmake in variants:
        v = sorted([t for D in data.values() for t in simulate(D, vmake(D), 0.6)], key=lambda x: x["t"])
        print(f"  {vname:<44} {fmt(S(v))}")
    return base
