"""
Model E на Python — risk parity + SMA200 по индексам и металлам для CFD-брокера.

Запуск из корня репозитория:   python models/11-model-e/model_e.py

Что делает скрипт:
    1. Собирает данные по 9 рынкам (ETF с дивидендами как приближение CFD).
    2. Прогоняет модель при трёх уровнях платы брокеру: 0 (как фьючерсы), 2.5% (допущение), реальные свопы HeroFX.
    3. Печатает метрики риска за разработку (2007–2016), проверку (2017–2026) и весь период + хвостовой риск и Монте-Карло.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # чтобы найти пакет qm в корне репозитория
import numpy as np

from qm import risk  # noqa: E402  (импорт qm включает UTF-8 вывод)
from qm.portfolio import Asset, build_data, backtest, metrics, first_trading_day, mask

# --- рынки: ETF с полной доходностью вместо CFD (cost — спред в б.п.)
def F(key, sym, cost, cls):
    return Asset(key=key, sym=sym, cost=cost, fund=True, markup=0.025, rp=True, cls=cls, max_move=0.3)

ASSETS = [F("US500", "SPY", 1, "eq"), F("US100", "QQQ", 1, "eq"), F("US30", "DIA", 1.5, "eq"), F("US2000", "IWM", 1.5, "eq"),
          F("GER40", "EWG", 1.5, "eq"), F("JP225", "EWJ", 1.5, "eq"), F("UK100", "EWU", 1.5, "eq"),
          F("XAU", "GLD", 3, "metal"), F("XAG", "SLV", 5, "metal")]

# --- реальные свопы HeroFX по лонгу (выгрузил советник 10.11.2026): своп в год минус ставка ЦБ = наценка брокера
HEROFX_MARKUP = {"US500": 0.048, "US100": 0.042, "US30": 0.032, "US2000": 0.079, "GER40": 0.025, "JP225": 0.021,
                 "UK100": 0.044, "XAU": 0.069, "XAG": 0.082}

# --- правила модели E: акции и металлы получают равный риск (7 индексов делят долю «акций», 2 металла — долю «металлов»)
counts = {"eq": 7, "metal": 2}
WTS = {a.key: 1 / counts[a.cls] for a in ASSETS}
RULES = dict(strategy="rpsma", wts=WTS, rebal=1, cov_len=60, band=0.25)


def run(D, label, **cost):
    bt = backtest(D, **RULES, **cost)
    start = first_trading_day(D)
    dev, test, full = metrics(D, bt["ret"], start, "2017-01-01"), metrics(D, bt["ret"], "2017-01-01"), metrics(D, bt["ret"], start)
    print(f"  {label:<34} разработка {dev['sharpe']:.2f} | проверка {test['sharpe']:.2f} | весь период: {risk.fmt(full)}")
    return bt, start


if __name__ == "__main__":
    print("Загрузка данных (кэш data-cache/) ...")
    D = build_data(ASSETS, "2004-01-01")
    print(f"Данные {D['dates'][0]} → {D['dates'][-1]}, все рынки торгуются с {first_trading_day(D)}\n")

    print("Sharpe и риск модели E:")
    run(D, "без наценки (фьючерсы / ETF)", markup=0)
    run(D, "наценка 2.5% (допущение шага 13)")
    import copy
    D_hero = {**D, "assets": [copy.replace(a, markup=HEROFX_MARKUP[a.key]) for a in ASSETS]}
    bt, start = run(D_hero, "реальные свопы HeroFX")

    # хвостовой риск и Монте-Карло для варианта с реальными свопами
    idx = [t for t, d in enumerate(D["dates"]) if d >= start]
    r = bt["ret"][idx]
    dds = risk.bootstrap_drawdowns(r, runs=1000)
    print(f"\nРеальные свопы HeroFX, хвосты: худший день {r.min():.2%}, VaR99 {risk.var_historical(r):.2%} "
          f"(по Гауссу {risk.var_parametric(r):.2%}), CVaR99 {risk.cvar_historical(r):.2%}")
    print(f"Монте-Карло (1000 перестановок блоками по 20 дней): просадка медиана {np.median(dds):.1%}, худшие 5% {np.quantile(dds, 0.05):.1%}")
    last_date, w = bt["w_hist"][-1]
    print(f"\nВеса модели на {last_date}: " + "  ".join(f"{a.key} {x:.3f}" for a, x in zip(ASSETS, w)))
