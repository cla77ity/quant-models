const fs = require("fs");
const path = require("path");
const { oteRun, klines, bot, pick, stat, asV, P0, QT, randomBaseline, monteCarlo, VT, VB, key } = require("../../lib/intraday.js");
const OUT = path.join(__dirname, "results");
fs.mkdirSync(OUT, { recursive: true });

// Step 6 — the OTE "top / bottom tick" model (393ticks, youtu.be/6jPQKYR0nSc), exactly as coach/ПРАВИЛА.md grades it:
//  1. Bias: 15m close vs SMA50 (the higher-timeframe direction). Only trades with the trend count for the bot.
//  2. Range: the swing of the last 4 h in the trend's direction (external low → external high for longs), ≥ 3×ATR of 5m,
//     drawn after the first counter close; a new extreme redraws it (and voids the old order).
//  3. Levels 0.62 / 0.705 / 0.79. Mandatory confluence: the CE of a same-way 15m PD array (RB / OB / FVG) within
//     `tolATR`×ATR of an OTE level, plus discount (it's ≥ 0.5 by construction). At least `minConf` confluences in total —
//     the extra ones: a second PD-array CE on an OTE level (stacked), a key open (00:00 / 09:30 / 10:00) at the entry,
//     engineered liquidity (unswept lows / highs between the current pullback and the entry).
//  4. Entry: aggressive — limit at the shallowest matched level (the "highest" for longs). In 09:30–10:00 NY only the
//     conservative entry: after the touch, a 5m candle closing back beyond the previous candle (LTF trigger), at its close.
//     Mode "cons" uses the trigger everywhere.
//  5. Stop: beyond the pivot that breaks the idea (the range's origin) + 0.1×ATR; if that's wider than 3×ATR, beyond 0.79.
//     Stops narrower than 0.5×ATR are refused (E3 "stop for beauty").
//  6. Break-even once the internal high (highest high of the 6 candles before the fill) is taken. Target 5R.
//     Flat by 16:55 NY, ≤ 8 h; no fills 16:45–18:00. Account rules: ≤ 3 trades a day, done after 2 losses in a row.
// Then the checks a fund would ask for: random entries with the same risk, two halves of the period, Monte Carlo of the
// trade order, and how much the result moves when the rules move (robustness).
// Data and costs as steps 3–5: QQQ / SPY perps = NQ / ES, CME costs (~1 point NQ round trip).
//   node backtest.js
(async () => {
  const SYMS = ["QQQ", "SPY"], NAME = { QQQ: "NQ", SPY: "ES" }, data = {};
  let from = Infinity, to = 0;
  for (const sym of SYMS) {
    process.stdout.write(`${NAME[sym]} (${sym}) … `);
    const k15 = await klines(sym, "15m"), k5 = await klines(sym, "5m");
    data[sym] = { k15, k5 }; from = Math.min(from, k5[0][0]); to = Math.max(to, k5[k5.length - 1][0]);
    console.log(`${k5.length} свечей 5m`);
  }
  const runAll = (opts) => SYMS.flatMap((sym) => oteRun(data[sym].k15, data[sym].k5, opts).map((s) => ({ ...s, sym })));

  const t0 = Date.now();
  const base = runAll({});
  console.log(`основной прогон: ${base.length} сетапов, ${((Date.now() - t0) / 1000).toFixed(0)} с`);
  const rows = [], row = (sect, name, list, extra = {}) => { const st = stat(list.map((s) => s.res)); rows.push({ sect, name, ...(st || { n: list.length }), ...extra }); return st; };

  const all = pick(base), trendL = pick(base, "5|ih", (s) => s.trend), B = bot(pick(base));
  row("Модель", "все исполненные (и по тренду, и против)", all);
  row("Модель", "против тренда (для сравнения)", pick(base, "5|ih", (s) => !s.trend));
  row("Модель", "по тренду", trendL);
  const mainSt = row("Модель", "БОТ: по тренду + ≤3 в день + стоп после 2 убытков", B);
  row("Модель", "БОТ без издержек", bot(pick(base)).map((s) => ({ ...s, res: s.res + (P0.cost * s.entry) / s.R })));
  for (const sym of SYMS) row("По инструменту", `${NAME[sym]} (${sym})`, B.filter((s) => s.sym === sym));
  for (const lv of P0.levels) row("Уровень входа", `вход на ${lv}`, B.filter((s) => s.lv === lv));
  for (const [k, nm] of [["stack", "есть второй CE (стек)"], ["open", "есть ключевое открытие"], ["liq", "есть инженерная ликвидность"]]) row("Совпадения", nm, B.filter((s) => s.kinds.includes(k)));
  for (const nc of [3, 4, 5]) row("Совпадения", `ровно ${nc} совпадения`, B.filter((s) => s.nConf === nc));
  const sess = (m) => (m >= 1080 || m < 120 ? "Азия (18:00–02:00)" : m < 420 ? "Лондон (02:00–07:00)" : "Нью-Йорк (07:00–16:45)");
  for (const sn of ["Азия (18:00–02:00)", "Лондон (02:00–07:00)", "Нью-Йорк (07:00–16:45)"]) row("Сессия (время установки ордера, NY)", sn, B.filter((s) => sess(s.min) === sn));
  row("Тип входа", "лимит (агрессивный)", B.filter((s) => s.how === "лимит"));
  row("Тип входа", "триггер на 5m (9:30–10:00)", B.filter((s) => s.how === "триггер"));
  const BE_RU = { ih: "БУ после внутр. хая", "1R": "БУ после +1R", none: "без БУ" };
  for (const T of VT) for (const be of VB) row("Выход (цель × безубыток)", `цель ${T === "leg" ? "хай волны" : T + "R"} · ${BE_RU[be]}`, bot(pick(base, key(T, be))));

  // robustness: the same rules, moved
  const robust = [];
  const rob = (name, opts) => { const st = stat(bot(pick(runAll(opts))).map((s) => s.res)); robust.push({ name, ...(st || {}) }); process.stdout.write("."); };
  process.stdout.write("устойчивость ");
  for (const tolATR of [0.05, 0.1, 0.25, 0.4]) rob(`допуск CE↔уровень ${tolATR}×ATR (база 0.15)`, { tolATR });
  for (const minConf of [2, 4]) rob(`минимум ${minConf} совпадения (база 3)`, { minConf });
  rob("консервативный вход везде (триггер 5m)", { mode: "cons" });
  rob("стоп всегда за пивотом", { stopMode: "pivot" });
  rob("стоп всегда за 0.79", { stopMode: "79" });
  for (const legATR of [2, 4]) rob(`волна ≥ ${legATR}×ATR (база 3)`, { legATR });
  rob("уровни только 0.62 и 0.705", { levels: [0.62, 0.705] });
  rob("уровни 0.5–0.79 (добавлен 0.5)", { levels: [0.5, 0.62, 0.705, 0.79] });
  console.log();

  // random entries and Monte Carlo on the bot
  const rand = mainSt ? randomBaseline(data, B, 300) : [];
  const pRand = rand.length ? rand.filter((x) => x >= mainSt.avgR).length / rand.length : null;
  const mc = mainSt ? monteCarlo(B.map((s) => s.res)) : null;

  const report = {
    period: [new Date(from).toISOString().slice(0, 10), new Date(to).toISOString().slice(0, 10)], rules: P0, rows, robust,
    random: rand.length ? { runs: rand.length, p5: rand[Math.floor(0.05 * rand.length)], p50: rand[Math.floor(0.5 * rand.length)], p95: rand[Math.floor(0.95 * rand.length)], pBeat: pRand, hist: rand } : null,
    mc: mc ? { ...mc, ddHist: undefined } : null, mcDD: mc ? mc.ddHist.filter((_, i) => i % 10 === 0) : [],
    trades: B.map((s) => ({ sym: s.sym, bull: s.bull, t: s.t, fillT: s.fillT, exitT: s.exitT, entry: s.entry, stop: s.stop, tgt: s.tgt, exitPx: s.exitPx, res: s.res, state: s.state,
      legX: s.legX, legO: s.legO, lv: s.lv, conf: s.conf, how: s.how, ih: s.ih })),
  };
  fs.writeFileSync(path.join(OUT, "step6_ote_tick.json"), JSON.stringify(report));

  const f = (x, d = 2) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "") + x.toFixed(d));
  console.log(`\nПериод ${report.period.join(" → ")}`);
  let s0 = null;
  for (const r of rows) {
    if (r.sect !== s0) { console.log(`\n== ${r.sect}`); s0 = r.sect; }
    console.log(r.avgR == null ? `  ${r.name.padEnd(52)} n=${r.n} (мало)` : `  ${r.name.padEnd(52)} n=${String(r.n).padStart(4)} win ${r.win.toFixed(0).padStart(2)}%  ${f(r.avgR)}R  t ${f(r.t, 1)}  PF ${r.pf ? r.pf.toFixed(2) : "—"}  сумма ${f(r.sumR, 0)}R  DD ${r.maxDD.toFixed(1)}R  серия ${r.maxLossStreak}  половины ${f(r.first)}/${f(r.second)}`);
  }
  console.log("\n== Устойчивость (бот)");
  for (const r of robust) console.log(r.avgR == null ? `  ${r.name.padEnd(52)} мало сделок` : `  ${r.name.padEnd(52)} n=${String(r.n).padStart(4)} ${f(r.avgR)}R  t ${f(r.t, 1)}  DD ${r.maxDD.toFixed(1)}R`);
  if (report.random) console.log(`\n== Случайные входы (${report.random.runs} прогонов, тот же риск и выходы): 5%/50%/95% = ${f(report.random.p5)} / ${f(report.random.p50)} / ${f(report.random.p95)}R; модель ${f(mainSt.avgR)}R; случай лучше модели в ${(pRand * 100).toFixed(0)}% прогонов`);
  if (mc) console.log(`== Монте-Карло (10 000): вероятность итога ≤ 0 — ${(mc.pLoss * 100).toFixed(0)}%; итог 5%/50%/95% = ${f(mc.sum5, 0)} / ${f(mc.sum50, 0)} / ${f(mc.sum95, 0)}R; просадка медиана ${mc.dd50.toFixed(1)}R, худшие 5% ${mc.dd5.toFixed(1)}R; серия стопов медиана ${mc.streak50}, 95% — ${mc.streak95}`);
})();
