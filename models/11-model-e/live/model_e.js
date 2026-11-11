// Model E on a HeroFX demo account — the daily signal (research step 13):
//   risk parity over 7 index CFDs + gold + silver, a market below its 200-day average gets 0, equities / metals balanced
//   by risk, 10% annual volatility, daily check with a 60-day covariance and a 25% no-trade band. Long only.
// Run once a day after the US close (22:15+ Poland). It writes:
//   plan.md         — target lots per instrument, what to buy / sell now, and the model's paper record since goLive
//   journal.csv     — one line per run (weights, lots, equity)
//   positions.json  — the lots you hold (the script assumes you executed the last plan; fix by hand if not)
// Never places orders and never logs in anywhere: execution is yours, in the platform.
//   node model_e.js                 daily run
//   node model_e.js --equity 101500 set the account balance (USD) first
const fs = require("fs");
const path = require("path");
const { buildData, backtest, metrics } = require("../../../lib/portfolio.js");
const DIR = __dirname, CFG_F = path.join(DIR, "herofx.json"), POS_F = path.join(DIR, "positions.json");
const CFG = JSON.parse(fs.readFileSync(CFG_F, "utf8"));
const arg = (k) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
if (arg("--equity")) { CFG.equity = +arg("--equity"); fs.writeFileSync(CFG_F, JSON.stringify(CFG, null, 2)); }

// the exact universe and settings of step 13, model E
const F = (key, sym, cost, cls) => ({ key, sym, fund: true, rate: null, cost, markup: 0.025, rp: true, cls, maxMove: 0.3 });
const A = [F("US500", "SPY", 1, "eq"), F("US100", "QQQ", 1, "eq"), F("US30", "DIA", 1.5, "eq"), F("US2000", "IWM", 1.5, "eq"), F("GER40", "EWG", 1.5, "eq"),
  F("JP225", "EWJ", 1.5, "eq"), F("UK100", "EWU", 1.5, "eq"), F("XAU", "GLD", 3, "metal"), F("XAG", "SLV", 5, "metal")];
// live prices for sizing: the cash index / spot-like quote and its currency
const QUOTE = { US500: ["^GSPC", "USD"], US100: ["^NDX", "USD"], US30: ["^DJI", "USD"], US2000: ["^RUT", "USD"], GER40: ["^GDAXI", "EUR"], JP225: ["^N225", "JPY"], UK100: ["^FTSE", "GBP"], XAU: ["GC=F", "USD"], XAG: ["SI=F", "USD"] };
const FXQ = { EUR: ["EURUSD=X", false], GBP: ["GBPUSD=X", false], JPY: ["JPY=X", true] };

