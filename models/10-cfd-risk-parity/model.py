"""
10 · Портфельные стратегии на обычном CFD-счёте (21 рынок).   Запуск:  python models/10-cfd-risk-parity/model.py

Рынки: 7 индексов, облигации США, золото, серебро, нефть (CFD через ETF с дивидендами) + 9 валют.
Стратегии (риск 10% в год, ребаланс раз в неделю):
    Risk parity         — лонг всех не-валютных рынков, вес ∝ 1 / волатильность (равный вклад в риск)
    Risk parity + SMA200 — то же, но рынок ниже своей 200-дневной средней выключается
    Тренд лонг/шорт     — рецепт CTA-фондов по всем 21 рынку
    Risk parity + тренд — 50/50
Сравнение: US500 «купить и держать» и 60/40 (акции / облигации).
"""
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qm import risk  # noqa: E402
from qm.portfolio import backtest, build_data, first_trading_day, mask, metrics  # noqa: E402
from qm.universe import FUNDS, FX  # noqa: E402

FX_CFD = [replace(a, markup=0.01) for a in FX if a.key != "XAU"]   # золото уже есть среди FUNDS (через GLD)
ALL = FUNDS + FX_CFD

if __name__ == "__main__":
    D = build_data(ALL, "2004-01-01")
    start, split = first_trading_day(D), "2016-01-01"
    no_bonds = mask(D, [a.key for a in ALL if a.key not in ("UST10", "UST30")])
    print(f"{start} → {D['dates'][-1]}: CFD (ставка + наценка + спред)\n")

    S = [("Risk parity", D, dict(strategy="rp")), ("Risk parity + SMA200", D, dict(strategy="rpsma")),
         ("Тренд лонг/шорт, 21 рынок", D, dict(strategy="trend")), ("Risk parity + тренд 50/50", D, dict(strategy="rptrend")),
         ("US500: купить и держать", D, dict(strategy="weights", weights={"US500": 1.0}, fixed=True)),
         ("60/40: US500 + US 10Y", D, dict(strategy="weights", weights={"US500": 0.6, "UST10": 0.4}, fixed=True))]
    rets = {}
    for name, data, kw in S:
        r = backtest(data, **kw)["ret"]
        rets[name] = r
        a, b = metrics(D, r, start, split), metrics(D, r, split)
        print(f"  {name:<28} {risk.fmt(metrics(D, r, start))}  до/после 2016: {a['sharpe']:.2f}/{b['sharpe']:.2f}")

    print("\nУстойчивость (Sharpe):")
    for nm, st in [("Risk parity", "rp"), ("Risk parity + SMA200", "rpsma"), ("Risk parity + тренд", "rptrend")]:
        cells = [("без облигаций", backtest(no_bonds, strategy=st)), ("без наценки", backtest(D, strategy=st, markup=0)),
                 ("наценка ×2", backtest(D, strategy=st, markup_mult=2)), ("ребаланс раз в месяц", backtest(D, strategy=st, rebal=21))]
        print(f"  {nm:<22} " + "   ".join(f"{c}: {metrics(D, bt['ret'], start)['sharpe']:.2f}" for c, bt in cells))
