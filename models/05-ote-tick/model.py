"""
05 · OTE «top / bottom tick».   Запуск:  python models/05-ote-tick/model.py [--stocks]

Правила (coach/ПРАВИЛА.md, заданы до теста): волна по тренду за 4 часа ≥ 3 ATR → обязательный CE 15m RB/OB/FVG у уровня
0.62 / 0.705 / 0.79 (допуск 0.15 ATR) + дисконт + ещё одно совпадение (второй CE / открытие 00:00-09:30-10:00 /
неснятые лои перед уровнем). Лимит на самом мелком уровне, в 9:30–10:00 — только после триггера на 5m. Стоп за началом
волны, цель 5R, безубыток после снятия внутреннего хая. Бот: ≤ 3 сделок в день, стоп после 2 убытков, только по тренду.

Проверки: разрезы, сдвиг каждого правила (устойчивость), случайные входы, Монте-Карло; --stocks — те же правила на 10 акциях.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qm.intraday import klines  # noqa: E402
from qm.setups import OTE, bot, drive, fmt, monte_carlo, pick, random_baseline, stat  # noqa: E402

ROBUST = [(f"допуск CE↔уровень {x}×ATR (база 0.15)", dict(tolATR=x)) for x in (0.05, 0.1, 0.25, 0.4)] + \
         [(f"минимум {x} совпадения (база 3)", dict(minConf=x)) for x in (2, 4)] + \
         [("консервативный вход везде", dict(mode="cons")), ("стоп всегда за пивотом", dict(stopMode="pivot")), ("стоп всегда за 0.79", dict(stopMode="79"))] + \
         [(f"волна ≥ {x}×ATR (база 3)", dict(legATR=x)) for x in (2, 4)] + \
         [("уровни только 0.62 и 0.705", dict(levels=(0.62, 0.705))), ("уровни 0.5–0.79", dict(levels=(0.5, 0.62, 0.705, 0.79)))]


def session(m):
    return "Азия (18:00–02:00)" if m >= 1080 or m < 120 else "Лондон (02:00–07:00)" if m < 420 else "Нью-Йорк (07:00–16:45)"


if __name__ == "__main__":
    data, base, rob = {}, [], {nm: [] for nm, _ in ROBUST}
    for sym in ["QQQ", "SPY"]:
        k15, k5 = klines(sym, "15m"), klines(sym, "5m")
        data[sym] = (k15, k5)
        main, variants = OTE(k15, k5), {nm: OTE(k15, k5, **o) for nm, o in ROBUST}
        drive(k15, k5, [main, *variants.values()])
        for s in main.out: s["sym"] = sym; base.append(s)
        for nm, st in variants.items():
            for s in st.out: s["sym"] = sym; rob[nm].append(s)

    S = lambda lst: stat([x["res"] for x in lst])
    B = bot(pick(base))
    rows = [("все исполненные", pick(base)), ("против тренда", pick(base, "5|ih", lambda s: not s["trend"])), ("по тренду", pick(base, "5|ih", lambda s: s["trend"])),
            ("БОТ", B), ("БОТ без издержек", [dict(x, res=x["res"] + 0.00004 * x["entry"] / x["R"]) for x in B]),
            ("NQ", [x for x in B if x["sym"] == "QQQ"]), ("ES", [x for x in B if x["sym"] == "SPY"])]
    rows += [(f"вход на {lv}", [x for x in B if x["lv"] == lv]) for lv in (0.62, 0.705, 0.79)]
    rows += [(nm, [x for x in B if k in x["kinds"]]) for k, nm in [("stack", "есть второй CE"), ("open", "есть ключевое открытие"), ("liq", "есть ликвидность перед уровнем")]]
    rows += [(sn, [x for x in B if session(x["min"]) == sn]) for sn in ("Азия (18:00–02:00)", "Лондон (02:00–07:00)", "Нью-Йорк (07:00–16:45)")]
    rows += [(f"выход {T}|{be}", bot(pick(base, f"{T}|{be}"))) for T in (2, 3, 5, "leg") for be in ("none", "1R", "ih")]
    print("== OTE на NQ/ES")
    for name, lst in rows:
        print(f"  {name:<34} {fmt(S(lst))}")

    print("\n== Устойчивость (бот)")
    for nm, _ in ROBUST:
        s = S(bot(pick(rob[nm])))
        print(f"  {nm:<40} " + (f"n={s['n']:>4} {s['avgR']:+.2f}R  t {s['t']:+.1f}  DD {s['maxDD']:.1f}R" if s else "мало сделок"))

    a = S(B)
    rnd = random_baseline(data, B, 300)
    mc = monte_carlo([x["res"] for x in B])
    print(f"\n== Случайные входы (300): 5/50/95% = {rnd[15]:+.2f} / {rnd[150]:+.2f} / {rnd[285]:+.2f}R; модель {a['avgR']:+.2f}R; "
          f"случай не хуже модели в {sum(x >= a['avgR'] for x in rnd) / len(rnd):.0%}")
    print(f"== Монте-Карло: итог ≤ 0 в {mc['pLoss']:.0%}; итог 5/50/95% = {mc['sum5']:+.0f} / {mc['sum50']:+.0f} / {mc['sum95']:+.0f}R; "
          f"просадка медиана {mc['dd50']:.1f}R, худшие 5% {mc['dd5']:.1f}R; серия стопов медиана {mc['streak50']}, 95% — {mc['streak95']}")

    if "--stocks" in sys.argv:
        print("\n== Те же правила на 10 акциях (часы США, издержки 2 б.п.)")
        st_sigs = []
        for sym in ["IWM", "SMH", "NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "TSLA", "AVGO"]:
            k15, k5 = klines(sym, "15m"), klines(sym, "5m")
            if len(k5["t"]) < 3000:
                continue
            s = OTE(k15, k5, rth=True, cost=0.0002)
            drive(k15, k5, [s])
            for x in s.out: x["sym"] = sym; st_sigs.append(x)
        SB = bot(pick(st_sigs))
        print(f"  бот, все сделки                    {fmt(S(SB))}")
        print(f"  есть ключевое открытие у входа     {fmt(S([x for x in SB if 'open' in x['kinds']]))}")
