"""
12 · Бот RBV для пропа (NextLevelFunded Instant Flex 10k) — проверка правил на истории.
Запуск из корня репозитория:  python models/12-rbv-prop-bot/prop_sim.py

Что считает:
    1. RBV в версии для MT5 (без фильтров объёма — у CFD только тиковый объём) при издержках 0.4 / 0.6 / 0.8 б.п.
    2. Счёт Instant Flex: статичная просадка 4% (счёт закрыт на $9 600), бот сам останавливается на дневном убытке 1%
       и дневной прибыли 1% (правило консистентности 15%), ≤ 10 сделок в день. Старт с каждого дня истории:
       доля стартов, где счёт дошёл до +4% раньше, чем до −4%, при разном риске на сделку.
    3. То же для серии с вычтенным средним (стратегия без преимущества) — с чем сравнивать.
Старты пересекаются и истории всего ~7 месяцев: проценты — грубая оценка, а не вероятность.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np  # noqa: E402

from qm import ict  # noqa: E402
from qm.intraday import klines, ny  # noqa: E402
from qm.setups import RBVid, bot, drive, pick  # noqa: E402


def by_day(trades):
    """Сделки (время, R) → список торговых дней CME со списком результатов."""
    keys = ny(np.array([t for t, _ in trades]) + 6 * 3_600_000)[0]
    days, cur, last = [], [], None
    for (t, r), k in zip(trades, keys):
        if k != last and cur:
            days.append(cur); cur = []
        last = k; cur.append(r)
    if cur:
        days.append(cur)
    return days


def account(days, risk, target=0.04, max_dd=0.04, day_stop=0.01, day_cap=0.01, max_trades=10):
    """Старт с каждого дня: дошли до +target раньше, чем до −max_dd (статичная просадка от старта)?"""
    passed = failed = 0
    med = []
    for s in range(len(days)):
        eq, n = 0.0, 0
        for d in days[s:]:
            n += 1
            day = 0.0
            for r in d[:max_trades]:
                day += r * risk
                if day <= -day_stop or day >= day_cap:              # бот закрывает всё и ждёт следующего дня
                    break
            eq += day
            if eq <= -max_dd:
                failed += 1; break
            if eq >= target:
                passed += 1; med.append(n); break
    done = passed + failed
    return (passed / done if done else None), (int(np.median(med)) if med else None), done


if __name__ == "__main__":
    ict.P["volZ"] = ict.P["obVolZ"] = -99                               # как в MT5: без фильтров объёма
    trades = []
    for sym in ["QQQ", "SPY"]:
        k15, k5 = klines(sym, "15m"), klines(sym, "5m")
        st = RBVid(k15, k5)
        drive(k15, k5, [st])
        for x in st.out:
            x["sym"] = sym
        trades += bot(pick(st.out))
    gross = np.array([x["res"] + 0.4e-4 * x["entry"] / x["R"] for x in trades])   # результат без издержек
    cost1bp = np.array([1e-4 * x["entry"] / x["R"] for x in trades])              # 1 б.п. в долях R
    print(f"RBV (версия для MT5): {len(trades)} сделок; 1 б.п. издержек = {cost1bp.mean():.3f}R на сделку")
    for bp in (0.4, 0.6, 0.8):
        net = gross - bp * cost1bp
        print(f"  издержки {bp} б.п.: {net.mean():+.3f}R  t {net.mean() / (net.std(ddof=1) / np.sqrt(len(net))):+.1f}")

    net = gross - 0.6 * cost1bp
    series = sorted(zip([x["fillT"] for x in trades], net))
    days, zero = by_day(series), by_day([(t, r - net.mean()) for t, r in series])
    streak = best = 0
    for _, r in series:                                              # по времени, а не «сначала NQ, потом ES»
        streak = streak + 1 if r < -0.25 else 0; best = max(best, streak)
    print(f"\nInstant Flex 10k при 0.6 б.п.: самая длинная серия стопов {best}")
    for risk in (0.0015, 0.002, 0.0025, 0.0035, 0.005):
        p, med, n = account(days, risk)
        z, _, _ = account(zero, risk)
        print(f"  риск {risk:.2%}: +4% раньше −4% в {p:.0%} из {n} стартов (без преимущества {z:.0%}), медиана {med} торговых дней")
