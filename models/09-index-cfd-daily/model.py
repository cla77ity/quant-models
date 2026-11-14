"""
09 · NQ и ES как дневные позиции на CFD-счёте.   Запуск:  python models/09-index-cfd-daily/model.py

Цены — непрерывные фьючерсы NQ=F / ES=F с 2000 года. CFD = фьючерс − наценка брокера 2.5% в год за удержание.
Сравниваем «купить и держать», фильтры тренда (SMA200 / SMA50), размер по волатильности и тренд лонг/шорт.
В скобках — те же стратегии на настоящих фьючерсах CME (без наценки): видно, сколько забирает CFD.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qm import risk  # noqa: E402
from qm.portfolio import backtest, build_data, mask, metrics  # noqa: E402
from qm.universe import FX, INDEX  # noqa: E402

STRATS = [
    ("Купить и держать (1x)", dict(strategy="long", fixed=True)),
    ("SMA200 (1x)", dict(strategy="sma", sma_n=200, fixed=True)),
    ("SMA50 (1x)", dict(strategy="sma", sma_n=50, fixed=True)),
    ("Лонг, таргет волатильности 15%", dict(strategy="long", target=0.15)),
    ("SMA200 + таргет 15%", dict(strategy="sma", sma_n=200, target=0.15)),
    ("Тренд лонг/шорт (15%)", dict(strategy="trend", target=0.15)),
    ("Тренд только лонг (15%)", dict(strategy="trendLO", target=0.15)),
]

if __name__ == "__main__":
    D = build_data(INDEX, "2000-01-01")
    start = D["dates"][260 + 252]
    print(f"NQ/ES: торговля с {start} по {D['dates'][-1]}")
    for key in ["NQ", "ES"]:
        print(f"\n== {key}: CFD с наценкой 2.5%  (в скобках — фьючерсы CME)")
        for name, kw in STRATS:
            cfd = metrics(D, backtest(D, only=key, rebal=1, **kw)["ret"], start)
            fut = metrics(D, backtest(D, only=key, rebal=1, markup=0, **kw)["ret"], start)
            print(f"  {name:<32} {cfd['cagr']:+.1%} ({fut['cagr']:+.1%})  Sharpe {cfd['sharpe']:.2f} ({fut['sharpe']:.2f})  просадка {cfd['max_dd']:.0%}")

    print("\n== Тренд-портфель 10% волатильности: помогают ли индексы валютному тренду?")
    DM = build_data(FX + INDEX)
    s = DM["dates"][260 + 252]
    for name, keys in [("валюты + золото", [a.key for a in FX]), ("валюты + золото + NQ + ES", [a.key for a in FX + INDEX]), ("только NQ + ES + золото", ["NQ", "ES", "XAU"])]:
        m = metrics(DM, backtest(mask(DM, keys), strategy="trend")["ret"], s)
        print(f"  {name:<28} {risk.fmt(m)}")
