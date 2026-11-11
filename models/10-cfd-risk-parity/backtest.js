// Step 11 — documented portfolio strategies that a plain retail forex / CFD account can actually trade.
// Markets a typical broker lists, modelled as CFDs on the ETF's total return (dividends in) minus what the broker takes:
// the USD rate on longs (shorts receive it) + a financing markup (2.5%/yr non-FX, 1%/yr FX) + spread per unit traded.
//   indices  US500 (SPY) · US100 (QQQ) · US30 (DIA) · US2000 (IWM) · GER40 (EWG) · JP225 (EWJ) · UK100 (EWU)
//   bonds    US 10Y (IEF) · US 30Y (TLT) — not every forex broker has them, so a "no bonds" variant is part of the plan
//   metals / energy  gold (GLD) · silver (SLV) · oil (USO)      FX  the 9 USD pairs of step 9 with real rate carry
// Strategies (all at 10% annual volatility, weekly rebalance), fixed in advance:
//   1. Risk parity — long every non-FX market, weights ∝ 1/volatility, scaled by the 120-day covariance
//   2. Risk parity + SMA200 — a market below its 200-day average gets weight 0
//   3. Diversified trend long/short (1/3/12-month signs) over all 21 markets — the CTA recipe
//   4. Risk parity + trend, 50/50
// Benchmarks: US500 buy & hold (1x CFD), 60/40 US500 / US 10Y (1x CFD).
// Country ETFs carry their currency (EWG = DAX in dollars) and ETF fees (0.1–0.8%/yr): a conservative bias.
//   node step11_cfd_portfolio.js   → results/step11_cfd_portfolio.json, then node step11_report.js
const fs = require("fs");
const path = require("path");
const { ASSETS: FX0, buildData, backtest, metrics } = require("../../lib/portfolio.js");

const F = (key, sym, cost, cls) => ({ key, sym, fund: true, rate: null, cost, markup: 0.025, rp: true, cls, maxMove: 0.3 });
const FUNDS = [F("US500", "SPY", 1, "акции"), F("US100", "QQQ", 1, "акции"), F("US30", "DIA", 1.5, "акции"), F("US2000", "IWM", 1.5, "акции"),
  F("GER40", "EWG", 1.5, "акции"), F("JP225", "EWJ", 1.5, "акции"), F("UK100", "EWU", 1.5, "акции"),
  F("UST10", "IEF", 2, "облигации"), F("UST30", "TLT", 2, "облигации"), F("XAU", "GLD", 3, "металлы"), F("XAG", "SLV", 5, "металлы"), F("OIL", "USO", 4, "энергия")];
const FX = FX0.filter((a) => a.key !== "XAU").map((a) => ({ ...a, markup: 0.01, cls: "валюты" }));
const ALL = [...FUNDS, ...FX];
const pc = (x, d = 1) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");

