"""
04 · RB entry model (RBV): сначала волна и фибо, потом rejection block в откате.   Запуск:  python models/04-rb-video/model.py [--crypto]

Правила: волна = свинг за 4 часа по тренду ≥ 3 ATR, фибо после первой встречной свечи; в откате — RB 5m с тенью в OTE
(≥ 0.62) и началом в дисконте (≥ 0.5), контекст (свип / OB-FVG 15m / открытие 00:00-10:00), подтверждающее закрытие.
Лимит на начало RB живёт 3 свечи, стоп за тенью + 0.1 ATR, цель 5R, безубыток после снятия внутреннего хая.

Проверки: до / после заморозки правил (10.10.2026), случайные входы, Монте-Карло, сдвиг каждого параметра,
и с флагом --crypto — BTC/ETH за 2019–2026 (сотни тысяч свечей, несколько минут).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qm.intraday import klines  # noqa: E402
from qm.setups import RBVid, bot, drive, fmt, monte_carlo, pick, random_baseline, stat  # noqa: E402

FROZEN = 1791670800000            # 2026-10-10 22:00 UTC — правила RBV зафиксированы в терминале
ROBUST = [("волна ≥ 2 ATR (база 3)", dict(legATR=2)), ("волна ≥ 4 ATR", dict(legATR=4)), ("тень до 0.5 (база 0.62)", dict(oteLo=0.5)),
          ("тень до 0.705", dict(oteLo=0.705)), ("тень RB ≥ 35% (база 50%)", dict(wickPct=0.35)), ("тень RB ≥ 65%", dict(wickPct=0.65)),
          ("лимит 1 свеча (база 3)", dict(wait=1)), ("лимит 6 свечей", dict(wait=6)), ("стоп без буфера", dict(stopBuf=0)), ("стоп + 0.25 ATR", dict(stopBuf=0.25)),
          ("подтверждение той же свечой", dict(confirmBars=0)), ("подтверждение до 4 свечей", dict(confirmBars=4)),
          ("волна за 2 ч (база 4 ч)", dict(legBars=24)), ("волна за 8 ч", dict(legBars=96))]

if __name__ == "__main__":
    data, base, variants = {}, [], {nm: [] for nm, _ in ROBUST}
    for sym in ["QQQ", "SPY"]:
        k15, k5 = klines(sym, "15m"), klines(sym, "5m")
        data[sym] = (k15, k5)
        main = RBVid(k15, k5)
        rob = {nm: RBVid(k15, k5, **o) for nm, o in ROBUST}
        drive(k15, k5, [main, *rob.values()])                   # один проход движка: база + 14 сдвигов параметров
        for s in main.out: s["sym"] = sym; base.append(s)
        for nm, st in rob.items():
            for s in st.out: s["sym"] = sym; variants[nm].append(s)

    S = lambda lst: stat([x["res"] for x in lst])
    B = bot(pick(base))
    print("== RBV на NQ/ES (бот: тренд + ≤3 в день + стоп после 2 убытков, 5R, БУ после внутр. хая)")
    for name, lst in [("весь период", B), ("до заморозки правил", [x for x in B if x["fillT"] < FROZEN]), ("ПОСЛЕ 10.10 — тест вперёд", [x for x in B if x["fillT"] >= FROZEN]),
                      ("  NQ", [x for x in B if x["sym"] == "QQQ"]), ("  ES", [x for x in B if x["sym"] == "SPY"]),
                      ("все исполненные, без фильтров бота", pick(base)), ("против тренда (контроль)", pick(base, "5|ih", lambda s: not s["trend"]))]:
        print(f"  {name:<40} {fmt(S(lst))}")
    for k in ["2|none", "3|none", "5|none", "5|1R", "leg|ih"]:
        print(f"  выход {k:<34} {fmt(S(bot(pick(base, k))))}")

    a = S(B)
    rnd = random_baseline(data, B, 300)
    p_beat = sum(x >= a["avgR"] for x in rnd) / len(rnd)
    mc = monte_carlo([x["res"] for x in B])
    print(f"\n  случайные входы (300): 5/50/95% = {rnd[15]:+.2f} / {rnd[150]:+.2f} / {rnd[285]:+.2f}R → случай не хуже модели в {p_beat:.0%}")
    print(f"  Монте-Карло: итог ≤ 0 в {mc['pLoss']:.0%}; просадка медиана {mc['dd50']:.1f}R, худшие 5% {mc['dd5']:.1f}R; стопов подряд 95% — {mc['streak95']}")

    print("\n== Устойчивость (бот NQ/ES)")
    for nm, _ in ROBUST:
        s = S(bot(pick(variants[nm])))
        print(f"  {nm:<36} {s['avgR']:+.2f}R  t {s['t']:+.1f}  n {s['n']}" if s else f"  {nm:<36} мало")

    if "--crypto" in sys.argv:
        print("\n== BTC и ETH (перпы, издержки 0.07% за круг)")
        for sym in ["BTC", "ETH"]:
            k15, k5 = klines(sym, "15m"), klines(sym, "5m")
            st = RBVid(k15, k5, cost=0.0007)
            drive(k15, k5, [st], proxy=False)
            for s in st.out: s["sym"] = sym
            cb = bot(pick(st.out))
            print(f"  {sym}: бот {fmt(S(cb))}")
            print(f"  {sym}: без издержек {fmt(S([dict(x, res=x['res'] + 0.0007 * x['entry'] / x['R']) for x in cb]))}")