const etNow = () => { const o = {}; for (const p of new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).formatToParts(Date.now())) o[p.type] = p.value; return { date: `${o.year}-${o.month}-${o.day}`, min: +o.hour * 60 + +o.minute }; };
const last = async (sym) => {
  const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?interval=1d&range=5d`, { headers: { "User-Agent": "Mozilla/5.0" } });
  return (await r.json()).chart.result[0].meta.regularMarketPrice;
};
const pc = (x, d = 1) => (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%";

(async () => {
  // fresh daily closes: drop this model's cached ETF files older than an hour
  const cache = path.join(DIR, "..", "..", "..", "data-cache");
  for (const a of A) { const f = path.join(cache, `D_${a.sym}_adj.json`); if (fs.existsSync(f) && Date.now() - fs.statSync(f).mtimeMs > 3600e3) fs.unlinkSync(f); }
  for (const k of ["IR3TIB01USM156N"]) { const f = path.join(cache, `F_${k}.csv`); if (fs.existsSync(f) && Date.now() - fs.statSync(f).mtimeMs > 7 * 864e5) fs.unlinkSync(f); }

  const enabled = A.filter((a) => CFG.instruments[a.key]?.enabled !== false);
  let D = await buildData(A, "2004-01-01");
  // only completed sessions: before 16:15 New York today's bar is still moving
  const now = etNow();
  let T = D.dates.length;
  if (D.dates[T - 1] === now.date && now.min < 975) { const cut = (x) => x.slice(0, T - 1); D = { ...D, dates: cut(D.dates), R: D.R.map(cut), RL: D.RL.map(cut), carry: D.carry.map(cut), active: D.active.map(cut), vol: D.vol.map(cut), diff: D.diff.map(cut) }; T--; }
  // instruments the broker doesn't have are switched off for the model itself, so the risk is spread over the rest
  D = { ...D, active: D.active.map((act, i) => (enabled.some((a) => a.key === A[i].key) ? act : act.map(() => false))) };
  const cnt = {}; for (const a of enabled) cnt[a.cls] = (cnt[a.cls] || 0) + 1;
  const wts = Object.fromEntries(A.map((a) => [a.key, cnt[a.cls] ? 1 / cnt[a.cls] : 0]));
  const bt = backtest(D, { strategy: "rpsma", wts, rebal: 1, covL: 60, band: 0.25 });
  const row = bt.wHist[bt.wHist.length - 1], asOf = row[0], W = Object.fromEntries(A.map((a, i) => [a.key, row[i + 1]]));

  // weights → lots
  const fx = { USD: 1 }; for (const [c, [s, inv]] of Object.entries(FXQ)) { const p = await last(s); fx[c] = inv ? 1 / p : p; }
  const pos = fs.existsSync(POS_F) ? JSON.parse(fs.readFileSync(POS_F, "utf8")) : {};
  const lines = [], newPos = {}, warn = [];
  for (const a of A) {
    const spec = CFG.instruments[a.key], [qs, ccy] = QUOTE[a.key];
    if (spec.enabled === false) continue;
    const px = await last(qs), notionalLot = px * fx[ccy] * spec.contractSize, w = W[a.key] || 0;
    let lots = Math.floor(((w * CFG.equity) / notionalLot) / spec.lotStep + 1e-9) * spec.lotStep;
    if (w > 0 && lots < spec.minLot) { warn.push(`${spec.symbol}: модель хочет ${(w * 100).toFixed(1)}% счёта = ${((w * CFG.equity) / notionalLot).toFixed(3)} лота — меньше минимального ${spec.minLot}, ставлю 0. Нужен баланс больше.`); lots = 0; }
    lots = +lots.toFixed(2);
    const have = pos[a.key] || 0, delta = +(lots - have).toFixed(2);
    // the 25% band again at the lot level, so rounding never makes you trade back and forth
    const act = delta !== 0 && (lots === 0 || have === 0 || Math.abs(delta) >= 0.25 * have);
    newPos[a.key] = act ? lots : have;
    lines.push({ key: a.key, sym: spec.symbol, w, px, ccy, notional: (act ? lots : have) * notionalLot, lots: act ? lots : have, have, delta: act ? delta : 0 });
  }

  // the model's own paper record since go-live (independent of rounding and of how you execute)
  const m = metrics(D.dates, bt.ret, CFG.goLive, "2100");
  let eq = 1; for (let t = 0; t < D.dates.length; t++) if (D.dates[t] >= CFG.goLive) eq *= 1 + bt.ret[t];
  const days = D.dates.filter((d) => d >= CFG.goLive).length;

  // targets for the MT5 expert ModelE.mq5: model weights (fraction of equity), the EA sizes lots from the broker's own specs
  const MT5 = CFG.executor === "mt5";
  if (MT5) {
    const common = path.join(process.env.APPDATA || "", "MetaQuotes", "Terminal", "Common", "Files", "ModelE");
    fs.mkdirSync(common, { recursive: true });
    const csv = [`asOf,${asOf}`, `generated,${new Date().toISOString()}`, ...A.filter((a) => CFG.instruments[a.key].enabled !== false).map((a) => `${a.key},${CFG.instruments[a.key].symbol},${(W[a.key] || 0).toFixed(5)}`)].join("\r\n") + "\r\n";
    fs.writeFileSync(path.join(common, "targets.tmp"), csv);
    fs.renameSync(path.join(common, "targets.tmp"), path.join(common, "targets.csv"));   // atomic: the EA never reads half a file
    fs.writeFileSync(path.join(DIR, "targets.csv"), csv);
  }
  const trades = MT5 ? [] : lines.filter((l) => l.delta !== 0), gross = lines.reduce((s, l) => s + l.notional, 0) / CFG.equity;
  let md = `# Модель E — план на следующую сессию\n\nСигнал по закрытию **${asOf}** (Нью-Йорк). Баланс в расчёте: **$${CFG.equity.toLocaleString("en-US")}**. Плечо после плана: ${gross.toFixed(2)}x.\n\n`;
  md += MT5 ? "## Исполнение\n\nЦели переданы советнику **ModelE** в MT5 (`Common\\Files\\ModelE\\targets.csv`): он сам пересчитает лоты по балансу и спецификациям HeroFX и сделает сделки, когда рынок открыт. Руками ничего не открывай — советник видит только свои позиции. Лоты ниже — ориентир.\n\n"
    : trades.length ? `## Сделать сейчас (рыночными ордерами, без стопов — риск держит размер позиций)\n\n| Инструмент | Действие | Лотов | Станет |\n|---|---|---|---|\n` + trades.map((l) => `| ${l.sym} | ${l.delta > 0 ? "КУПИТЬ" : "ПРОДАТЬ (закрыть часть)"} | ${Math.abs(l.delta).toFixed(2)} | ${l.lots.toFixed(2)} |`).join("\n") + "\n\n"
    : "## Сегодня ничего делать не нужно\n\nИзменения меньше 25% от позиции — их пропускаем, чтобы не платить спред.\n\n";
  md += `## Целевой портфель\n\n| Инструмент | Доля счёта | Цена | Лотов | Объём, $ |\n|---|---|---|---|---|\n` + lines.map((l) => `| ${l.sym} | ${(l.w * 100).toFixed(1)}% | ${l.px.toLocaleString("en-US")} ${l.ccy} | ${l.lots.toFixed(2)} | ${Math.round(l.notional).toLocaleString("en-US")} |`).join("\n") + "\n\n";
  md += "Доля 0% — рынок ниже своей 200-дневной средней, модель его не держит.\n\n";
  if (warn.length) md += `## Внимание\n\n${warn.map((x) => `- ${x}`).join("\n")}\n\n`;
  md += `## Бумажный учёт модели с ${CFG.goLive}\n\n${days < 2 ? "Пока нет торговых дней — учёт начнётся с первой закрытой сессии.\n" : `${days} торговых дней: ${pc(eq - 1, 2)}${m ? `, волатильность ${pc(m.vol)} в год, макс. просадка ${pc(m.maxDD)}` : ""}. Ориентир из бэктеста: около +4% в год при 10% волатильности, просадки до −28% нормальны. Выводы — не раньше чем через 1–2 года.\n`}\n`;
  md += `_Это исследовательская модель на демо-счёте, не совет вкладывать деньги._\n`;
  fs.writeFileSync(path.join(DIR, "plan.md"), md);
  fs.writeFileSync(POS_F, JSON.stringify(newPos, null, 2));
  const jf = path.join(DIR, "journal.csv");
  if (!fs.existsSync(jf)) fs.writeFileSync(jf, "run,asOf,equity,model_since_golive," + A.map((a) => `w_${a.key}`).join(",") + "," + A.map((a) => `lots_${a.key}`).join(",") + "\n");
  fs.appendFileSync(jf, [new Date().toISOString(), asOf, CFG.equity, (eq - 1).toFixed(5), ...A.map((a) => (W[a.key] || 0).toFixed(4)), ...A.map((a) => newPos[a.key] ?? "")].join(",") + "\n");
  console.log(md);
})().catch((e) => { console.error("ОШИБКА:", e.message); process.exit(1); });
