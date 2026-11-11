// Step 6b — out-of-market check of the step 6 hypothesis "a key open (00:00 / 09:30 / 10:00) at the OTE entry".
// The OTE model, rules unchanged, on 10 stocks / ETFs the hypothesis was NOT found on (US hours, stock costs 2 bp).
// The London hypothesis can't be checked here (stocks trade US hours only) — it goes to the forward journal only.
//   node step6b_crossmarket.js
const fs = require("fs");
const path = require("path");
const { oteRun, klines, bot, pick, stat } = require("../../lib/intraday.js");

const STOCKS = ["IWM", "SMH", "NVDA", "AAPL", "MSFT", "META", "AMZN", "GOOGL", "TSLA", "AVGO"];
(async () => {
  const all = [];
  for (const sym of STOCKS) {
    process.stdout.write(`${sym} … `);
    const k15 = await klines(sym, "15m"), k5 = await klines(sym, "5m");
    if (k5.length < 3000) { console.log("мало истории"); continue; }
    const sigs = oteRun(k15, k5, { rth: true, cost: 0.0002 }).map((s) => ({ ...s, sym }));
    all.push(...sigs); console.log(`${sigs.length} сетапов`);
  }
  const B = bot(pick(all));
  const f = (x, d = 2) => (x == null ? "—" : (x >= 0 ? "+" : "") + x.toFixed(d));
  const rows = [
    ["бот, все сделки", B],
    ["ГИПОТЕЗА: есть ключевое открытие у входа", B.filter((s) => s.kinds.includes("open"))],
    ["без ключевого открытия", B.filter((s) => !s.kinds.includes("open"))],
    ["вход в 9:30–12:00 NY (утро)", B.filter((s) => s.min >= 570 && s.min < 720)],
  ];
  const out = { rows: [], perSym: {} };
  console.log("\n== Акции и ETF, правила шага 6 без изменений");
  for (const [name, list] of rows) {
    const st = stat(list.map((s) => s.res)); out.rows.push({ name, ...(st || { n: list.length }) });
    console.log(st ? `  ${name.padEnd(44)} n=${String(st.n).padStart(4)} win ${st.win.toFixed(0).padStart(2)}%  ${f(st.avgR)}R  t ${f(st.t, 1)}  PF ${st.pf ? st.pf.toFixed(2) : "—"}  половины ${f(st.first)}/${f(st.second)}` : `  ${name.padEnd(44)} n=${list.length} (мало)`);
  }
  for (const sym of STOCKS) { const st = stat(B.filter((s) => s.sym === sym && s.kinds.includes("open")).map((s) => s.res)); out.perSym[sym] = st ? { n: st.n, avgR: +st.avgR.toFixed(2) } : null; }
  console.log("  с открытием по тикерам: " + Object.entries(out.perSym).map(([s, v]) => `${s} ${v ? `${f(v.avgR)}/${v.n}` : "—"}`).join("  "));
  fs.writeFileSync(path.join(__dirname, "results", "step6b_crossmarket.json"), JSON.stringify(out, null, 1));
})();
