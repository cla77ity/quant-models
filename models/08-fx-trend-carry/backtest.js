const fs = require("fs");
const path = require("path");
const { ASSETS, buildData, backtest, metrics } = require("../../lib/portfolio.js");
const OUT = path.join(__dirname, "results");
fs.mkdirSync(OUT, { recursive: true });

// Step 9 — how an FX fund would trade a retail account: trend + carry on 9 USD pairs and gold, daily data 2004 → now.
//  TREND: per asset the average of sign(return over 1 / 3 / 12 months) (−1…+1), sized by inverse volatility.
//  CARRY: rank the 9 currencies by (3-month rate − USD rate); long the top 3, short the bottom 3 against USD.
//  COMBO: half of each. Every portfolio is scaled to 10% annual volatility (ex-ante, 120-day covariance), gross ≤ 6x,
//  rebalanced weekly. All fixed in advance; the sensitivity block shows how much the result moves when they move.
//  Retail reality: spread + commission per unit traded (1–5 bp by pair, gold 3 bp), a broker swap markup of 1%/yr on
//  every held position on top of the true rate differential (0% / 2% as variants), rates used with a 2-month lag.
//  Data: Yahoo daily closes (FX pairs, GC=F front-month gold futures), FRED OECD 3-month interbank rates.
//   node backtest.js → results/step9_fx_trend.json, then node report.js
(async () => {
  const D = await buildData(ASSETS), { dates, R, carry, bad } = D, T = dates.length;
  const start = dates[260 + 252], end = "2100";
  console.log(`Даты ${dates[0]} → ${dates[T - 1]}, торговля с ${start}; выброшено плохих тиков: ${JSON.stringify(bad)}`);

  // ---- strategies
  const S = {
    "Тренд": backtest(D, { strategy: "trend" }),
    "Керри": backtest(D, { strategy: "carry" }),
    "Тренд + керри": backtest(D, { strategy: "combo" }),
    "Золото: купить и держать": backtest(D, { strategy: "gold", markup: 0.01 }),
  };
  // EUR/USD buy & hold as a "single pair" benchmark
  const eurBH = new Array(T).fill(0); for (let t = 1; t < T; t++) eurBH[t] = R[0][t] + carry[0][t] - 0.01 / 252;
  const f = (x, d = 1) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");
  const rep = { start, end: dates[T - 1], bad, main: {}, years: {}, crises: {}, halves: {}, sens: [], curves: {} };
  const line = (name, ret) => { const m = metrics(dates, ret, start, end); rep.main[name] = m; console.log(`  ${name.padEnd(26)} год ${f(m.cagr).padStart(7)}  вол ${f(m.vol).padStart(6)}  Sharpe ${m.sharpe.toFixed(2).padStart(5)} (t ${m.tstat.toFixed(1)})  просадка ${f(m.maxDD).padStart(7)}  Calmar ${m.calmar ? m.calmar.toFixed(2) : "—"}  итого ${f(m.total, 0)}`); };
  console.log(`\n== ${start} → ${dates[T - 1]} (розничные издержки, своп-наценка 1%/год)`);
  for (const [k, v] of Object.entries(S)) line(k, v.ret);
  line("EUR/USD: купить и держать", eurBH);

  console.log("\n== Половины (до и после 2015)");
  for (const k of ["Тренд", "Керри", "Тренд + керри"]) {
    const a = metrics(dates, S[k].ret, start, "2015-01-01"), b = metrics(dates, S[k].ret, "2015-01-01", end); rep.halves[k] = [a, b];
    console.log(`  ${k.padEnd(16)} ${start.slice(0, 4)}–2014: Sharpe ${a.sharpe.toFixed(2)}, год ${f(a.cagr)}   2015–2026: Sharpe ${b.sharpe.toFixed(2)}, год ${f(b.cagr)}`);
  }
  console.log("\n== Кризисы (доходность за период)");
  const CR = [["Lehman, сен–дек 2008", "2008-09-01", "2009-01-01"], ["Отмена привязки CHF, янв 2015", "2015-01-12", "2015-01-31"], ["COVID, фев–апр 2020", "2020-02-15", "2020-05-01"], ["Инфляция и ставки, 2022", "2022-01-01", "2023-01-01"], ["Развал керри в иене, июль–авг 2024", "2024-07-01", "2024-08-31"]];
  for (const [nm, a, b] of CR) {
    rep.crises[nm] = {}; let s = `  ${nm.padEnd(36)}`;
    for (const k of ["Тренд", "Керри", "Тренд + керри", "Золото: купить и держать"]) { let eq = 1; for (let t = 0; t < T; t++) if (dates[t] >= a && dates[t] < b) eq *= 1 + S[k].ret[t]; rep.crises[nm][k] = eq - 1; s += `${k.split(":")[0]} ${f(eq - 1)}   `; }
    console.log(s);
  }
  const years = [...new Set(dates.filter((d) => d >= start).map((d) => d.slice(0, 4)))];
  for (const k of ["Тренд", "Керри", "Тренд + керри", "Золото: купить и держать"]) rep.years[k] = Object.fromEntries(years.map((y) => { let eq = 1; for (let t = 0; t < T; t++) if (dates[t].startsWith(y) && dates[t] >= start) eq *= 1 + S[k].ret[t]; return [y, eq - 1]; }));
  console.log("\n== По годам: " + ["Тренд", "Керри", "Тренд + керри"].map((k) => `${k}: ${Object.values(rep.years[k]).filter((x) => x > 0).length}/${years.length} лет в плюсе`).join(", "));

  console.log("\n== Устойчивость «Тренд + керри» (Sharpe / год / просадка)");
  const sens = (name, o) => { const m = metrics(dates, backtest(D, { strategy: "combo", ...o }).ret, start, end); rep.sens.push({ name, ...m }); console.log(`  ${name.padEnd(44)} ${m.sharpe.toFixed(2).padStart(5)}  ${f(m.cagr).padStart(7)}  ${f(m.maxDD).padStart(7)}`); };
  sens("без своп-наценки (институциональные условия)", { markup: 0 });
  sens("своп-наценка 2%/год (плохой брокер)", { markup: 0.02 });
  sens("издержки на сделку ×2", { costMult: 2 });
  sens("ребаланс каждый день", { rebal: 1 });
  sens("ребаланс раз в месяц", { rebal: 21 });
  sens("тренд только 1 мес.", { horizons: [21] });
  sens("тренд только 3 мес.", { horizons: [63] });
  sens("тренд только 12 мес.", { horizons: [252] });
  sens("керри: 2 лучших / 2 худших", { nCarry: 2 });
  sens("целевая волатильность 5%", { target: 0.05 });
  sens("целевая волатильность 15%", { target: 0.15 });
  const sT = metrics(dates, backtest(D, { strategy: "trend", markup: 0 }).ret, start, end), sC = metrics(dates, backtest(D, { strategy: "carry", markup: 0 }).ret, start, end);
  console.log(`  без наценки отдельно: тренд Sharpe ${sT.sharpe.toFixed(2)}, керри Sharpe ${sC.sharpe.toFixed(2)}`);
  rep.noMarkup = { trend: sT, carry: sC };

  // correlation trend vs carry, and curves for the report (weekly points)
  const ix = dates.map((d, t) => t).filter((t) => dates[t] >= start), a = ix.map((t) => S["Тренд"].ret[t]), b = ix.map((t) => S["Керри"].ret[t]);
  const ma = a.reduce((s, x) => s + x, 0) / a.length, mb = b.reduce((s, x) => s + x, 0) / b.length;
  rep.corr = a.reduce((s, x, k) => s + (x - ma) * (b[k] - mb), 0) / Math.sqrt(a.reduce((s, x) => s + (x - ma) ** 2, 0) * b.reduce((s, x) => s + (x - mb) ** 2, 0));
  console.log(`\nКорреляция тренда и керри: ${rep.corr.toFixed(2)}; среднее плечо «тренд + керри»: ${(ix.reduce((s, t) => s + S["Тренд + керри"].lev[t], 0) / ix.length).toFixed(1)}x`);
  for (const [k, v] of [...Object.entries(S), ["EUR/USD: купить и держать", { ret: eurBH }]]) { let eq = 1; rep.curves[k] = []; ix.forEach((t, n) => { eq *= 1 + v.ret[t]; if (n % 5 === 0) rep.curves[k].push([dates[t], +eq.toFixed(4)]); }); }
  rep.lastWeights = { assets: ASSETS.map((x) => x.key), combo: S["Тренд + керри"].wHist.slice(-1)[0] };
  fs.writeFileSync(path.join(OUT, "step9_fx_trend.json"), JSON.stringify(rep, null, 1));
})();
