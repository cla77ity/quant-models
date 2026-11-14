"""
qm.intraday — внутридневные данные и общие инструменты для моделей на 5-минутках.

    klines("QQQ", "5m")            → свечи перпетуала Binance (QQQ/SPY = прокси NQ/ES) — numpy-массивы t, o, h, l, c, v
    yahoo_intraday("QQQ", "1h")    → часовые свечи Yahoo за ~2 года
    ny(t_ms)                       → дата и минута дня по Нью-Йорку (сессии, 9:30, 16:55 — всё в NY-времени)
    trade_stats(results_in_R)      → сводка сделок: win-rate, средний R, t, просадка в R, половины периода
    Lcg                            → тот же генератор случайных чисел, что в JS-версии (для одинаковых результатов)

Кэш — data-cache/ (общий с JS: QQQ_5m.json, Y_QQQ_1h.json ...).
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .data import CACHE, MAX_AGE, _get

INTRADAY_MAX_AGE = min(MAX_AGE, 12 * 3600) if MAX_AGE < 10**8 else MAX_AGE   # как в JS: свечи свежие 12 часов


def _fresh(path):
    return path.exists() and time.time() - path.stat().st_mtime < INTRADAY_MAX_AGE


def klines(sym, tf):
    """Вся история свечей перпетуала SYMUSDT с Binance (с конца в начало по 1500 штук), кэш на 12 часов.
    Возвращает dict массивов: t (мс, открытие свечи), o, h, l, c, v."""
    path = CACHE / f"{sym}_{tf}.json"
    if not _fresh(path):
        out, end = [], None
        try:
            while True:
                url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}USDT&interval={tf}&limit=1500" + (f"&endTime={end}" if end else "")
                k = json.loads(_get(url))
                if not isinstance(k, list) or not k:
                    break
                out = k + out
                end = k[0][0] - 1
                time.sleep(0.25)
                if len(k) < 1500:
                    break
        except OSError as e:
            print(f"  (сеть: {e.__class__.__name__})")
        if out:
            path.write_text(json.dumps(out))
    raw = json.loads(path.read_text()) if path.exists() else []
    a = np.array([[float(x) for x in k[:6]] for k in raw]) if raw else np.zeros((0, 6))
    return {"t": a[:, 0].astype(np.int64), "o": a[:, 1], "h": a[:, 2], "l": a[:, 3], "c": a[:, 4], "v": a[:, 5]}


def yahoo_intraday(sym, interval="1h", rng="730d"):
    """Внутридневные свечи Yahoo (часовые — до 2 лет). Возвращает список [t_ms, o, h, l, c]."""
    path = CACHE / f"Y_{''.join(ch for ch in sym if ch.isupper())}_{interval}.json"
    if not _fresh(path):
        try:
            j = json.loads(_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={interval}&range={rng}&includePrePost=false"))["chart"]["result"][0]
            q = j["indicators"]["quote"][0]
            rows = [[t * 1000, q["open"][i], q["high"][i], q["low"][i], q["close"][i]] for i, t in enumerate(j["timestamp"])]
            rows = [r for r in rows if all(x is not None for x in r[1:])]
            path.write_text(json.dumps(rows))
            time.sleep(0.4)
        except OSError as e:
            if not path.exists():
                raise
            print(f"  (нет сети для {path.name}: {e.__class__.__name__} — беру кэш)")
    return json.loads(path.read_text())


def ny(t_ms):
    """Нью-йоркское время для массива меток (мс): даты 'YYYY-MM-DD', минута дня (0…1439), день недели (0 = пн)."""
    ts = pd.to_datetime(pd.Series(np.asarray(t_ms, dtype=np.int64)), unit="ms", utc=True).dt.tz_convert("America/New_York")
    return ts.dt.strftime("%Y-%m-%d").to_numpy(), (ts.dt.hour * 60 + ts.dt.minute).to_numpy(), ts.dt.dayofweek.to_numpy()


class Lcg:
    """Простой генератор псевдослучайных чисел (тот же, что в JS-версии), чтобы «случайные» тесты совпадали до знака."""
    # Считаем в float, а не в целых: JS хранит числа как double, и seed × 1103515245 там теряет точность.
    # Python с целыми был бы «правильнее», но давал бы другую последовательность — и другие цифры, чем у JS.
    def __init__(self, seed):
        self.s = float(seed)

    def __call__(self):
        self.s = (self.s * 1103515245.0 + 12345.0) % 2147483648.0
        return self.s / 2147483648.0


def trade_stats(r, min_n=10):
    """Сводка по результатам сделок (в R или в долях): n, % прибыльных, среднее, t, сумма, просадка суммы, половины."""
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n < min_n:
        return None
    m, sd = r.mean(), r.std(ddof=1)
    eq = np.cumsum(r)
    dd = (eq - np.maximum.accumulate(np.maximum(eq, 0))).min()
    half = n // 2
    return {"n": n, "win": (r > 0).mean() * 100, "avg": m, "t": m / (sd / np.sqrt(n)) if sd else 0.0, "sum": eq[-1], "dd": min(dd, 0.0),
            "first": r[:half].mean(), "second": r[half:].mean(), "sd": sd}
