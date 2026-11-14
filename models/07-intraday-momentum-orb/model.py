"""
07 · Внутридневной моментум и пробой диапазона открытия (ORB).   Запуск:  python models/07-intraday-momentum-orb/model.py

A. Моментум (Gao, Han, Li, Zhou 2018): направление от вчерашнего закрытия до утра → сделка в последние 30 минут дня.
B. ORB: пробой диапазона первых N минут, стоп на другой стороне (или в середине), выход на закрытии.
Плюс проверки: случайное направление в тот же момент и симуляция проп-челленджа 50k (трейлинг $2 500, цель $3 000).

Данные: часовки Yahoo за ~3 года (QQQ/SPY = NQ/ES, IWM/DIA — контроль) и 5-минутки перпов Binance за ~7 месяцев.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np  # noqa: E402

from qm.intraday import Lcg, klines, ny, trade_stats, yahoo_intraday  # noqa: E402

COST = 0.00004   # издержки CME за круг ≈ 0.4 б.п. (~1 пункт NQ)


def sessions(t_ms, o, h, l, c):
    """Свечи → торговые дни США: {дата: {минута_дня: (o, h, l, c)}}, только 9:30–16:00 Нью-Йорка."""
    dates, mins, _ = ny(t_ms)
    days = {}
    for d, m, *bar in zip(dates, mins, o, h, l, c):
        if 570 <= m < 960:
            days.setdefault(d, {})[int(m)] = tuple(float(x) for x in bar)
    return [(d, days[d]) for d in sorted(days)]


# ---------------- A. моментум
def momentum(days, first_end, last_start, last_end, filt="base"):
    """first_end — минута свечи, чьё закрытие завершает «начало дня»; вход — закрытие последней свечи до last_start,
    выход — закрытие свечи last_end. filt: base | agree | strong | long | r12 (контрольные варианты)."""
    out, prev_close, hist = [], None, []
    for date, bars in days:
        fb, exit_bar = bars.get(first_end), bars.get(last_end)
        before = [m for m in bars if m < last_start]
        if prev_close is not None and fb and before and exit_bar:
            r1 = fb[3] / prev_close - 1                              # доходность от вчерашнего закрытия до утра
            entry = bars[max(before)][3]
            prev_hour = sorted(m for m in bars if last_start - 60 <= m < last_start)
            r12 = entry / bars[prev_hour[0]][0] - 1 if prev_hour else 0.0
            sd = np.sqrt(np.mean(np.square(hist[-20:]))) if len(hist) >= 20 else None
            d = np.sign(r1)
            if filt == "agree": d = np.sign(r1) if np.sign(r1) == np.sign(r12) else 0
            if filt == "strong": d = np.sign(r1) if sd is not None and abs(r1) > sd else 0
            if filt == "long": d = 1
            if filt == "r12": d = np.sign(r12)
            if d != 0:
                out.append({"date": date, "ret": d * (exit_bar[3] / entry - 1) - COST})
            hist.append(r1)
        if exit_bar:
            prev_close = exit_bar[3]
    return out


# ---------------- B. пробой диапазона открытия
def orb(days, or_end, bar, stop_at="other", target=None, colour=False):
    """or_end — первая минута после диапазона; bar — размер свечи; stop_at: other | mid; target в R (None — до закрытия);
    colour=True — вариант Zarattini: направление по цвету первой свечи, вход на открытии следующей."""
    out = []
    for date, bars in days:
        mins = sorted(bars)
        if not mins or mins[0] != 570 or mins[-1] < 960 - bar:
            continue
        rng = [bars[m] for m in mins if m < or_end]
        after = [m for m in mins if m >= or_end]
        H, L, last = max(b[1] for b in rng), min(b[2] for b in rng), bars[mins[-1]][3]
        if (H - L) / L < 0.0003:
            continue                                                   # «мёртвый» диапазон — нет осмысленного стопа
        if colour:
            o0, c0 = rng[0][0], rng[0][3]
            d = np.sign(c0 - o0)
            if not d:
                continue
            entry, stop, k0 = bars[after[0]][0], (L if d > 0 else H), 0
            if (entry - stop) * d <= 0:
                continue
        else:
            d = 0
            for k, m in enumerate(after):
                b = bars[m]
                up, dn = b[1] > H, b[2] < L
                if up and dn:
                    d, k0 = 2, k
                    break                                              # обе стороны в одной свече — считаем стопом
                if up or dn:
                    d, k0 = (1 if up else -1), k
                    entry = max(H, b[0]) if up else min(L, b[0])
                    break
            if not d:
                continue
            if d == 2:
                out.append({"date": date, "dir": 0, "R": -1.0, "ret": -(H - L) / ((H + L) / 2) - COST})
                continue
            stop = (H + L) / 2 if stop_at == "mid" else (L if d > 0 else H)
        R = abs(entry - stop)
        tgt = entry + d * target * R if target else None
        exit_px = last
        for k in range(k0, len(after)):
            b = bars[after[k]]
            if (b[2] <= stop) if d > 0 else (b[1] >= stop):
                exit_px = stop
                break
            if tgt is not None and ((b[1] >= tgt) if d > 0 else (b[2] <= tgt)) and not (not colour and k == k0):
                exit_px = tgt
                break
        out.append({"date": date, "dir": d, "entry": entry, "stop": stop,
                    "R": (exit_px - entry) * d / R - COST * entry / R, "ret": (exit_px - entry) * d / entry - COST})
    return out


def orb_random(days, or_end, colour=False, runs=200):
    """Тот же момент входа и тот же стоп, но направление — монетка. Средний R по каждому из `runs` прогонов."""
    rnd = Lcg(4242)
    real = [t for t in orb(days, or_end, 60, colour=colour) if t["dir"] != 0]
    by_date = dict(days)
    avgs = []
    for _ in range(runs):
        s = 0.0
        for t in real:
            bars = by_date[t["date"]]
            d = 1 if rnd() < 0.5 else -1
            R = abs(t["entry"] - t["stop"])
            stop = t["entry"] - d * R
            mins = [m for m in sorted(bars) if m >= or_end]
            exit_px, started = bars[mins[-1]][3], False
            for m in mins:
                b = bars[m]
                if not started:
                    if ((b[1] >= t["entry"]) if t["dir"] > 0 else (b[2] <= t["entry"])) or colour:
                        started = True
                    else:
                        continue
                if (b[2] <= stop) if d > 0 else (b[1] >= stop):
                    exit_px = stop
                    break
            s += (exit_px - t["entry"]) * d / R
        avgs.append(s / len(real))
    return sorted(avgs)


def prop_sim(rs, per_r=250):
    """Проп-челлендж с каждого дня истории: трейлинг-просадка $2 500 от максимума, цель +$3 000.
    Возвращает долю прохождений у стратегии и у той же серии с вычтенным средним (ноль преимущества)."""
    def run(arr):
        passed = failed = 0
        for s in range(len(arr)):
            bal = pk = 0.0
            for x in arr[s:]:
                bal += x * per_r
                pk = max(pk, bal)
                if bal >= 3000: passed += 1; break
                if bal <= pk - 2500: failed += 1; break
        return passed / (passed + failed) if passed + failed else None
    rs = np.asarray(rs)
    return run(rs), run(rs - rs.mean())


def line(name, trades, key):
    st = trade_stats([t[key] for t in trades])
    if not st:
        print(f"  {name} мало"); return st
    if key == "ret":
        print(f"  {name:<50} n={st['n']:>4} win {st['win']:.0f}%  {st['avg'] * 1e4:+.1f} б.п.  t {st['t']:+.1f}  Sharpe {st['avg'] / st['sd'] * np.sqrt(252):+.2f}")
    else:
        sr = trade_stats([t["ret"] for t in trades])
        print(f"  {name:<50} n={st['n']:>4} win {st['win']:.0f}%  {st['avg']:+.2f}R  t {st['t']:+.1f}  DD {st['dd']:.1f}R  Sharpe {sr['avg'] / sr['sd'] * np.sqrt(252):+.2f}")
    return st


if __name__ == "__main__":
    H1 = {s: sessions(*np.array(yahoo_intraday(s, "1h")).T) for s in ["QQQ", "SPY", "IWM", "DIA"]}
    P5 = {}
    for s in ["QQQ", "SPY"]:
        k = klines(s, "5m"); P5[s] = sessions(k["t"], k["o"], k["h"], k["l"], k["c"])
    print(f"1h: {H1['QQQ'][0][0]} → {H1['QQQ'][-1][0]}, {len(H1['QQQ'])} дней; 5m перпы: {len(P5['QQQ'])} дней")
    NAME = {"QQQ": "NQ (QQQ)", "SPY": "ES (SPY)", "IWM": "Russell (IWM)", "DIA": "Dow (DIA)"}

    print("\n== A. Моментум: последние 30 мин по направлению начала дня (1h, ~3 года)")
    mom = lambda days, f="base": momentum(days, 570, 930, 930, f)   # 1h: свеча 9:30 закрывается в 10:30; последняя — 15:30–16:00
    line("ОСНОВНАЯ: NQ+ES, первый час → последние 30 мин", sorted(mom(H1["QQQ"]) + mom(H1["SPY"]), key=lambda t: t["date"]), "ret")
    for s in ["QQQ", "SPY", "IWM", "DIA"]:
        line(f"  {NAME[s]}", mom(H1[s]), "ret")
    for f, nm in [("agree", "фильтр: час 14:30 согласен"), ("strong", "фильтр: сильное начало дня"), ("r12", "контроль: только 14:30–15:30"), ("long", "контроль: всегда лонг")]:
        line(nm, mom(H1["QQQ"], f) + mom(H1["SPY"], f), "ret")
    line("5m: 16:00 → 10:00 предсказывает 15:30 → 16:00", momentum(P5["QQQ"], 595, 930, 955) + momentum(P5["SPY"], 595, 930, 955), "ret")

    print("\n== B. ORB (стоп на другой стороне, выход на закрытии)")
    main_b = sorted(orb(H1["QQQ"], 630, 60) + orb(H1["SPY"], 630, 60), key=lambda t: t["date"])
    mb = line("ОСНОВНАЯ: ORB-60, NQ+ES", main_b, "R")
    for s in ["QQQ", "SPY", "IWM", "DIA"]:
        line(f"  {NAME[s]}", orb(H1[s], 630, 60), "R")
    line("ORB-60, стоп в середине", orb(H1["QQQ"], 630, 60, "mid") + orb(H1["SPY"], 630, 60, "mid"), "R")
    line("ORB-60, цель 2R", orb(H1["QQQ"], 630, 60, target=2) + orb(H1["SPY"], 630, 60, target=2), "R")
    for m in [5, 15, 30]:
        line(f"5m ORB-{m}, стоп на другой стороне", orb(P5["QQQ"], 570 + m, 5) + orb(P5["SPY"], 570 + m, 5), "R")
    line("5m ORB-5 по Zarattini (цель 10R)", orb(P5["QQQ"], 575, 5, target=10, colour=True) + orb(P5["SPY"], 575, 5, target=10, colour=True), "R")
    rb = sorted(orb_random(H1["QQQ"], 630) + orb_random(H1["SPY"], 630))
    print(f"  случайное направление (ORB-60): медиана {rb[len(rb) // 2]:+.2f}R, 95% {rb[int(len(rb) * 0.95)]:+.2f}R; модель {mb['avg']:+.2f}R")

    print("\n== Проп-челлендж 50k, только NQ, старт с каждого дня")
    a = mom(H1["QQQ"]); sd = np.sqrt(np.mean([t["ret"] ** 2 for t in a]))
    for name, rs in [("моментум (1σ = $X)", [t["ret"] / sd for t in a]), ("ORB-60 (риск $X)", [t["R"] for t in orb(H1["QQQ"], 630, 60)])]:
        for per in [250, 500]:
            p, z = prop_sim(rs, per)
            print(f"  {name.replace('$X', f'${per}'):<28} проходит {p:.0%}; без преимущества {z:.0%}")
