"""
08 · FX trend + carry на Python.   Запуск из корня репозитория:  python models/08-fx-trend-carry/model.py

Две классические стратегии валютных фондов на 9 парах против доллара + золото, дневки с 2004 года:
    ТРЕНД  — по каждому рынку среднее знаков доходности за 1, 3 и 12 месяцев (−1 … +1)
    КЕРРИ  — лонг 3 валют с самой высокой ставкой, шорт 3 с самой низкой (получаем разницу ставок)
Розничные условия: спред 1–5 б.п., брокер берёт 1% в год за перенос любой позиции, портфель на 10% волатильности.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np

from qm import risk  # noqa: E402
from qm.portfolio import backtest, build_data, metrics  # noqa: E402
from qm.universe import FX  # noqa: E402

if __name__ == "__main__":
    D = build_data(FX)
    dates, T = D["dates"], len(D["dates"])
    start = dates[260 + 252]                 # нужен год истории для 12-месячного тренда + год на оценку риска
    print(f"{start} → {dates[-1]} (розничные издержки, наценка 1%/год)\n")

    S = {
        "Тренд": backtest(D, strategy="trend")["ret"],
        "Керри": backtest(D, strategy="carry")["ret"],
        "Тренд + керри": backtest(D, strategy="combo")["ret"],
        "Золото: купить и держать": backtest(D, strategy="weights", weights={"XAU": 1.0}, fixed=True)["ret"],
    }
    # EUR/USD «купить и держать»: курс + разница ставок EUR−USD − 1% в год брокеру
    eur = np.zeros(T)
    eur[1:] = D["R"][0, 1:] + D["carry"][0, 1:] - 0.01 / 252
    S["EUR/USD: купить и держать"] = eur

    for name, r in S.items():
        print(f"  {name:<26} {risk.fmt(metrics(D, r, start))}")

    print("\nПоловины (до и после 2015), Sharpe:")
    for name in ["Тренд", "Керри", "Тренд + керри"]:
        a, b = metrics(D, S[name], start, "2015-01-01"), metrics(D, S[name], "2015-01-01")
        print(f"  {name:<16} {a['sharpe']:.2f} → {b['sharpe']:.2f}")

    print("\nКризисы (доходность за период):")
    crises = [("Lehman, сен–дек 2008", "2008-09-01", "2009-01-01"), ("COVID, фев–апр 2020", "2020-02-15", "2020-05-01"),
              ("Инфляция, 2022", "2022-01-01", "2023-01-01"), ("Развал керри в иене, июль–авг 2024", "2024-07-01", "2024-08-31")]
    for nm, a, b in crises:
        idx = [t for t, d in enumerate(dates) if a <= d < b]
        print(f"  {nm:<36} " + "   ".join(f"{k.split(':')[0]} {risk.equity_curve(S[k][idx])[-1] - 1:+.1%}" for k in ["Тренд", "Керри", "Золото: купить и держать"]))

    print("\nУстойчивость «тренд + керри» (Sharpe):")
    for nm, kw in [("без наценки брокера", dict(markup=0)), ("наценка 2%", dict(markup=0.02)), ("издержки ×2", dict(cost_mult=2)),
                   ("ребаланс каждый день", dict(rebal=1)), ("ребаланс раз в месяц", dict(rebal=21)), ("тренд только 12 мес.", dict(horizons=(252,))),
                   ("керри 2 / 2", dict(n_carry=2)), ("цель волатильности 15%", dict(target=0.15))]:
        print(f"  {nm:<26} {metrics(D, backtest(D, strategy='combo', **kw)['ret'], start)['sharpe']:.2f}")
