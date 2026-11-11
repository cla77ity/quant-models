// Step 13b — model E re-run with HeroFX's real swaps (exported by the ModelE EA on 2026-11-10, Common\Files\ModelE\symbols.csv).
// Swap mode 1 = points per lot per night; triple swap once a week, so ~365 charges a year.
// Annual long cost = |swap_long| × point × contract / price × 365. Broker markup = that − the local 3-month rate.
//   node step13b_real_swaps.js
const { buildData, backtest, metrics } = require("../../lib/portfolio.js");
const SPEC = { US500: [-179.52, 0.01, 1, 7507.62, 3.94], US100: [-614.81, 0.01, 1, 27650.42, 3.94], US30: [-1014.23, 0.01, 1, 52176.9, 3.94], US2000: [-90.98, 0.01, 1, 2815.58, 3.94],
  GER40: [-326.61, 0.01, 1, 26491.3, 2.03], JP225: [-605.5, 0.01, 1, 59795.48, 1.56], UK100: [-240.97, 0.01, 1, 10803.1, 3.71], XAU: [-120.05, 0.01, 100, 4030.46, 3.94], XAG: [-17.08, 0.001, 5000, 51.465, 3.94] };
const markup = {};
console.log("Свопы HeroFX по лонгу, % годовых от объёма позиции:");
for (const [k, [sw, pt, cs, px, rate]] of Object.entries(SPEC)) {
  const yearly = (-sw * pt * cs * 365) / (px * cs) * 100; markup[k] = Math.max(0, (yearly - rate) / 100);
  console.log(`  ${k.padEnd(7)} своп ${yearly.toFixed(1)}%  ставка ${rate}%  → наценка брокера ${(markup[k] * 100).toFixed(1)}%`);
}
const F = (key, sym, cost, cls) => ({ key, sym, fund: true, rate: null, cost, markup: markup[key], rp: true, cls, maxMove: 0.3 });
const A = [F("US500", "SPY", 1, "eq"), F("US100", "QQQ", 1, "eq"), F("US30", "DIA", 1.5, "eq"), F("US2000", "IWM", 1.5, "eq"), F("GER40", "EWG", 1.5, "eq"), F("JP225", "EWJ", 1.5, "eq"), F("UK100", "EWU", 1.5, "eq"), F("XAU", "GLD", 3, "metal"), F("XAG", "SLV", 5, "metal")];
(async () => {
  const D = await buildData(A, "2004-01-01"), { dates } = D, start = dates[Math.max(...D.active.map((a) => a.indexOf(true)))];
  const cnt = { eq: 7, metal: 2 }, wts = Object.fromEntries(A.map((a) => [a.key, 1 / cnt[a.cls]]));
  const pc = (x) => (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%";
  const run = (o, name) => { const bt = backtest(D, { strategy: "rpsma", wts, rebal: 1, covL: 60, band: 0.25, ...o }); const m = metrics(dates, bt.ret, start, "2100"), a = metrics(dates, bt.ret, start, "2017-01-01"), b = metrics(dates, bt.ret, "2017-01-01", "2100");
    console.log(`  ${name.padEnd(44)} Sharpe ${m.sharpe.toFixed(2)} (2007–16 ${a.sharpe.toFixed(2)}, 2017–26 ${b.sharpe.toFixed(2)})  ${pc(m.cagr)} в год  просадка ${pc(m.maxDD)}`); };
  console.log(`\nМодель E, ${start} → ${dates[dates.length - 1]}:`);
  run({ markup: 0 }, "без наценки (как фьючерсы)");
  run({}, "реальные свопы HeroFX");
  // what's left without the two most expensive markets
  const D2 = { ...D, active: D.active.map((a, i) => (["US2000", "XAG"].includes(A[i].key) ? a.map(() => false) : a)) };
  const bt = backtest(D2, { strategy: "rpsma", wts: { ...wts, XAU: 1 }, rebal: 1, covL: 60, band: 0.25 }), m = metrics(dates, bt.ret, start, "2100");
  console.log(`  ${"реальные свопы, без RUS2000 и серебра".padEnd(44)} Sharpe ${m.sharpe.toFixed(2)}  ${pc(m.cagr)} в год  просадка ${pc(m.maxDD)}`);
})();
