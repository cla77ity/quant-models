// Step 12 — the RB-video model (RBV, QT.rbvidRun — exactly what the terminal's paper bot runs) put through the full check:
//  1. NQ/ES (QQQ/SPY perps) through today; the rules were frozen on 2026-10-10 (commit 619630b), so everything after that
//     date is a true forward test. The backtest trades of that period are matched against the bot's journal.
//  2. Random entries with the same direction, risk and exits; Monte Carlo of the trade order.
//  3. Robustness: every rule parameter moved one notch either way.
//  4. A different market with years of 5m data: BTC and ETH perps (crypto costs ~0.07% round trip, 24/7, bot rules).
//   node step12_rbv.js   [journal check needs RBV_JOURNAL=<path to an exported qt.sigLog JSON>]
const fs = require("fs");
const path = require("path");
const { klines, bot, pick, stat, QT, randomBaseline, monteCarlo } = require("../../lib/intraday.js");
const FROZEN = Date.UTC(2026, 9, 10, 22, 0);   // 2026-10-10 evening (UTC): RBV committed to the terminal
const f = (x, d = 2) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "") + x.toFixed(d));
const line = (name, st, extra = "") => console.log(st ? `  ${name.padEnd(46)} n=${String(st.n).padStart(5)} win ${st.win.toFixed(0).padStart(2)}%  ${f(st.avgR)}R  t ${f(st.t, 1).padStart(5)}  PF ${st.pf ? st.pf.toFixed(2) : "—"}  сумма ${f(st.sumR, 0)}R  DD ${st.maxDD.toFixed(0)}R  серия ${st.maxLossStreak}  половины ${f(st.first)}/${f(st.second)}${extra}` : `  ${name.padEnd(46)} мало сделок`);

