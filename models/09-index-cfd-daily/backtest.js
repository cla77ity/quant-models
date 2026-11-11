// Step 10 — NQ and ES as daily positions on a retail CFD account (financing paid every day a position is held).
// Futures closes from Yahoo (NQ=F, ES=F, 2000 → now) already include the market's financing and dividends, so the
// CFD's extra cost is the broker's markup: 2.5%/yr on the notional held (0% = trading real CME futures), plus ~1 bp
// spread per unit traded. Strategies, fixed in advance:
//   buy & hold (1x) · SMA200 filter (long above the 200-day average, flat below) · SMA50 filter · vol-targeted long
//   (15%/yr) · SMA200 + vol target · trend long/short (1/3/12-month signs, 15%) · trend long-only.
// Then the step-9 trend book with NQ and ES added (9 FX pairs + gold + 2 indices, 10% vol): do indices rescue it?
//   node step10_index_cfd.js
const fs = require("fs");
const path = require("path");
const { ASSETS: FX, buildData, backtest, metrics } = require("../../lib/portfolio.js");

const IDX = [{ key: "NQ", sym: "NQ=F", inv: false, rate: null, cost: 1.0, maxMove: 0.25, markup: 0.025 }, { key: "ES", sym: "ES=F", inv: false, rate: null, cost: 1.0, maxMove: 0.25, markup: 0.025 }];
const pc = (x, d = 1) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");

(async () => {
  const D = await buildData(IDX, "2000-01-01"), { dates } = D, T = dates.length;
  const start = dates[260 + 252], end = "2100", split = "2014-01-01";
  console.log(`NQ/ES: ${dates[0]} → ${dates[T - 1]}, торговля с ${start}; плохих тиков ${JSON.stringify(D.bad)}`);
  const STRATS = [
    ["Купить и держать (1x)", { strategy: "long", fixed: true }],
    ["SMA200: в лонге выше средней (1x)", { strategy: "sma", smaN: 200, fixed: true }],
    ["SMA50: в лонге выше средней (1x)", { strategy: "sma", smaN: 50, fixed: true }],
    ["Лонг с таргетом волатильности 15%", { strategy: "long", target: 0.15 }],
    ["SMA200 + таргет волатильности 15%", { strategy: "sma", smaN: 200, target: 0.15 }],
    ["Тренд лонг/шорт 1/3/12 мес. (15%)", { strategy: "trend", target: 0.15 }],
    ["Тренд только лонг (15%)", { strategy: "trendLO", target: 0.15 }],
  ];
  const rep = { start, end: dates[T - 1], rows: [], crises: {}, multi: {} };
  const CR = [["Lehman, 2008", "2008-01-01", "2009-03-10"], ["COVID, фев–мар 2020", "2020-02-19", "2020-03-24"], ["2022", "2022-01-01", "2023-01-01"], ["Апрель 2025 (пошлины)", "2025-02-19", "2025-04-09"]];
  const period = (ret, a, b) => { let eq = 1; for (let t = 0; t < T; t++) if (dates[t] >= a && dates[t] < b) eq *= 1 + ret[t]; return eq - 1; };
  for (const key of ["NQ", "ES"]) {
    console.log(`\n== ${key}: CFD с наценкой 2.5%/год  |  в скобках: настоящие фьючерсы CME без наценки`);
    for (const [name, o] of STRATS) {
      const cfd = backtest(D, { ...o, only: key, rebal: 1 }), fut = backtest(D, { ...o, only: key, rebal: 1, markup: 0 });
      const m = metrics(dates, cfd.ret, start, end), mf = metrics(dates, fut.ret, start, end), a = metrics(dates, cfd.ret, start, split), b = metrics(dates, cfd.ret, split, end);
      const cr = Object.fromEntries(CR.map(([nm, x, y]) => [nm, period(cfd.ret, x, y)]));
      rep.rows.push({ key, name, ...m, futSharpe: mf.sharpe, futCagr: mf.cagr, h1: a.sharpe, h2: b.sharpe, crises: cr });
      console.log(`  ${name.padEnd(36)} год ${pc(m.cagr).padStart(7)} (${pc(mf.cagr)})  Sharpe ${m.sharpe.toFixed(2)} (${mf.sharpe.toFixed(2)})  просадка ${pc(m.maxDD, 0).padStart(5)}  до/после 2014: ${a.sharpe.toFixed(2)}/${b.sharpe.toFixed(2)}  | ${CR.map(([nm]) => pc(cr[nm], 0)).join(" ")}`);
    }
  }
  console.log(`  (кризисы: ${CR.map(([nm]) => nm).join(" · ")})`);

  // multi-asset trend book: FX + gold (step 9) vs + NQ/ES — same engine, rules and costs
  console.log("\n== Тренд-портфель 10% волатильности: валюты + золото (шаг 9) против валюты + золото + NQ + ES");
  const DM = await buildData([...FX, ...IDX]), s = DM.dates[260 + 252];
  for (const [name, assets] of [["валюты + золото", FX.map((a) => a.key)], ["валюты + золото + NQ + ES", [...FX, ...IDX].map((a) => a.key)], ["только NQ + ES + золото", ["NQ", "ES", "XAU"]]]) {
    const sub = { ...DM, active: DM.active.map((act, i) => (assets.includes(DM.assets[i].key) ? act : act.map(() => false))) };
    const m = metrics(DM.dates, backtest(sub, { strategy: "trend" }).ret, s, end), a = metrics(DM.dates, backtest(sub, { strategy: "trend" }).ret, s, split), b = metrics(DM.dates, backtest(sub, { strategy: "trend" }).ret, split, end);
    rep.multi[name] = { ...m, h1: a.sharpe, h2: b.sharpe };
    console.log(`  ${name.padEnd(28)} год ${pc(m.cagr).padStart(7)}  Sharpe ${m.sharpe.toFixed(2)}  просадка ${pc(m.maxDD, 0)}  до/после 2014: ${a.sharpe.toFixed(2)}/${b.sharpe.toFixed(2)}`);
  }
  fs.writeFileSync(path.join(__dirname, "results", "step10_index_cfd.json"), JSON.stringify(rep, null, 1));
})();