(async () => {
  const D = await buildData(ALL, "2004-01-01"), { dates } = D, T = dates.length;
  // trading starts once the youngest market (SLV / USO, April 2006) has a year of history
  const firstAll = Math.max(...D.active.map((a) => a.indexOf(true))), start = dates[firstAll], end = "2100", split = "2016-01-01";
  console.log(`Данные ${dates[0]} → ${dates[T - 1]}, все 21 рынок активны с ${start}; плохих тиков ${JSON.stringify(Object.fromEntries(Object.entries(D.bad).filter(([, v]) => v)))}`);
  const mask = (keys) => ({ ...D, active: D.active.map((a, i) => (keys.includes(D.assets[i].key) ? a : a.map(() => false))) });
  const noBonds = mask(ALL.map((a) => a.key).filter((k) => !["UST10", "UST30"].includes(k)));

  const S = [
    ["Risk parity", D, { strategy: "rp" }],
    ["Risk parity + SMA200", D, { strategy: "rpsma" }],
    ["Тренд лонг/шорт, 21 рынок", D, { strategy: "trend" }],
    ["Risk parity + тренд 50/50", D, { strategy: "rptrend" }],
    ["US500: купить и держать (1x)", D, { strategy: "weights", weights: { US500: 1 }, fixed: true }],
    ["60/40: US500 + US 10Y (1x)", D, { strategy: "weights", weights: { US500: 0.6, UST10: 0.4 }, fixed: true }],
  ];
  const CR = [["Lehman, сен 2008 – мар 2009", "2008-09-01", "2009-03-10"], ["Долговой кризис США, авг 2011", "2011-07-22", "2011-10-04"], ["Q4 2018", "2018-10-01", "2018-12-25"], ["COVID, фев–мар 2020", "2020-02-19", "2020-03-24"], ["2022: акции и облигации вниз", "2022-01-01", "2023-01-01"], ["Пошлины, фев–апр 2025", "2025-02-19", "2025-04-09"]];
  const period = (ret, a, b) => { let eq = 1; for (let t = 0; t < T; t++) if (dates[t] >= a && dates[t] < b) eq *= 1 + ret[t]; return eq - 1; };
  const rep = { start, end: dates[T - 1], main: {}, halves: {}, crises: {}, years: {}, sens: [], curves: {}, corr: {}, lev: {} };
  const years = [...new Set(dates.filter((d) => d >= start).map((d) => d.slice(0, 4)))];
  const R = {};
  console.log(`\n== ${start} → ${dates[T - 1]}: CFD (ставка + наценка + спред)`);
  for (const [name, data, o] of S) {
    const bt = backtest(data, o), m = metrics(dates, bt.ret, start, end), a = metrics(dates, bt.ret, start, split), b = metrics(dates, bt.ret, split, end);
    R[name] = bt.ret; rep.main[name] = m; rep.halves[name] = [a.sharpe, b.sharpe];
    rep.crises[name] = Object.fromEntries(CR.map(([nm, x, y]) => [nm, period(bt.ret, x, y)]));
    rep.years[name] = Object.fromEntries(years.map((y) => [y, period(bt.ret, `${y}-01-01`, `${+y + 1}-01-01`)]));
    const ix = dates.map((_, t) => t).filter((t) => dates[t] >= start);
    rep.lev[name] = ix.reduce((s, t) => s + bt.lev[t], 0) / ix.length;
    let eq = 1; rep.curves[name] = []; ix.forEach((t, n) => { eq *= 1 + bt.ret[t]; if (n % 5 === 0) rep.curves[name].push([dates[t], +eq.toFixed(4)]); });
    console.log(`  ${name.padEnd(30)} год ${pc(m.cagr).padStart(7)}  вол ${pc(m.vol).padStart(6)}  Sharpe ${m.sharpe.toFixed(2)} (t ${m.tstat.toFixed(1)})  просадка ${pc(m.maxDD, 0).padStart(5)}  плечо ${rep.lev[name].toFixed(1)}x  до/после 2016: ${a.sharpe.toFixed(2)}/${b.sharpe.toFixed(2)}  лет в плюсе ${Object.values(rep.years[name]).filter((x) => x > 0).length}/${years.length}`);
  }
  console.log("\n== Кризисы");
  for (const [nm] of CR) console.log(`  ${nm.padEnd(32)} ` + S.map(([n]) => `${n.split(",")[0].split(":")[0].slice(0, 18)} ${pc(rep.crises[n][nm], 0)}`).join("  "));

  // how related the pieces are (daily returns after start)
  const ix = dates.map((_, t) => t).filter((t) => dates[t] >= start), cor = (x, y) => { const a = ix.map((t) => x[t]), b = ix.map((t) => y[t]), ma = a.reduce((s, v) => s + v, 0) / a.length, mb = b.reduce((s, v) => s + v, 0) / b.length; return a.reduce((s, v, k) => s + (v - ma) * (b[k] - mb), 0) / Math.sqrt(a.reduce((s, v) => s + (v - ma) ** 2, 0) * b.reduce((s, v) => s + (v - mb) ** 2, 0)); };
  rep.corr = { "RP ~ тренд": cor(R["Risk parity"], R["Тренд лонг/шорт, 21 рынок"]), "RP ~ US500": cor(R["Risk parity"], R["US500: купить и держать (1x)"]), "тренд ~ US500": cor(R["Тренд лонг/шорт, 21 рынок"], R["US500: купить и держать (1x)"]) };
  console.log("\nКорреляции: " + Object.entries(rep.corr).map(([k, v]) => `${k} ${v.toFixed(2)}`).join(", "));

  console.log("\n== Устойчивость (Sharpe / год / просадка)");
  const sens = (name, data, o) => { const m = metrics(dates, backtest(data, o).ret, start, end); rep.sens.push({ name, ...m }); console.log(`  ${name.padEnd(52)} ${m.sharpe.toFixed(2).padStart(5)}  ${pc(m.cagr).padStart(7)}  ${pc(m.maxDD, 0).padStart(5)}`); };
  for (const [nm, st] of [["Risk parity", "rp"], ["Risk parity + SMA200", "rpsma"], ["Risk parity + тренд", "rptrend"]]) {
    sens(`${nm}: без облигаций (их нет у брокера)`, noBonds, { strategy: st });
    sens(`${nm}: без наценки брокера (как фьючерсы)`, D, { strategy: st, markup: 0 });
    sens(`${nm}: наценка ×2 (5% / 2%)`, D, { strategy: st, markupMult: 2 });
    sens(`${nm}: ребаланс раз в месяц`, D, { strategy: st, rebal: 21 });
    sens(`${nm}: целевая волатильность 15%`, D, { strategy: st, target: 0.15 });
  }
  sens("Только US500 + US100 + золото, risk parity + SMA200", mask(["US500", "US100", "XAU"]), { strategy: "rpsma" });
  sens("Тренд только на не-валютах (12 рынков)", mask(FUNDS.map((a) => a.key)), { strategy: "trend" });
  fs.writeFileSync(path.join(__dirname, "results", "step11_cfd_portfolio.json"), JSON.stringify(rep, null, 1));
})();