(async () => {
  const rep = { idx: {}, robust: [], crypto: {} };
  const data = {};
  for (const s of ["QQQ", "SPY"]) data[s] = { k15: await klines(s, "15m"), k5: await klines(s, "5m") };
  const run = (opts = {}, syms = ["QQQ", "SPY"], proxy = true) => syms.flatMap((s) => QT.rbvidRun(data[s].k15, data[s].k5, proxy, { cost: 0.00004, ...opts }).sigs.map((x) => ({ ...x, sym: s })));
  const period = data.QQQ.k5.map((k) => k[0]);
  console.log(`NQ/ES 5m: ${new Date(period[0]).toISOString().slice(0, 10)} → ${new Date(period[period.length - 1]).toISOString().slice(0, 10)}`);

  // ---- 1. the bot, whole period / before / after the freeze
  const sigs = run(), B = bot(pick(sigs));
  const S = (list) => stat(list.map((x) => x.res));
  console.log("\n== RBV на NQ/ES (бот: тренд + ≤3 в день + стоп после 2 убытков, цель 5R, БУ после внутр. хая)");
  const all = S(B), pre = S(B.filter((x) => x.fillT < FROZEN)), post = S(B.filter((x) => x.fillT >= FROZEN));
  line("весь период", all); line("до заморозки правил (на чём придумано)", pre); line("ПОСЛЕ 10.10 — чистый тест вперёд", post);
  for (const s of ["QQQ", "SPY"]) line(`  ${s === "QQQ" ? "NQ" : "ES"}`, S(B.filter((x) => x.sym === s)));
  line("все исполненные, без фильтров бота", S(pick(sigs)));
  line("против тренда (контроль)", S(pick(sigs, "5|ih", (x) => !x.trend)));
  rep.idx = { all, pre, post };

  // ---- 2. luck checks
  const rand = randomBaseline(data, B, 300), pBeat = rand.filter((x) => x >= all.avgR).length / rand.length, mc = monteCarlo(B.map((x) => x.res));
  console.log(`\n  случайные входы (тот же тренд, риск в ATR, 5R, БУ): 5/50/95% = ${f(rand[15])} / ${f(rand[150])} / ${f(rand[285])}R → случай не хуже модели в ${(pBeat * 100).toFixed(0)}%`);
  console.log(`  Монте-Карло: итог ≤ 0 в ${(mc.pLoss * 100).toFixed(0)}%; просадка медиана ${mc.dd50.toFixed(1)}R, худшие 5% ${mc.dd5.toFixed(1)}R; стопов подряд 95% — ${mc.streak95}`);
  const top = B.map((x) => x.res).sort((a, b) => b - a), n5 = top.filter((x) => x > 4).length;
  console.log(`  сделок по +5R: ${n5} из ${B.length}; без 5 лучших сумма ${f(top.slice(5).reduce((s, x) => s + x, 0), 1)}R`);
  rep.random = { p5: rand[15], p50: rand[150], p95: rand[285], pBeat }; rep.mc = { ...mc, ddHist: undefined };

  // ---- journal match: do the backtest's post-freeze trades equal the bot's?
  if (process.env.RBV_JOURNAL && fs.existsSync(process.env.RBV_JOURNAL)) {
    const J = JSON.parse(fs.readFileSync(process.env.RBV_JOURNAL, "utf8")).filter((s) => s.setup === "RBV" && s.valid && s.fillT && ["target", "stop", "time"].includes(s.state));
    const symOf = { NQ: "QQQ", ES: "SPY" }, key = (sym, t, bull) => `${sym}|${Math.round(t / 300e3)}|${bull}`;
    const jk = new Map(J.map((s) => [key(symOf[s.sym], s.fillT, s.bull), s])), bt = B.filter((x) => x.fillT >= Math.min(...J.map((s) => s.fillT)));
    let same = 0, sameRes = 0; for (const x of bt) { const j = jk.get(key(x.sym, x.fillT, x.bull)); if (j) { same++; if (Math.abs(j.res - x.res) < 0.3) sameRes++; } }
    const jr = J.map((s) => s.res);
    console.log(`\n  сверка с журналом бота: в журнале ${J.length} сделок (${f(jr.reduce((s, x) => s + x, 0) / jr.length)}R), в бэктесте за тот же период ${bt.length} (${f(bt.reduce((s, x) => s + x.res, 0) / (bt.length || 1))}R); совпали ${same}, из них с тем же итогом ${sameRes}`);
    rep.journal = { journal: J.length, backtest: bt.length, same, sameRes };
  }

  // ---- 3. robustness: one notch either way on every rule
  console.log("\n== Устойчивость (бот NQ/ES, средний R / t / сделок)");
  const base = (o) => { const st = S(bot(pick(run(o)))); rep.robust.push({ o: JSON.stringify(o), ...(st || {}) }); return st; };
  for (const [nm, o] of [["волна ≥ 2 ATR (база 3)", { legATR: 2 }], ["волна ≥ 4 ATR", { legATR: 4 }], ["тень до 0.5 (база OTE 0.62)", { oteLo: 0.5 }], ["тень до 0.705", { oteLo: 0.705 }],
    ["тень RB ≥ 35% свечи (база 50%)", { wickPct: 0.35 }], ["тень RB ≥ 65%", { wickPct: 0.65 }], ["лимит живёт 1 свечу (база 3)", { wait: 1 }], ["лимит живёт 6 свечей", { wait: 6 }],
    ["стоп без буфера (база 0.1 ATR)", { stopBuf: 0 }], ["стоп + 0.25 ATR", { stopBuf: 0.25 }], ["подтверждение только той же свечой", { confirmBars: 0 }], ["подтверждение до 4 свечей", { confirmBars: 4 }],
    ["волна за 2 ч (база 4 ч)", { legBars: 24 }], ["волна за 8 ч", { legBars: 96 }]]) {
    const st = base(o); console.log(`  ${nm.padEnd(40)} ${st ? `${f(st.avgR)}R  t ${f(st.t, 1)}  n ${st.n}` : "мало"}`);
  }

  // ---- 4. crypto: years of 5m bars
  console.log("\n== BTC и ETH (перпы Binance, 5m, издержки 0.07% за круг)");
  for (const s of ["BTC", "ETH"]) {
    process.stdout.write(`  загрузка ${s} … `);
    const k15 = await klines(s, "15m"), k5 = await klines(s, "5m");
    console.log(`${k5.length} свечей, с ${new Date(k5[0][0]).toISOString().slice(0, 10)}`);
    const cs = QT.rbvidRun(k15, k5, false, { cost: 0.0007 }).sigs.map((x) => ({ ...x, sym: s })), CB = bot(pick(cs));
    line(`${s}: бот`, S(CB)); line(`${s}: бот без издержек`, S(CB.map((x) => ({ ...x, res: x.res + (0.0007 * x.entry) / x.R }))));
    const byYear = {}; for (const x of CB) { const y = new Date(x.fillT).getUTCFullYear(); (byYear[y] = byYear[y] || []).push(x.res); }
    console.log("    по годам: " + Object.entries(byYear).map(([y, r]) => `${y} ${f(r.reduce((a, v) => a + v, 0) / r.length)}R (${r.length})`).join("  "));
    rep.crypto[s] = { bot: S(CB), noCost: S(CB.map((x) => ({ ...x, res: x.res + (0.0007 * x.entry) / x.R }))), byYear: Object.fromEntries(Object.entries(byYear).map(([y, r]) => [y, { n: r.length, avg: r.reduce((a, v) => a + v, 0) / r.length }])) };
  }
  fs.writeFileSync(path.join(__dirname, "results", "step12_rbv.json"), JSON.stringify(rep, null, 1));
})();
