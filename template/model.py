"""
КАРКАС МОДЕЛИ — шаблон, с которого начинается любая новая модель.   Запуск:  python template/model.py

Пример внутри — самая простая модель тренда: держать S&P 500 (SPY), когда цена выше 200-дневной средней, иначе кэш.
Это НЕ рабочая стратегия для торговли, а учебный пример, на котором видно все 7 шагов.
Чтобы сделать свою модель: скопируй папку в models/NN-название/, поменяй ПРАВИЛА (шаг 0) и СИГНАЛ (шаг 3).

    0. Правила — записаны ДО теста          4. P&L с издержками
    1. Данные                                5. Метрики риска
    2. Проверка данных                       6. Проверки на честность (половины, издержки ×2, случайность, Монте-Карло)
    3. Сигнал (без заглядывания в будущее)   7. Вывод
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # чтобы найти пакет qm в корне репозитория
import numpy as np  # noqa: E402

from qm import risk  # noqa: E402
from qm.data import yahoo_daily  # noqa: E402

# ---------------------------------------------------------------- 0. ПРАВИЛА (заданы до теста, потом не трогаем)
RULES = dict(
    symbol="SPY",          # что торгуем
    sma=200,               # длина средней, дней
    cost_bp=2.0,           # издержки в б.п. за каждую смену позиции (спред + комиссия)
    split="2016-01-01",    # до этой даты — «разработка», после — «проверка»
)


# ---------------------------------------------------------------- 1. ДАННЫЕ
def load(sym):
    """Дневные закрытия с поправкой на дивиденды и сплиты (adj=True) — иначе доходность врёт в дни выплат."""
    s = yahoo_daily(sym, adj=True)
    return list(s.index), s.values


# ---------------------------------------------------------------- 2. ПРОВЕРКА ДАННЫХ
def check_data(dates, px, max_move=0.25):
    """Ничего не считаем, пока не убедились, что данные целые. Возвращает список найденных проблем."""
    problems = []
    if list(dates) != sorted(dates):
        problems.append("даты не по порядку")
    if len(set(dates)) != len(dates):
        problems.append("есть повторы дат")
    if np.any(~np.isfinite(px)) or np.any(px <= 0):
        problems.append("есть пустые, нулевые или отрицательные цены")
    r = px[1:] / px[:-1] - 1
    big = np.where(np.abs(r) > max_move)[0]
    if len(big):
        problems.append(f"скачки больше {max_move:.0%} за день: {[dates[i + 1] for i in big[:5]]} — проверь вручную")
    gaps = [(a, b) for a, b in zip(dates[:-1], dates[1:]) if (np.datetime64(b) - np.datetime64(a)).astype(int) > 5]
    if gaps:
        problems.append(f"дыры больше 5 дней: {gaps[:3]}")
    return problems


# ---------------------------------------------------------------- 3. СИГНАЛ
def signal(px, n):
    """Позиция на ЗАВТРА по данным, известным СЕГОДНЯ на закрытии: 1 — в рынке, 0 — кэш.
    pos[t] решается на закрытии дня t и зарабатывает доходность дня t+1 (это делаем в шаге 4)."""
    pos = np.zeros(len(px))
    for t in range(n - 1, len(px)):
        sma = px[t - n + 1:t + 1].mean()                           # средняя последних n закрытий, включая сегодня (срез до t+1!)
        pos[t] = 1.0 if px[t] > sma else 0.0                       # не через cumsum: см. ловушку «порядок сложения»
    return pos


# ---------------------------------------------------------------- 4. P&L С ИЗДЕРЖКАМИ
def pnl(px, pos, cost_bp):
    """Дневная доходность стратегии. Главная строка любого бэктеста: ВЧЕРАШНЯЯ позиция × СЕГОДНЯШНЯЯ доходность."""
    r = np.zeros(len(px))
    r[1:] = px[1:] / px[:-1] - 1                                 # доходность рынка за день
    held = np.r_[0.0, pos[:-1]]                                    # позиция, с которой вошли в день (решена вчера)
    trades = np.abs(np.diff(np.r_[0.0, pos]))                      # сколько позиции поменяли на закрытии дня
    cost = np.r_[0.0, trades[:-1]] * cost_bp * 1e-4                # платим в день, когда сделка исполнилась
    return held * r - cost, r


# ---------------------------------------------------------------- 5–6. МЕТРИКИ И ПРОВЕРКИ
def random_baseline(r_mkt, pos, runs=300, seed=1):
    """Случайная модель с тем же временем в рынке: позицию «режем» на куски и перемешиваем.
    Если наша модель не лучше 95% таких случайных — её сигнал ничего не стоит."""
    rng = np.random.default_rng(seed)
    out = []
    blocks = np.array_split(pos, len(pos) // 60)                   # куски по ~60 дней, чтобы сохранить длину сделок
    for _ in range(runs):
        p = np.concatenate([blocks[i] for i in rng.permutation(len(blocks))])
        held = np.r_[0.0, p[:-1]]
        out.append(risk.sharpe(held * r_mkt))
    return np.array(out)


if __name__ == "__main__":
    P = RULES
    dates, px = load(P["symbol"])
    print(f"Данные {P['symbol']}: {dates[0]} → {dates[-1]}, {len(px)} дней")

    problems = check_data(dates, px)
    print("Проверка данных:", "чисто" if not problems else "")
    for p in problems:
        print("  ⚠", p)

    pos = signal(px, P["sma"])
    strat, mkt = pnl(px, pos, P["cost_bp"])
    start = P["sma"]                                               # первые n дней сигнала ещё нет — их не считаем
    dates, strat, mkt, pos = dates[start:], strat[start:], mkt[start:], pos[start:]

    print(f"\nМодель:            {risk.fmt(risk.summary(strat))}")
    print(f"Купить и держать:  {risk.fmt(risk.summary(mkt))}")
    print(f"В рынке {pos.mean():.0%} дней, смен позиции в год: {np.abs(np.diff(pos)).sum() / (len(pos) / 252):.1f}")

    a = np.array([d < P["split"] for d in dates])
    print(f"\nРазработка (до {P['split']}): Sharpe {risk.sharpe(strat[a]):.2f}   Проверка: Sharpe {risk.sharpe(strat[~a]):.2f}")
    s2, _ = pnl(px, signal(px, P["sma"]), P["cost_bp"] * 2)
    print(f"Издержки ×2: Sharpe {risk.sharpe(s2[start:]):.2f}  (база {risk.sharpe(strat):.2f})")
    rnd = random_baseline(mkt, pos)
    print(f"Случайная позиция с тем же временем в рынке: Sharpe 50% {np.median(rnd):.2f}, 95% {np.quantile(rnd, 0.95):.2f} "
          f"→ случай не хуже модели в {(rnd >= risk.sharpe(strat)).mean():.0%}")
    dds = risk.bootstrap_drawdowns(strat, runs=500)
    print(f"Монте-Карло: просадка медиана {np.median(dds):.0%}, худшие 5% {np.quantile(dds, 0.05):.0%} (в истории {risk.max_drawdown(strat):.0%})")
    print(f"t-статистика: {risk.t_stat(strat):.1f}  (нужно > 2)")

    # ---------------------------------------------------------------- 7. ВЫВОД — пишем словами в README.md модели
