// Step 7 — "OTE + trend": a pullback into the OTE zone of a leg in the direction of the higher-timeframe trend.
// Same engine as step 6 (leg ≥ 3×ATR over 4 h, limit, stop beyond the leg's origin, 5R, break-even at the internal high,
// ≤ 3 trades a day, done after 2 losses in a row, CME costs). Two questions, all variants fixed BEFORE the run:
//   1. Which trend: 15m SMA50 · 1h SMA50 · 4h SMA50 · daily SMA20 · "stack" (1h and daily agree).
//   2. Entry: plain limit at 0.62 / 0.705 / 0.79 of the leg (no PD array needed) vs the step-6 rule (CE of a PD array).
// Main model, chosen in advance: stack trend + limit at 0.705. 20 variants in total — so only t ≥ ~2.8 would survive
// a multiple-testing correction; everything below that is a hint, not a finding.
//   node step7_ote_trend.js
const fs = require("fs");
const path = require("path");
const { oteRun, klines, bot, pick, stat, QT, randomBaseline, monteCarlo, trendFn } = require("../../lib/intraday.js");

// trend from completed higher-timeframe buckets built out of 15m bars (no peeking into the unfinished bucket)
const tfTrend = (keyFn, N) => (k15) => {
  const t = k15.map((k) => k[0]), c = k15.map((k) => +k[4]), st = new Array(t.length).fill(null), closes = [];
  let cur = null, last = null;
  for (let i = 0; i < t.length; i++) {
    const k = keyFn(t[i]);
    if (cur !== null && k !== cur) closes.push(last);
    cur = k; last = c[i];
    if (closes.length >= N) { let s = 0; for (let q = closes.length - N; q < closes.length; q++) s += closes[q]; st[i] = closes[closes.length - 1] > s / N; }
  }
  return (tm) => {
    let lo = 0, hi = t.length - 1, i = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (t[m] + 15 * 60e3 <= tm) { i = m; lo = m + 1; } else hi = m - 1; }
    return i < 0 ? null : st[i];
  };
};
const h1 = tfTrend((t) => Math.floor(t / 3600e3), 50), h4 = tfTrend((t) => Math.floor(t / 14400e3), 50), d1 = tfTrend((t) => QT.cmeDay(t), 20);
const stack = (k15) => { const a = h1(k15), b = d1(k15); return (t) => { const x = a(t), y = b(t); return x != null && x === y ? x : null; }; };
const TRENDS = [["15m SMA50", trendFn], ["1h SMA50", h1], ["4h SMA50", h4], ["день SMA20", d1], ["1h + день согласны", stack]];
const ENTRIES = [["лимит 0.62", { pda: false, levels: [0.62] }], ["лимит 0.705", { pda: false, levels: [0.705] }], ["лимит 0.79", { pda: false, levels: [0.79] }], ["CE PD-массива (шаг 6)", {}]];

(async () => {
  const SYMS = ["QQQ", "SPY"], NAME = { QQQ: "NQ", SPY: "ES" }, data = {};
  for (const sym of SYMS) data[sym] = { k15: await klines(sym, "15m"), k5: await klines(sym, "5m") };
  const runAll = (opts) => SYMS.flatMap((sym) => oteRun(data[sym].k15, data[sym].k5, opts).map((s) => ({ ...s, sym })));
  const f = (x, d = 2) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "") + x.toFixed(d));

  const grid = [];
  console.log("== Сетка: средний R на сделку (t, сделок) — бот, NQ+ES\n" + "".padEnd(22) + ENTRIES.map(([n]) => n.padEnd(24)).join(""));
  let mainList = null;
  for (const [tn, tf] of TRENDS) {
    let line = tn.padEnd(22);
    for (const [en, eo] of ENTRIES) {
      const B = bot(pick(runAll({ ...eo, trendOf: tf }))), st = stat(B.map((s) => s.res));
      grid.push({ trend: tn, entry: en, ...(st || { n: B.length }) });
      line += (st ? `${f(st.avgR)}R (t ${f(st.t, 1)}, ${st.n})` : `мало (${B.length})`).padEnd(24);
      if (tn === "1h + день согласны" && en === "лимит 0.705") mainList = B;
    }
    console.log(line);
  }

  const main = stat(mainList.map((s) => s.res));
  console.log(`\n== Основная модель (выбрана заранее): тренд 1h + день, лимит 0.705`);
  console.log(`  n ${main.n}, win ${main.win.toFixed(0)}%, ${f(main.avgR)}R, t ${f(main.t, 1)}, PF ${main.pf ? main.pf.toFixed(2) : "—"}, сумма ${f(main.sumR, 0)}R, DD ${main.maxDD.toFixed(1)}R, серия ${main.maxLossStreak}, половины ${f(main.first)}/${f(main.second)}`);
  for (const sym of SYMS) { const s = stat(mainList.filter((x) => x.sym === sym).map((x) => x.res)); console.log(`  ${NAME[sym]}: ${s ? `n ${s.n}, ${f(s.avgR)}R, t ${f(s.t, 1)}` : "мало"}`); }
  const exits = [];
  for (const k of ["2|none", "3|none", "5|none", "leg|none", "3|ih", "5|1R"]) {
    const s = stat(bot(pick(runAll({ pda: false, levels: [0.705], trendOf: stack }), k)).map((x) => x.res));
    exits.push({ k, ...(s || {}) }); console.log(`  выход ${k.replace("|none", " без БУ").replace("|ih", " БУ внутр. хай").replace("|1R", " БУ +1R").replace("leg", "хай волны")}: ${s ? `${f(s.avgR)}R, t ${f(s.t, 1)}, n ${s.n}` : "мало"}`);
  }
  const rand = randomBaseline(data, mainList, 300, stack), pBeat = rand.filter((x) => x >= main.avgR).length / rand.length;
  console.log(`  случайные входы по тому же тренду (300): 5/50/95% = ${f(rand[15])} / ${f(rand[150])} / ${f(rand[285])}R → случай не хуже модели в ${(pBeat * 100).toFixed(0)}%`);
  const mc = monteCarlo(mainList.map((s) => s.res));
  console.log(`  Монте-Карло: шанс итога ≤ 0 — ${(mc.pLoss * 100).toFixed(0)}%; просадка медиана ${mc.dd50.toFixed(1)}R, худшие 5% ${mc.dd5.toFixed(1)}R; стопов подряд 95% — ${mc.streak95}`);

  // another market, same rules
  const STOCKS = ["IWM", "SMH", "NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "TSLA", "AVGO"], st2 = [];
  for (const sym of STOCKS) { const k15 = await klines(sym, "15m"), k5 = await klines(sym, "5m"); if (k5.length > 3000) st2.push(...oteRun(k15, k5, { pda: false, levels: [0.705], trendOf: stack, rth: true, cost: 0.0002 }).map((s) => ({ ...s, sym }))); }
  const sS = stat(bot(pick(st2)).map((s) => s.res));
  console.log(`  те же правила на 10 акциях/ETF: ${sS ? `n ${sS.n}, ${f(sS.avgR)}R, t ${f(sS.t, 1)}, половины ${f(sS.first)}/${f(sS.second)}` : "мало"}`);

  fs.writeFileSync(path.join(__dirname, "results", "step7_ote_trend.json"), JSON.stringify({ grid, main, exits, random: { p5: rand[15], p50: rand[150], p95: rand[285], pBeat }, mc: { ...mc, ddHist: undefined }, stocks: sS }, null, 1));
})();
