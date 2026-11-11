// Step 13 — improving the best retail-CFD strategy found so far (risk parity + SMA200, step 11) for an offshore
// forex / CFD broker such as HeroFX: indices, gold, silver, oil as CFDs; no bond CFDs assumed; swaps not published,
// so the financing markup is a parameter (0 / 1.5 / 2.5 / 4 %/yr) to be replaced by the platform's real swap table.
// Improvements, each with a reason known before looking at results, DESIGNED on 2007–2016 and CHECKED on 2017–2026:
//   B. class balance — equities / metals / oil get equal risk (7 indices are one bet: corr 0.8 with US500)
//   C. faster risk control — daily check, 60-day covariance, 25% no-trade band (step 11: monthly rebalance halved Sharpe)
//   D. B + C          E. D without oil (oil CFDs bleed roll costs — partly hindsight, flagged)
//   node step13_herofx.js
const fs = require("fs");
const path = require("path");
const { buildData, backtest, metrics } = require("../../lib/portfolio.js");
const F = (key, sym, cost, cls) => ({ key, sym, fund: true, rate: null, cost, markup: 0.025, rp: true, cls, maxMove: 0.3 });
const A = [F("US500", "SPY", 1, "eq"), F("US100", "QQQ", 1, "eq"), F("US30", "DIA", 1.5, "eq"), F("US2000", "IWM", 1.5, "eq"), F("GER40", "EWG", 1.5, "eq"), F("JP225", "EWJ", 1.5, "eq"), F("UK100", "EWU", 1.5, "eq"),
  F("XAU", "GLD", 3, "metal"), F("XAG", "SLV", 5, "metal"), F("OIL", "USO", 4, "oil")];
const pc = (x, d = 1) => (x == null ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");
(async () => {
  const D = await buildData(A, "2004-01-01"), { dates } = D, T = dates.length;
  const start = dates[Math.max(...D.active.map((a) => a.indexOf(true)))], split = "2017-01-01", end = "2100";
  const cnt = { eq: 7, metal: 2, oil: 1 }, wts = Object.fromEntries(A.map((a) => [a.key, 1 / cnt[a.cls]]));
  const noOil = { ...D, active: D.active.map((a, i) => (A[i].key === "OIL" ? a.map(() => false) : a)) };
  const fast = { rebal: 1, covL: 60, band: 0.25 };
  const V = [["A. база: risk parity + SMA200 (шаг 11, без облигаций)", D, {}], ["B. баланс классов", D, { wts }], ["C. быстрый контроль риска", D, fast], ["D. B + C", D, { wts, ...fast }], ["E. D без нефти", noOil, { wts, ...fast }]];
  const rep = { start, split, rows: [], markup: [] };
  console.log(`${start} → ${dates[T - 1]}; разработка до ${split}, проверка после. CFD: ставка + наценка 2.5% + спред, риск 10%/год\n`);
  console.log("".padEnd(54) + "РАЗРАБОТКА 2007–16        ПРОВЕРКА 2017–26          ВЕСЬ ПЕРИОД");
  const res = {};
  for (const [name, data, o] of V) {
    const bt = backtest(data, { strategy: "rpsma", ...o }), a = metrics(dates, bt.ret, start, split), b = metrics(dates, bt.ret, split, end), m = metrics(dates, bt.ret, start, end);
    res[name] = bt; rep.rows.push({ name, design: a, test: b, all: m });
    const s = (x) => `Sharpe ${x.sharpe.toFixed(2)} ${pc(x.cagr).padStart(6)} DD ${pc(x.maxDD, 0).padStart(4)}`;
    console.log(`${name.padEnd(54)}${s(a)}   ${s(b)}   ${s(m)}`);
  }
  const bestName = rep.rows.slice().sort((x, y) => y.design.sharpe - x.design.sharpe)[0].name, [, bd, bo] = V.find((v) => v[0] === bestName);
  rep.chosen = bestName;
  console.log(`\nВыбор по периоду разработки: ${bestName}\n== Чувствительность выбранной модели к свопам брокера (Sharpe разработка / проверка / весь, доходность в год)`);
  for (const mk of [0, 0.6, 1, 1.6]) {
    const bt = backtest(bd, { strategy: "rpsma", ...bo, markup: mk === 0 ? 0 : 0.01, markupMult: mk }), a = metrics(dates, bt.ret, start, split), b = metrics(dates, bt.ret, split, end), m = metrics(dates, bt.ret, start, end);
    rep.markup.push({ markup: mk * 2.5, design: a.sharpe, test: b.sharpe, all: m.sharpe, cagr: m.cagr, dd: m.maxDD });
    console.log(`  наценка ${(mk * 2.5).toFixed(1)}%/год:  ${a.sharpe.toFixed(2)} / ${b.sharpe.toFixed(2)} / ${m.sharpe.toFixed(2)}   ${pc(m.cagr)} в год, просадка ${pc(m.maxDD, 0)}`);
  }
  const bt = res[bestName], per = (a, b) => { let eq = 1; for (let t = 0; t < T; t++) if (dates[t] >= a && dates[t] < b) eq *= 1 + bt.ret[t]; return eq - 1; };
  rep.crises = { "2008": per("2008-09-01", "2009-03-10"), "2020": per("2020-02-19", "2020-03-24"), "2022": per("2022-01-01", "2023-01-01"), "2025": per("2025-02-19", "2025-04-09") };
  const ix = dates.map((_, t) => t).filter((t) => dates[t] >= start), lev = ix.map((t) => bt.lev[t]);
  const yrs = [...new Set(dates.filter((d) => d >= start).map((d) => d.slice(0, 4)))].map((y) => per(`${y}-01-01`, `${+y + 1}-01-01`));
  console.log(`  кризисы: ${Object.entries(rep.crises).map(([k, v]) => `${k} ${pc(v, 0)}`).join(", ")}; лет в плюсе ${yrs.filter((x) => x > 0).length}/${yrs.length}; плечо среднее ${(lev.reduce((a, x) => a + x, 0) / lev.length).toFixed(2)}x, макс ${Math.max(...lev).toFixed(2)}x`);
  const last = bt.wHist[bt.wHist.length - 1]; rep.last = { date: last[0], w: Object.fromEntries(A.map((a, i) => [a.key, last[i + 1]])) };
  console.log(`  позиции модели на ${last[0]} (доля счёта): ` + A.map((a, i) => `${a.key} ${last[i + 1]}`).join("  "));
  fs.writeFileSync(path.join(__dirname, "results", "step13_herofx.json"), JSON.stringify(rep, null, 1));
})();
