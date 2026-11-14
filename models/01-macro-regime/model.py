"""
01 · Макро-режим: помогает ли макро-счёт простой стратегии на индексах?   Запуск:  python models/01-macro-regime/model.py

10 макро-факторов (ликвидность ФРС, 10Y, доллар, VIX, 2Y против ставки ФРС, реальная доходность, тренд инфляции,
кредитный спред, структура VIX, индекс финансовых условий NFCI). Каждый даёт −1 / 0 / +1, счёт = сумма.
Главное правило честности: каждый ряд используется только ПОСЛЕ даты публикации (lag) — иначе бэктест «знает будущее».
Правила взяты из 2008–2016, проверка — 2017–сейчас.
"""
import sys
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import json  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402

from qm.data import CACHE, MAX_AGE, _get  # noqa: E402

DAY = 86_400_000
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def ms(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def cached(name, url):
    """Скачать один раз в сутки и хранить в data-cache/ (чтобы не нагружать FRED и Yahoo при каждом запуске)."""
    path = CACHE / name
    if not (path.exists() and time.time() - path.stat().st_mtime < MAX_AGE):
        path.write_text(_get(url), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def fred(series_id):
    """Ряд FRED с 2005 года: список (время в мс, значение)."""
    rows = [l.split(",") for l in cached(f"M_{series_id}.csv", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd=2005-01-01").strip().split("\n")[1:]]
    out = []
    for d, v in rows:
        try:
            out.append((ms(d), float(v)))
        except ValueError:
            pass                                    # «.» — пропуск в данных
    return out


def yahoo(sym):
    """Дневные закрытия Yahoo с 2006 года: список (день 'YYYY-MM-DD', close)."""
    p2 = int(datetime.now(timezone.utc).timestamp())
    name = "M_" + "".join(ch for ch in sym if ch.isalnum()) + ".json"
    r = json.loads(cached(name, f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=1136073600&period2={p2}&interval=1d"))["chart"]["result"][0]
    off = (r["meta"].get("gmtoffset") or 0) * 1000
    return [(datetime.fromtimestamp((t * 1000 + off) / 1000, timezone.utc).strftime("%Y-%m-%d"), c)
            for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]


def as_of(series, t, lag_days=0):
    """Последнее значение ряда, опубликованное к моменту t с учётом задержки публикации (lag_days)."""
    times = [x[0] for x in series]
    k = bisect_right(times, t - lag_days * DAY)
    return series[k - 1] if k else None


def factors(days, vix, vix3, dxy, S):
    """Макро-факторы на каждый день: словарь {фактор: −1/0/+1} + score (сумма)."""
    T = [ms(d) for d in days]
    nl, pce = S["nl"], S["pce"]
    out = []
    for i, t in enumerate(T):
        f = {}
        a, b = as_of(nl, t, 2), as_of(nl, t - 28 * DAY, 2)
        if a and b:                                          # ликвидность ФРС: изменение за 4 недели, $ млрд
            ch = a[1] - b[1]; f["liq"] = 1 if ch > 25 else -1 if ch < -25 else 0
        y, y5 = as_of(S["d10"], t, 1), as_of(S["d10"], t - 7 * DAY, 1)
        if y and y5:                                         # доходность 10Y за неделю, б.п.
            bp = (y[1] - y5[1]) * 100; f["y10"] = 1 if bp <= -5 else -1 if bp >= 5 else 0
        dx, dx5 = dxy[i], (dxy[i - 5] if i >= 5 else None)
        if dx and dx5:                                       # доллар за неделю
            c = (dx / dx5 - 1) * 100; f["dxy"] = 1 if c <= -0.3 else -1 if c >= 0.3 else 0
        v, v5 = vix[i], (vix[i - 5] if i >= 5 else None)
        if v and v5:                                         # страх: уровень и изменение VIX
            c = (v / v5 - 1) * 100; f["vix"] = 1 if v < 20 and c <= 0 else -1 if v > 25 or c >= 20 else 0
        two, ff = as_of(S["d2"], t, 1), as_of(S["dff"], t, 1)
        if two and ff:                                       # рынок ждёт снижения ставки (2Y ниже ставки ФРС)?
            sp = two[1] - ff[1]; f["fed"] = 1 if sp < -0.25 else -1 if sp > 0.25 else 0
        r0, r5 = as_of(S["real"], t, 1), as_of(S["real"], t - 7 * DAY, 1)
        if r0 and r5:                                        # реальная доходность за неделю
            bp = (r0[1] - r5[1]) * 100; f["real"] = 1 if bp <= -5 else -1 if bp >= 5 else 0
        pa = [x for x in pce if x[0] <= t]
        if len(pa) > 13:                                     # инфляция: 3-месячный темп против годового
            yoy = (pa[-1][1] / pa[-13][1] - 1) * 100; m3 = ((pa[-1][1] / pa[-4][1]) ** 4 - 1) * 100
            f["infl"] = 1 if m3 < yoy - 0.3 and m3 < 3 else -1 if m3 > yoy + 0.3 else 0
        c0, c20 = as_of(S["baa"], t, 1), as_of(S["baa"], t - 28 * DAY, 1)
        if c0 and c20:                                       # кредитный спред за 4 недели
            ch = c0[1] - c20[1]; f["credit"] = 1 if ch <= -0.10 else -1 if ch >= 0.15 else 0
        if v and vix3[i]:                                    # структура VIX: краткосрочный страх выше долгосрочного = плохо
            ratio = v / vix3[i]; f["vixts"] = 1 if ratio < 0.9 else -1 if ratio > 1 else 0
        nf, nf4 = as_of(S["nfci"], t, 7), as_of(S["nfci"], t - 28 * DAY, 7)
        if nf and nf4:                                       # финансовые условия
            ch = nf[1] - nf4[1]; f["nfci"] = 1 if nf[1] < 0 and ch <= 0 else -1 if nf[1] > 0 or ch > 0.15 else 0
        f["score"] = sum(f.values())
        out.append(f)
    return out


def run(close, want, a, b, cost=0.0002):
    """Сигнал на закрытии дня i, позиция держится в день i+1; 2 б.п. за каждую единицу изменения позиции."""
    eq, pos, turn, inside = [1.0], 0.0, 0.0, 0
    for i in range(a, b - 1):
        w, r = want(i), close[i + 1] / close[i] - 1
        turn += abs(w - pos); c = abs(w - pos) * cost; pos = w
        eq.append(eq[-1] * (1 + pos * r - c)); inside += pos > 0
    eq = np.array(eq); rets = eq[1:] / eq[:-1] - 1; yrs = len(rets) / 252
    sd = rets.std()                                          # как в JS: стандартное отклонение «по генеральной совокупности»
    return {"cagr": eq[-1] ** (1 / yrs) - 1, "vol": sd * np.sqrt(252), "sharpe": rets.mean() / sd * np.sqrt(252) if sd else 0,
            "mdd": (eq / np.maximum.accumulate(np.maximum(eq, 1)) - 1).min(), "inside": inside / len(rets), "switches": turn / yrs}


if __name__ == "__main__":
    ids = {"walcl": "WALCL", "tga": "WTREGEN", "rrp": "RRPONTSYD", "d10": "DGS10", "d2": "DGS2", "dff": "DFF", "real": "DFII10", "baa": "BAA10Y", "nfci": "NFCI", "pce": "PCEPILFE"}
    S = {k: fred(v) for k, v in ids.items()}
    # чистая ликвидность ФРС = баланс ФРС − счёт Казначейства (TGA) − обратное репо, $ млрд
    S["nl"] = [(t, v / 1000 - g[1] / 1000 - r[1]) for t, v in S["walcl"] if (g := as_of(S["tga"], t)) and (r := as_of(S["rrp"], t))]
    S["pce"] = [(t + 60 * DAY, v) for t, v in S["pce"]]      # PCE за месяц публикуется примерно через 2 месяца
    ext = {s: dict(yahoo(s)) for s in ["^VIX", "^VIX3M", "DX-Y.NYB"]}

    for IX, sym in [("NDX", "^NDX"), ("SPX", "^GSPC")]:
        px = yahoo(sym)
        days, close = [d for d, _ in px], np.array([c for _, c in px])
        F = factors(days, [ext["^VIX"].get(d) for d in days], [ext["^VIX3M"].get(d) for d in days], [ext["DX-Y.NYB"].get(d) for d in days], S)
        sma50 = lambda i: close[i - 49:i + 1].mean() if i >= 49 else 0.0
        trend = lambda i: close[i] > sma50(i)
        strats = {"Buy & hold": lambda i: 1, "Тренд (цена > SMA50)": lambda i: 1 if trend(i) else 0,
                  "Только макро (счёт ≥ 0)": lambda i: 1 if F[i]["score"] >= 0 else 0,
                  "Тренд + макро-фильтр (счёт ≥ 0)": lambda i: 1 if trend(i) and F[i]["score"] >= 0 else 0,
                  "Тренд + размер по макро": lambda i: 0 if not trend(i) else 1 if F[i]["score"] >= 2 else 0 if F[i]["score"] <= -2 else 0.5}
        # ежедневный макро-счёт (известный на закрытии дня) — его использует фильтр «тренд + макро» в модели 02
        out_dir = Path(__file__).resolve().parent / "results"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"step1_scores_{IX}.json").write_text(json.dumps({d: F[i]["score"] for i, d in enumerate(days)}))
        start = next(i for i, d in enumerate(days) if d >= "2008-01-02")
        split = next(i for i, d in enumerate(days) if d >= "2017-01-01")
        print(f"\n================ {IX}  {days[start]} → {days[-1]}")
        for name, fn in strats.items():
            print(name)
            for pn, (a, b) in {"2008–2016 (правила)": (start, split), "2017–сейчас (проверка)": (split, len(days)), "Всё время": (start, len(days))}.items():
                m = run(close, fn, a, b)
                print(f"   {pn:<22} CAGR {m['cagr']:+.1%}  vol {m['vol']:.1%}  Sharpe {m['sharpe']:.2f}  maxDD {m['mdd']:.1%}  в рынке {m['inside']:.0%}")
        # предсказывает ли счёт следующие 20 дней? (непересекающиеся отрезки — честная t-статистика)
        buckets = {}
        for i in range(start, len(days) - 20, 20):
            s = F[i]["score"]; key = "≤ −3" if s <= -3 else "−2…−1" if s <= -1 else "0" if s == 0 else "+1…+2" if s <= 2 else "≥ +3"
            buckets.setdefault(key, []).append((close[i + 20] / close[i] - 1) * 100)
        print("Счёт макро → доходность следующих 20 дней:")
        for k in ["≤ −3", "−2…−1", "0", "+1…+2", "≥ +3"]:
            if k in buckets:
                a = np.array(buckets[k]); t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 else 0
                print(f"   счёт {k:<7} n={len(a):>3}  средн {a.mean():+.1f}%  рост {(a > 0).mean():.0%}  t={t:.2f}")
