"""
03 · RB + фибо (стратегия из ТЗ).   Запуск:  python models/03-rb-fib/model.py

Rejection block 5m (длинная тень ≥ 50% свечи и ≥ 0.5 ATR) в контексте (свип ликвидности / OB-FVG 15m / открытие),
фибо от тени RB до экстремума импульса ≥ 2 ATR, лимит на открытие RB, пока оно в OTE 0.62–0.79, стоп за тенью, 5R.
Сравнение: без фибы и вход в середину RB (CE). Индексы (QQQ/SPY = NQ/ES, издержки CME) и 10 акций (издержки 2 б.п.).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qm.intraday import klines  # noqa: E402
from qm.setups import RBFib, drive, pick, risk_rules, stat  # noqa: E402

INDEX, STOCKS = ["QQQ", "SPY"], ["IWM", "SMH", "NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "TSLA", "AVGO"]
VARIANTS = {"fib": ("с фибой OTE", {}), "nofib": ("без фибы", dict(fibLo=0, fibHi=1, impATR=0)), "ce": ("с фибой, вход в CE", dict(entryAt="ce"))}

if __name__ == "__main__":
    sigs = {k: [] for k in VARIANTS}
    for sym in INDEX + STOCKS:
        k15, k5 = klines(sym, "15m"), klines(sym, "5m")
        if len(k5["t"]) < 3000:
            continue
        base = dict(cost=0.00004 if sym in INDEX else 0.0002, rth=sym not in INDEX)
        strats = {k: RBFib(k15, k5, **base, **opts) for k, (_, opts) in VARIANTS.items()}
        drive(k15, k5, list(strats.values()))                 # один проход ICT-движка на все варианты
        for k, s in strats.items():
            for cd in s.out:
                cd["sym"], cd["group"] = sym, "idx" if sym in INDEX else "stk"
                sigs[k].append(cd)
        print(f"{sym}: {len(strats['fib'].out)} ордеров (с фибой)")

    for group, gname in [("idx", "Индексы (NQ/ES)"), ("stk", "Акции и ETF")]:
        print(f"\n================ {gname}")
        for k, (label, _) in VARIANTS.items():
            g = [s for s in sigs[k] if s["group"] == group]
            for T in [5, 3, 2, "pool"]:
                tr = lambda f=lambda s: True: pick(g, T, f)
                b = sorted([x for sym in dict.fromkeys(s["sym"] for s in g) for x in risk_rules(tr(lambda s, sym=sym: s["sym"] == sym))], key=lambda s: s["fillT"])
                for name, lst in [("все исполненные", tr()), ("по тренду", tr(lambda s: s["trend"])), ("БОТ (тренд + ≤3 в день + стоп после 2 убытков)", b)]:
                    st = stat([x["res"] for x in lst], win_cut=0)
                    if st:
                        print(f"  {('пул' if T == 'pool' else f'{T}R'):<4} {label} · {name:<48} n={st['n']:>5} win {st['win']:.0f}%  {st['avgR']:+.2f}R  t {st['t']:+.1f}  сумма {st['sumR']:+.0f}R  DD {st['maxDD']:.0f}R")
