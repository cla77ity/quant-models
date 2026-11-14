"""
qm.portfolio — движок дневного портфеля (порт lib/portfolio.js, цифры совпадают с JS-версией).

Как устроен бэктест, по шагам (функция backtest):
    1. Каждый день считаем P&L вчерашних позиций: Σ вес × (доходность актива + керри) − плата брокеру за перенос.
    2. В день ребаланса строим сигнал по каждому рынку (тренд / керри / risk parity / SMA-фильтр ...).
    3. «Сырой» вес = сигнал / волатильность рынка — так каждый рынок вносит примерно одинаковый риск (risk parity).
    4. Масштабируем весь портфель так, чтобы его ожидаемая волатильность = цели (например 10% в год).
       Ожидаемая волатильность портфеля = √(wᵀ Σ w), где Σ — ковариационная матрица доходностей за последние L дней.
    5. Ограничиваем суммарное плечо, пропускаем мелкие изменения (полоса), платим спред за изменение позиций.

Обозначения: T — число дней, N — число рынков, R[i, t] — доходность рынка i в день t, w — веса (доля капитала в позиции;
0.15 = позиция на 15% капитала, отрицательный вес = шорт).
"""
from dataclasses import dataclass
from datetime import date as _date

import numpy as np

from . import data
from . import risk

USD_RATE = "IR3TIB01USM156N"


@dataclass
class Asset:
    key: str                 # наше имя рынка: "US500"
    sym: str                 # тикер Yahoo: "SPY"
    cost: float = 1.0        # спред + комиссия в базисных пунктах (0.01%) за каждую единицу изменения веса
    fund: bool = False       # True: ETF с дивидендами как CFD — лонг платит ставку USD
    inv: bool = False        # True: котировка USD/XXX (JPY=X) — разворачиваем знак, чтобы получить XXX против USD
    rate: str = None         # ряд FRED со ставкой валюты (для керри)
    markup: float = None     # наценка брокера за перенос в год (None — взять общую из параметров)
    cls: str = ""            # класс актива: "eq" (акции), "metal" ...
    rp: bool = False         # участвует в risk parity
    max_move: float = 0.15   # дневное движение больше этого считаем битым тиком (обнуляем)


# ------------------------------------------------------------------ данные

def build_data(assets, start="2003-01-01"):
    """Готовит всё для бэктеста на одном календаре:
    R  — простые дневные доходности (для P&L), RL — логарифмические (для сигналов),
    carry — ежедневное керри (разница ставок / стоимость финансирования), active — рынок уже торгуется > 260 дней,
    vol — годовая волатильность за 60 дней."""
    prices = {a.key: data.yahoo_daily(a.sym, adj=a.fund) for a in assets}
    rates = {a.key: data.fred(a.rate) for a in assets if a.rate}
    usd = data.fred(USD_RATE)

    # общий календарь: все будние дни, в которые торговался хотя бы один рынок
    days = set()
    for s in prices.values():
        for d in s.index:
            if d >= start and _date.fromisoformat(d).weekday() < 5:
                days.add(d)
    dates = sorted(days)
    T, N = len(dates), len(assets)

    RL = np.zeros((N, T)); carry = np.zeros((N, T)); active = np.zeros((N, T), bool); diff = np.full((N, T), np.nan)
    bad = {}
    for i, a in enumerate(assets):
        p = prices[a.key].to_dict()
        last, first, bad[a.key] = None, None, 0
        for t, d in enumerate(dates):
            px = p.get(d)
            if px is not None:
                if last is not None:
                    x = np.log(px / last) * (-1 if a.inv else 1)
                    if abs(x) > a.max_move:      # битый тик в данных: обнуляем, а не верим
                        x, bad[a.key] = 0.0, bad[a.key] + 1
                    RL[i, t] = x
                first = t if first is None else first
                last = px
            active[i, t] = first is not None and t - first > 260
            ru = data.rate_on(usd, d)
            if a.rate:
                rf = data.rate_on(rates[a.key], d)
                if rf is not None and ru is not None:
                    diff[i, t] = rf - ru
                    carry[i, t] = (rf - ru) / 100 / 252      # держишь валюту с большей ставкой — получаешь разницу
            if a.fund and ru is not None:
                carry[i, t] = -ru / 100 / 252                # CFD на индекс: лонг платит ставку USD
    # волатильность за 60 дней (корень из среднего квадрата лог-доходностей × 252)
    # сумма квадратов за окно [t-59, t] = накопленная сумма квадратов на день t минус на день t-60
    sq = np.cumsum(RL ** 2, axis=1)
    vol = np.zeros((N, T))
    vol[:, 60:] = np.sqrt(np.clip(sq[:, 60:] - sq[:, :T - 60], 0, None) / 60 * 252)
    return dict(dates=dates, R=np.expm1(RL), RL=RL, carry=carry, active=active, vol=vol, diff=diff, assets=assets, bad=bad)


def mask(D, keep):
    """Копия данных, где торгуются только рынки из списка keep (остальные «выключены»)."""
    act = D["active"].copy()
    for i, a in enumerate(D["assets"]):
        if a.key not in keep:
            act[i] = False
    return {**D, "active": act}


# ------------------------------------------------------------------ бэктест

