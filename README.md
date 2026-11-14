# Quant models

Исследование торговых моделей: от ICT-сетапов внутри дня до портфелей фондового типа. Каждая модель — отдельная папка
с правилами, честным бэктестом (издержки, случайные входы, Монте-Карло, проверка вне выборки) и выводом.
Правила задаются **до** запуска; варианты, найденные после, помечаются.

| # | Модель | Рынки | Ключевой результат | Статус |
|---|---|---|---|---|
| 01 | [Macro regime filter](models/01-macro-regime) | Nasdaq-100, S&P 500 · daily · 2008–2026 | 1.06 (NDX, тренд + макро, 2017–26) | 🟡 risk filter |
| 02 | [ICT zones: RB / OB / FVG, key opens](models/02-ict-zones) | NQ/ES (QQQ/SPY perps) + 10 stocks · 5–15m · 2026 | +0.20R/сделка (RB CE по тренду) | 🟡 RB CE — forward test |
| 03 | [RB + Fibonacci confluence](models/03-rb-fib) | NQ/ES + 10 stocks · 5m · 2026 | ≈0R | 🔴 no edge |
| 04 | [RB entry model (RBV)](models/04-rb-video) | NQ/ES · 5m · 2026; BTC/ETH 2019–2026 | +0.29R/сделка (NQ/ES, t 1.5) | 🟡 candidate |
| 05 | [OTE top / bottom tick](models/05-ote-tick) | NQ/ES · 5m · 2026 | +0.03R/сделка (t 0.1) | 🔴 no edge · forward journal |
| 06 | [OTE + trend](models/06-ote-trend) | NQ/ES · 5m · 2026 | −0.01R/сделка (403 сделки) | 🔴 no edge |
| 07 | [Intraday momentum & opening range breakout](models/07-intraday-momentum-orb) | NQ/ES (+IWM, DIA) · 1h 2023–2026, 5m 2026 | −0.77 (моментум) / +0.41 (ORB-60, t 0.8) | 🔴 no edge |
| 08 | [FX trend + carry](models/08-fx-trend-carry) | 9 USD pairs + gold · daily · 2004–2026 | −0.05 (0.18 без наценки) | 🔴 no edge (retail) |
| 09 | [NQ / ES daily on CFD](models/09-index-cfd-daily) | NQ, ES · daily · 2002–2026 | 0.65 NQ B&H · 0.58 SMA200 | 🟡 risk management |
| 10 | [Risk parity on a CFD account](models/10-cfd-risk-parity) | 21 markets (indices, bonds, metals, oil, FX) · daily · 2007–2026 | 0.37 (0.64 без наценки) | 🟡 basis for model E |
| 11 | [Model E — risk parity + SMA200 for a CFD broker](models/11-model-e) | 7 index CFDs + gold + silver · daily · 2007–2026 · live on demo | 0.58 без наценки · 0.18 на свопах HeroFX | 🟢 live (demo) |

🟢 работает на демо · 🟡 кандидат / полезно как риск-фильтр · 🔴 преимущества нет

## Главные выводы
1. **Механические внутридневные правила** на NQ/ES (ICT-зоны, OTE, моментум, ORB) после издержек дают около нуля;
   при 400+ сделках «плюсы» малых выборок исчезают.
2. **Издержки важнее сигнала.** Те же портфельные модели на фьючерсах дают Sharpe ~0.6, на CFD с розничными свопами — 0.2–0.4.
3. **Работают премии за риск + риск-менеджмент:** лонг индексов и золота, размер по волатильности, фильтр SMA200,
   диверсификация. Тренд — страховка в кризисы, а не источник дохода.
4. **Прохождение проп-челленджа ничего не доказывает:** стратегия без преимущества проходит его в 35–50% попыток.

## Python (переход с JavaScript)
Модели переписываются на Python — основной язык квантов. Готово: **11 · Model E** (цифры совпадают с JS до сотых).
Общий код — пакет `qm/` (`data.py` — данные, `risk.py` — метрики риска с объяснениями, `portfolio.py` — движок портфеля).
Как разобраться в коде, не будучи программистом: **[КАК_ЧИТАТЬ_КОД.md](КАК_ЧИТАТЬ_КОД.md)**.

```
python models/11-model-e/model_e.py
```

## Структура
```
qm/             Python: data.py, risk.py, portfolio.py
lib/            JS-движок: ictengine.js, rbfib.js (ICT-уровни и RB-модели), intraday.js (5m-бэктест, статистика,
                случайные входы, Монте-Карло), portfolio.js (дневные портфели: тренд, керри, risk parity)
models/NN-*/    модель: README.md, скрипты, results/
data-cache/     скачанные данные (Binance, Yahoo, FRED) — не в git, создаётся при первом запуске
```

## Запуск
Python 3.10+ с numpy и pandas (`pip install numpy pandas`) или Node.js 18+ для JS-версий. `cd models/11-model-e && node backtest.js`. Данные качаются при первом запуске и кэшируются.

_Исследование, не инвестиционный совет. Бэктест не гарантирует будущих результатов._
