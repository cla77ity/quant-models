"""
02 · ICT-зоны как реальные сделки.   Запуск:  python models/02-ict-zones/model.py

ICT-движок (qm/ict.py) записывает каждое первое касание зоны RB / OB / FVG и каждый «свип + реклейм» открытий
00:00 / 09:30 / 10:00 (трассировка). Здесь эти события превращаются в сделки со стопом, целью и издержками:

    A. entries   — вход прямо на уровне, стоп за тенью / дальним краем, цель 1 / 2 / 3R, выход через 4 часа (NQ/ES)
    B. rb_10am   — RB: край зоны / середина (CE) / подтверждение; открытия: стоп индикатора / за свипом / ретест (12 инструментов)
    C. rb_edge   — можно ли спасти вход от края RB (стопы шире, цели, безубыток, половина на краю + половина в CE)
    D. rb_return — вход на возврате из зоны (ордер на краю, исполняется при выходе цены обратно)

Общие правила: стоп и цель в одной свече = стоп; акции — только часы США; издержки CME (индексы) / 2 б.п. (акции).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np  # noqa: E402

from qm import ict  # noqa: E402
from qm.intraday import klines, ny  # noqa: E402

INDEX, STOCKS = ["QQQ", "SPY"], ["IWM", "SMH", "NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "TSLA", "AVGO"]
KILLZONES = [(1200, 1320), (120, 240), (510, 630), (810, 930)]        # Азия, Лондон, NY AM, NY PM (время NY)
kz = lambda m: any(a <= m < b for a, b in KILLZONES)
rth = lambda m: 570 <= m < 960
cost_of = lambda sym: 0.00004 if sym in INDEX else 0.0002
HOLD = 48                                                              # 48 пятиминуток = 4 часа


def trend_find(k15, wrap_last=False):
    """Тренд как в JS-скриптах шагов 2–3: последняя закрытая 15m-свеча против SMA50 (сумма 50 закрытий по порядку)."""
    closes_at, c15 = k15["t"] + 15 * 60_000, k15["c"].tolist()
    n = len(c15)

    def at(t):
        idx = int(np.searchsorted(closes_at, t, side="right"))     # = индекс первой свечи, закрывшейся позже t
        i = (idx if idx < n else -1) - 1
        if wrap_last and i < 0:
            i = n - 1
        if i < 50:
            return None
        s = 0.0
        for x in c15[i - 49:i + 1]:
            s += x
        return c15[i] > s / 50
    return at


def stat(r, min_n=8):
    r = np.asarray(r, float)
    n = len(r)
    if n < min_n:
        return None
    m, sd = r.mean(), (r.std(ddof=1) if n > 1 else 0.0)
    half = n // 2
    return dict(n=n, win=(r > 0).mean() * 100, avgR=m, t=m / (sd / np.sqrt(n)) if sd else 0.0, first=r[:half].mean(), second=r[half:].mean())


def load(sym):
    k15, k5 = klines(sym, "15m"), klines(sym, "5m")
    if len(k5["t"]) < 3000:
        return None
    trace = []
    ict.run(k15, k5, proxy=True, trace=trace)
    return dict(k15=k15, k5=k5, trace=trace, mins=ny(k5["t"])[1], dates=ny(k5["t"])[0])


# ---------------------------------------------------------------- A. entries (NQ/ES)
def part_a(D):
    costs, targets = {"Binance перп": 0.0007, "CME фьючерс": 0.00004}, [1, 2, 3]
    print("\n######## A. Вход на уровне ICT (NQ/ES), цель 2R")
    for sym, ix in [("QQQ", "NDX"), ("SPY", "SPX")]:
        d = D[sym]
        h, l, c, trace = d["k5"]["h"], d["k5"]["l"], d["k5"]["c"], d["trace"]
        trend = trend_find(d["k15"], wrap_last=True)
        sc_path = Path(__file__).resolve().parents[1] / "01-macro-regime" / "results" / f"step1_scores_{ix}.json"
        sc = json.loads(sc_path.read_text()) if sc_path.exists() else None
        days = sorted(sc) if sc else []

        def prev_score(date):
            k = int(np.searchsorted(days, date, side="left")) - 1   # последний день СТРОГО раньше даты (макро известно к утру)
            return sc[days[k]] if k >= 0 else None
        trades = []
        for ev in trace:
            dr = 1 if ev["bull"] else -1
            buf = 0 if ev.get("atClose") else 0.02 * ev["atr"]
            entry, stop = ev["entry"], ev["stop"] - dr * buf
            R = abs(entry - stop)
            if not (0.05 * ev["atr"] <= R <= 3 * ev["atr"]):
                continue
            res = {}
            for T in targets:
                tgt, out = entry + dr * T * R, None
                if not ev.get("atClose") and (l[ev["j"]] <= stop if dr > 0 else h[ev["j"]] >= stop):
                    out = -1.0                                         # свеча касания уже прошла через стоп
                k = ev["j"] + 1
                while out is None and k < min(len(c), ev["j"] + 1 + HOLD):
                    if l[k] <= stop if dr > 0 else h[k] >= stop:
                        out = -1.0
                    elif h[k] >= tgt if dr > 0 else l[k] <= tgt:
                        out = float(T)
                    k += 1
                if out is None:
                    out = (c[min(len(c) - 1, ev["j"] + HOLD)] - entry) * dr / R
                res[T] = out
            s = prev_score(ny([ev["t"]])[0][0]) if sc else None
            trades.append(dict(ev, R=R, res=res, trend=trend(ev["t"]), kz=kz(ev["min"]), macro=None if s is None else (s >= 0 if ev["bull"] else s <= 0),
                               costR={k: p * entry / R for k, p in costs.items()}))
        filters = {"все сигналы": lambda x: True, "по тренду": lambda x: x["trend"] == x["bull"], "против тренда": lambda x: x["trend"] is not None and x["trend"] != x["bull"],
                   "в киллзону": lambda x: x["kz"], "тренд + киллзона": lambda x: x["trend"] == x["bull"] and x["kz"],
                   "тренд + макро": lambda x: x["trend"] == x["bull"] and x["macro"] is True, "тренд + киллзона + макро": lambda x: x["trend"] == x["bull"] and x["kz"] and x["macro"] is True}
        print(f"\n== {sym} ({'NQ' if sym == 'QQQ' else 'ES'}) · событий {len(trace)}, сделок {len(trades)}")
        for ck in costs:
            print(f"--- издержки: {ck}")
            for kind in ["RB", "OB", "FVG", "KO00", "KO0930", "KO1000", "ВСЕ"]:
                base = trades if kind == "ВСЕ" else [x for x in trades if x["kind"] == kind]
                for fn, f in filters.items():
                    r = [x["res"][2] - x["costR"][ck] for x in base if f(x)]
                    s = stat(r, 5)
                    if s:
                        print(f"  {kind:<7} {fn:<26} n={s['n']:>4} win {s['win']:>3.0f}%  ср {s['avgR']:+.2f}R  t={s['t']:+.1f}  половины {s['first']:+.2f}/{s['second']:+.2f}")


# ---------------------------------------------------------------- B. rb_10am (12 инструментов)
def walk(d, dr, entry, stop, frm, T, first_bar_stop):
    h, l, c = d["k5"]["h"], d["k5"]["l"], d["k5"]["c"]
    R, tgt = abs(entry - stop), entry + dr * T * abs(entry - stop)
    if first_bar_stop and (l[frm] <= stop if dr > 0 else h[frm] >= stop):
        return -1.0
    for k in range(frm + 1, min(len(c), frm + 1 + HOLD)):
        if l[k] <= stop if dr > 0 else h[k] >= stop:
            return -1.0
        if h[k] >= tgt if dr > 0 else l[k] <= tgt:
            return float(T)
    return (c[min(len(c) - 1, frm + HOLD)] - entry) * dr / R


def part_b(D):
    WAIT, trades = 6, []
    for sym, d in D.items():
        o, h, l, c, t5 = d["k5"]["o"], d["k5"]["h"], d["k5"]["l"], d["k5"]["c"], d["k5"]["t"]
        trend = trend_find(d["k15"])

        def add(ev, variant, dr, entry, stop, frm, fbs):
            R = abs(entry - stop)
            if not (0.05 * ev["atr"] <= R <= 3 * ev["atr"]):
                return
            m = d["mins"][frm]
            trades.append(dict(sym=sym, group="idx" if sym in INDEX else "stk", kind=ev["kind"] if ev["kind"].startswith("KO") else "RB", variant=variant, t=int(t5[frm]),
                               res={T: walk(d, dr, entry, stop, frm, T, fbs) - cost_of(sym) * entry / R for T in (2, 3)},
                               trend=trend(ev["t"]) == (dr > 0), kz=kz(m), rth=rth(m), am=600 <= m < 720, inMacro=ev.get("inMacro"), swept=ev.get("swept")))
        for ev in d["trace"]:
            dr, j = (1 if ev["bull"] else -1), ev["j"]
            if ev["kind"] == "RB":
                stop = ev["stop"] - dr * 0.02 * ev["atr"]
                add(ev, "край зоны", dr, ev["entry"], stop, j, True)
                for k in range(j, min(len(c), j + WAIT * 8)):                 # лимит в середину зоны (CE)
                    # сначала проверяем уровень входа: цена по пути к стопу ПРОХОДИТ CE, лимит исполняется,
                    # а стоп в той же свече засчитывает walk(first_bar_stop=True). Обратный порядок (как в JS-версии)
                    # выкидывал именно эти убыточные сделки и рисовал ложный плюс +0.20R.
                    if l[k] <= ev["ce"] if dr > 0 else h[k] >= ev["ce"]:
                        add(ev, "середина (CE)", dr, ev["ce"], stop, k, True); break
                    if l[k] <= stop if dr > 0 else h[k] >= stop:
                        break
                for k in range(j, min(len(c), j + WAIT)):                     # подтверждение: закрытие обратно из зоны
                    if l[k] <= stop if dr > 0 else h[k] >= stop:
                        break
                    if (c[k] > ev["entry"] and c[k] > o[k]) if dr > 0 else (c[k] < ev["entry"] and c[k] < o[k]):
                        add(ev, "подтверждение", dr, c[k], stop, k, False); break
            elif ev["kind"].startswith("KO"):
                add(ev, "pine (0.5 ATR)", dr, ev["entry"], ev["stop"], j, False)
                if ev.get("ext") is not None:
                    s_stop = ev["ext"] - dr * 0.02 * ev["atr"]
                    add(ev, "стоп за свипом", dr, ev["entry"], s_stop, j, False)
                    for k in range(j + 1, min(len(c), j + 1 + WAIT)):
                        if l[k] <= ev["level"] if dr > 0 else h[k] >= ev["level"]:   # уровень раньше стопа (см. CE)
                            add(ev, "ретест уровня", dr, ev["level"], s_stop, k, True); break
                        if l[k] <= s_stop if dr > 0 else h[k] >= s_stop:
                            break
    trades.sort(key=lambda x: x["t"])
    F = {"RB": {"все": lambda x: True, "по тренду": lambda x: x["trend"], "по тренду + киллзона": lambda x: x["trend"] and x["kz"],
                "RB в макро-окне 09:50–10:10": lambda x: x["inMacro"], "RB со снятием ликвидности": lambda x: x["swept"], "по тренду + (макро или снятие)": lambda x: x["trend"] and (x["inMacro"] or x["swept"])},
         "KO": {"все": lambda x: True, "по тренду": lambda x: x["trend"], "реклейм 10:00–12:00": lambda x: x["am"], "по тренду + 10:00–12:00": lambda x: x["trend"] and x["am"]}}
    print("\n######## B. RB и ключевые открытия подробно (цель 2R / 3R)")
    for group, gname in [("idx", "Индексы (NQ/ES)"), ("stk", "Акции и ETF")]:
        print(f"\n== {gname}")
        for kind in ["RB", "KO1000", "KO00", "KO0930"]:
            base = [x for x in trades if x["group"] == group and x["kind"] == kind and (group == "idx" or x["rth"])]
            for variant in dict.fromkeys(x["variant"] for x in base):
                for fn, f in F["RB" if kind == "RB" else "KO"].items():
                    lst = [x for x in base if x["variant"] == variant and f(x)]
                    s2, s3 = stat([x["res"][2] for x in lst]), stat([x["res"][3] for x in lst])
                    if s2:
                        print(f"  {kind:<7} {variant:<15} {fn:<31} n={s2['n']:>5}  2R {s2['avgR']:+.2f} (win {s2['win']:.0f}%, t {s2['t']:+.1f})   3R {s3['avgR']:+.2f}")


# ---------------------------------------------------------------- C. rb_edge
def part_c(D):
    rows = []
    for sym, d in D.items():
        h, l, c, t5 = d["k5"]["h"], d["k5"]["l"], d["k5"]["c"], d["k5"]["t"]
        trend = trend_find(d["k15"])
        hit_s = lambda dr, k, s: l[k] <= s if dr > 0 else h[k] >= s
        hit_t = lambda dr, k, t: h[k] >= t if dr > 0 else l[k] <= t

        def wlk(dr, entry, stop, tgt, frm, be):
            R = abs(entry - stop)
            if hit_s(dr, frm, stop):
                return stop
            s = stop
            for k in range(frm + 1, min(len(c), frm + 1 + HOLD)):
                if hit_s(dr, k, s): return s
                if hit_t(dr, k, tgt): return tgt
                if be and (h[k] >= entry + R if dr > 0 else l[k] <= entry - R): s = entry
            return c[min(len(c) - 1, frm + HOLD)]
        for ev in d["trace"]:
            if ev["kind"] != "RB":
                continue
            dr, A, j = (1 if ev["bull"] else -1), ev["atr"], ev["j"]
            m = d["mins"][j]
            if sym not in INDEX and not rth(m):
                continue
            res, cost = {}, cost_of(sym)

            def one(name, entry, stop, T, be):
                R = abs(entry - stop)
                if 0.05 * A <= R <= 3 * A:
                    res[name] = (wlk(dr, entry, stop, entry + dr * T * R, j, be) - entry) * dr / R - cost * entry / R
            wick = ev["stop"] - dr * 0.02 * A
            for T in (1, 1.5, 2, 3):
                one(f"стоп за тенью · цель {T}R", ev["entry"], wick, T, False)
            one("стоп за тенью +0.25 ATR · 2R", ev["entry"], wick - dr * 0.25 * A, 2, False)
            one("стоп за тенью +0.5 ATR · 2R", ev["entry"], wick - dr * 0.5 * A, 2, False)
            one("стоп +0.5 ATR · 1.5R", ev["entry"], wick - dr * 0.5 * A, 1.5, False)
            one("стоп за тенью · 2R · безубыток после 1R", ev["entry"], wick, 2, True)
            Re, Rc = abs(ev["entry"] - wick), abs(ev["ce"] - wick)
            if 0.05 * A <= Re <= 3 * A:                                   # половина на краю + половина в CE
                tgt, plan = ev["entry"] + dr * 2 * Re, 0.5 * Re + 0.5 * Rc
                pnl = 0.5 * ((wlk(dr, ev["entry"], wick, tgt, j, False) - ev["entry"]) * dr) - 0.5 * cost * ev["entry"]
                for k in range(j, min(len(c), j + 48)):
                    if k > j and (hit_s(dr, k, wick) or hit_t(dr, k, tgt)):
                        break
                    if l[k] <= ev["ce"] if dr > 0 else h[k] >= ev["ce"]:
                        pnl += 0.5 * ((wlk(dr, ev["ce"], wick, tgt, k, False) - ev["ce"]) * dr) - 0.5 * cost * ev["ce"]; break
                res["половина на краю + половина в CE"] = pnl / plan
            for k in range(j, min(len(c), j + 48)):                      # для сравнения: только CE (вход раньше стопа)
                if l[k] <= ev["ce"] if dr > 0 else h[k] >= ev["ce"]:
                    R = abs(ev["ce"] - wick)
                    if R >= 0.05 * A:
                        res["для сравнения: только CE · 2R"] = (wlk(dr, ev["ce"], wick, ev["ce"] + dr * 2 * R, k, False) - ev["ce"]) * dr / R - cost * ev["ce"] / R
                    break
                if hit_s(dr, k, wick):
                    break
            rows.append(dict(group="idx" if sym in INDEX else "stk", t=int(t5[j]), trend=trend(ev["t"]) == (dr > 0), kz=kz(m), res=res))
    rows.sort(key=lambda x: x["t"])
    names = list(dict.fromkeys(k for x in rows for k in x["res"]))
    print("\n######## C. Вход от края RB: можно ли его спасти")
    for group, gname in [("idx", "Индексы (NQ/ES)"), ("stk", "Акции и ETF")]:
        for fn, f in {"все": lambda x: True, "по тренду": lambda x: x["trend"]}.items():
            print(f"\n=== {gname} · {fn}")
            for nm in names:
                s = stat([x["res"][nm] for x in rows if x["group"] == group and f(x) and nm in x["res"]])
                if s:
                    print(f"  {nm:<46} n={s['n']:>5}  {s['avgR']:+.2f}R  win {s['win']:.0f}%  t {s['t']:+.1f}")


# ---------------------------------------------------------------- D. rb_return
def part_d(D):
    rows = []
    for sym, d in D.items():
        o, h, l, c, t5 = d["k5"]["o"], d["k5"]["h"], d["k5"]["l"], d["k5"]["c"], d["k5"]["t"]
        trend = trend_find(d["k15"])
        for ev in d["trace"]:
            if ev["kind"] != "RB":
                continue
            dr, A, j, edge, wick = (1 if ev["bull"] else -1), ev["atr"], ev["j"], ev["entry"], ev["stop"]
            if sym not in INDEX and not rth(d["mins"][j]):
                continue
            inside = lambda x: (wick < x < edge) if dr > 0 else (edge < x < wick)
            beyond = lambda x: x <= wick if dr > 0 else x >= wick
            deep = l[j] if dr > 0 else h[j]
            reached = l[j] <= ev["ce"] if dr > 0 else h[j] >= ev["ce"]
            armed, fill = inside(c[j]), None
            if beyond(c[j]):
                continue
            for k in range(j + 1, min(len(c), j + 1 + 24)):
                if armed:                                                  # ордер на краю: исполнение при выходе обратно
                    px = (o[k] if o[k] >= edge else edge if h[k] >= edge else None) if dr > 0 else (o[k] if o[k] <= edge else edge if l[k] <= edge else None)
                    if px is not None:
                        fill = (k, px, deep, reached); break
                deep = min(deep, l[k]) if dr > 0 else max(deep, h[k])
                if l[k] <= ev["ce"] if dr > 0 else h[k] >= ev["ce"]:
                    reached = True
                if beyond(c[k]):
                    break
                armed = inside(c[k])
            if not fill:
                continue
            k, px, dp, rc = fill
            res = {}
            for name, stop in [("стоп за тенью RB", wick - dr * 0.02 * A), ("стоп за глубиной захода", dp - dr * 0.02 * A)]:
                R = abs(px - stop)
                if 0.05 * A <= R <= 3 * A:
                    res[name] = {T: walk(d, dr, px, stop, k, T, True) - cost_of(sym) * px / R for T in (2, 3)}
            rows.append(dict(group="idx" if sym in INDEX else "stk", t=int(t5[k]), trend=trend(ev["t"]) == (dr > 0), kz=kz(d["mins"][k]), deepCE=rc, res=res))
    rows.sort(key=lambda x: x["t"])
    print("\n######## D. Вход на возврате из зоны RB")
    F = {"все": lambda x: True, "по тренду": lambda x: x["trend"], "по тренду + киллзона": lambda x: x["trend"] and x["kz"], "дошла до CE": lambda x: x["deepCE"]}
    for group, gname in [("idx", "Индексы (NQ/ES)"), ("stk", "Акции и ETF")]:
        print(f"\n=== {gname}")
        for fn, f in F.items():
            for sn in ["стоп за тенью RB", "стоп за глубиной захода"]:
                lst = [x for x in rows if x["group"] == group and f(x) and sn in x["res"]]
                s2, s3 = stat([x["res"][sn][2] for x in lst]), stat([x["res"][sn][3] for x in lst])
                if s2:
                    print(f"  {fn:<22} {sn:<24} n={s2['n']:>5}  2R {s2['avgR']:+.2f} (win {s2['win']:.0f}%, t {s2['t']:+.1f})  3R {s3['avgR']:+.2f}")


if __name__ == "__main__":
    D = {}
    for sym in INDEX + STOCKS:
        d = load(sym)
        if d:
            D[sym] = d
            print(f"{sym}: {len(d['trace'])} событий")
    part_a({s: D[s] for s in INDEX})
    part_b(D)
    part_c(D)
    part_d(D)