def backtest(D, strategy="combo", target=0.10, rebal=5, markup=0.01, markup_mult=1.0, cost_mult=1.0, gross=6.0,
             horizons=(21, 63, 252), n_carry=3, cov_len=120, band=None, wts=None, weights=None, fixed=False, only=None, sma_n=200):
    """Возвращает dict: ret — дневные доходности стратегии, lev — плечо по дням, w_hist — веса на каждом ребалансе.

    strategy: trend | carry | combo | long | sma | trendLO | rp | rpsma | rptrend | weights
    target  — целевая годовая волатильность портфеля; rebal — ребаланс раз в N дней; cov_len — окно ковариации;
    band    — не менять вес, если изменение меньше этой доли (0.25 = 25%); wts — множители веса по рынкам."""
    dates, R, RL, carry, active, vol, assets = D["dates"], D["R"], D["RL"], D["carry"], D["active"], D["vol"], D["assets"]
    N, T = R.shape
    cum = np.cumsum(RL, axis=1)                     # накопленная лог-доходность = log(цена / первая цена)
    mk = np.array([0.0 if markup == 0 else (a.markup if a.markup is not None else markup) * markup_mult for a in assets])
    cost = np.array([a.cost for a in assets]) * 1e-4 * cost_mult
    w = np.zeros(N)
    ret, lev, w_hist = np.zeros(T), np.zeros(T), []

    for t in range(260, T):
        # 1. P&L вчерашних позиций за сегодня, минус плата брокеру за перенос (в день = годовая / 252)
        r = np.sum(w * (R[:, t] + carry[:, t])) - np.sum(np.abs(w) * mk) / 252

        # 2–5. ребаланс на закрытии дня
        if (t - 260) % rebal == 0:
            act = [i for i in range(N) if active[i, t] and vol[i, t] > 0]
            raw = np.zeros(N)
            # тренд: среднее знаков доходности за 1, 3 и 12 месяцев (−1 … +1)
            s_trend = np.zeros(N)
            for i in act:
                s_trend[i] = np.mean([np.sign(cum[i, t] - cum[i, t - h]) for h in horizons])
            # керри: лонг n_carry валют с самой большой ставкой, шорт n_carry с самой маленькой
            s_carry = np.zeros(N)
            cur = sorted([i for i in act if assets[i].rate and not np.isnan(D["diff"][i, t])], key=lambda i: -D["diff"][i, t])
            if len(cur) >= 2 * n_carry:
                for i in cur[:n_carry]: s_carry[i] = 1
                for i in cur[-n_carry:]: s_carry[i] = -1

            def above_sma(i, n):   # 1, если цена выше своей средней за n дней
                return 1.0 if np.exp(cum[i, t]) > np.mean(np.exp(cum[i, t - n + 1:t + 1])) else 0.0

            for i in act:
                a = assets[i]
                if only and a.key != only:
                    continue
                s = {"trend": lambda: s_trend[i], "carry": lambda: s_carry[i], "long": lambda: 1.0, "sma": lambda: above_sma(i, sma_n),
                     "trendLO": lambda: max(0.0, s_trend[i]), "rp": lambda: 1.0 if a.rp else 0.0,
                     "rpsma": lambda: above_sma(i, 200) if a.rp else 0.0,
                     "rptrend": lambda: 0.5 * (1.0 if a.rp else 0.0) + 0.5 * s_trend[i],
                     "weights": lambda: (weights or {}).get(a.key, 0.0)}.get(strategy, lambda: 0.5 * s_trend[i] + 0.5 * s_carry[i])()
                raw[i] = (s if fixed else s / vol[i, t]) * ((wts or {}).get(a.key, 1.0) if wts else 1.0)

            # 4. масштаб к целевой волатильности: σ портфеля = √(rawᵀ Σ raw) по последним cov_len дням
            if fixed:
                k = 1.0
            else:
                X = R[act, t - cov_len + 1:t + 1]                 # доходности активных рынков за окно
                port = raw[act] @ X                                # дневные доходности «сырого» портфеля за окно
                pv = np.sqrt(max(0.0, np.mean(port ** 2) - np.mean(port) ** 2) * 252)
                k = target / pv if pv > 0 else 0.0
            nw = raw * k
            g = np.abs(nw).sum()
            if g > gross:
                nw *= gross / g                                    # 5. потолок плеча
            if band:
                small = (nw != 0) & (w != 0) & (np.abs(nw - w) < band * np.abs(w))
                nw[small] = w[small]                               # мелкие изменения не делаем — экономим спред
            r -= np.sum(np.abs(nw - w) * cost)                     # спред за изменение позиций
            w = nw
            w_hist.append((dates[t], w.copy()))
        ret[t], lev[t] = r, np.abs(w).sum()
    return dict(ret=ret, lev=lev, w_hist=w_hist)


def metrics(D, ret, start, end="2100"):
    """Метрики риска на отрезке дат [start, end)."""
    idx = [t for t, d in enumerate(D["dates"]) if start <= d < end]
    return risk.summary(ret[idx])


def first_trading_day(D):
    """Первая дата, когда торгуются ВСЕ рынки (у каждого есть год истории)."""
    return D["dates"][max(int(np.argmax(a)) for a in D["active"])]
