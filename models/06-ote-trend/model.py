"""
06 · OTE + тренд старшего таймфрейма.   Запуск:  python models/06-ote-trend/model.py

Откат в зону OTE по тренду. Заранее заданы 20 вариантов:
    тренд: 15m SMA50 · 1h SMA50 · 4h SMA50 · день SMA20 · «1h и день согласны»
    вход:  лимит 0.62 / 0.705 / 0.79 без PD-массива · CE PD-массива (как в модели 05)
Основная модель выбрана ДО запуска: 1h + день, лимит 0.705. При 20 вариантах значимым можно считать только t ≳ 2.8.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from qm.intraday import klines  # noqa: E402
from qm.setups import OTE, TREND_1H, TREND_4H, TREND_DAY, bot, drive, fmt, monte_carlo, pick, random_baseline, stat, trend_fn, trend_stack  # noqa: E402

TRENDS = [("15m SMA50", trend_fn), ("1h SMA50", TREND_1H), ("4h SMA50", TREND_4H), ("день SMA20", TREND_DAY), ("1h + день", trend_stack)]
ENTRIES = [("лимит 0.62", dict(pda=False, levels=(0.62,))), ("лимит 0.705", dict(pda=False, levels=(0.705,))),
           ("лимит 0.79", dict(pda=False, levels=(0.79,))), ("CE PD-массива", {})]

if __name__ == "__main__":
    data, sigs = {}, {(tn, en): [] for tn, _ in TRENDS for en, _ in ENTRIES}
    for sym in ["QQQ", "SPY"]:
        k15, k5 = klines(sym, "15m"), klines(sym, "5m")
        data[sym] = (k15, k5)
        strats = {(tn, en): OTE(k15, k5, trendOf=tf, **eo) for tn, tf in TRENDS for en, eo in ENTRIES}
        drive(k15, k5, list(strats.values()))                    # 20 вариантов за один проход движка
        for k, st in strats.items():
            for s in st.out: s["sym"] = sym; sigs[k].append(s)

    S = lambda lst: stat([x["res"] for x in lst])
    print("== Средний R (t, сделок), бот NQ+ES\n" + " " * 14 + "".join(f"{en:<24}" for en, _ in ENTRIES))
    for tn, _ in TRENDS:
        cells = []
        for en, _ in ENTRIES:
            s = S(bot(pick(sigs[(tn, en)])))
            cells.append(f"{s['avgR']:+.2f}R (t {s['t']:+.1f}, {s['n']})" if s else "мало")
        print(f"{tn:<14}" + "".join(f"{c:<24}" for c in cells))

    main = sigs[("1h + день", "лимит 0.705")]
    B = bot(pick(main))
    a = S(B)
    print(f"\n== Основная (1h + день, лимит 0.705): {fmt(a)}")
    for sym, nm in [("QQQ", "NQ"), ("SPY", "ES")]:
        print(f"  {nm}: {fmt(S([x for x in B if x['sym'] == sym]))}")
    for k in ["2|none", "3|none", "5|none", "leg|none", "3|ih", "5|1R"]:
        print(f"  выход {k:<10} {fmt(S(bot(pick(main, k))))}")
    rnd = random_baseline(data, B, 300, trend_stack)
    mc = monte_carlo([x["res"] for x in B])
    print(f"  случайные входы по тому же тренду: 5/50/95% = {rnd[15]:+.2f} / {rnd[150]:+.2f} / {rnd[285]:+.2f}R → случай не хуже в {sum(x >= a['avgR'] for x in rnd) / len(rnd):.0%}")
    print(f"  Монте-Карло: итог ≤ 0 в {mc['pLoss']:.0%}; просадка медиана {mc['dd50']:.1f}R, худшие 5% {mc['dd5']:.1f}R; стопов подряд 95% — {mc['streak95']}")
