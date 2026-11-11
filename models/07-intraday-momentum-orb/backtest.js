// Step 8 — two intraday ideas with academic backing, both one trade a day, flat by the close (prop-firm friendly).
//
// A. Intraday momentum (Gao, Han, Li, Zhou 2018, "Market intraday momentum"): the return from yesterday's close to the
//    end of the first half-hour predicts the last half-hour. Trade 15:30 → 16:00 NY in the direction of that return.
// B. Opening range breakout: trade the break of the first N minutes' range, stop on the other side (or the middle),
//    flat at the close. ORB-5 as in Zarattini & Aziz 2023: the first 5m candle's colour → entry 9:35, stop at its
//    extreme, target 10R or the close.
//
// Main models, fixed BEFORE the run (everything else is a variant / control):
//   A-main: 1h data, first hour (close 16:00 → 10:30) → last half-hour 15:30–16:00.  B-main: ORB-60, 1h data, stop at the other side.
// Data: Yahoo 1h, Dec 2023 → now (QQQ / SPY = NQ / ES; IWM / DIA as markets the idea wasn't looked at on) and the
// Binance perps 5m (Apr → Nov 2026) for the exact 30-min / 5-min versions. Costs: CME ~0.4 bp round trip.
// Then: halves, random-direction baseline, and an Apex-style 50k evaluation (trailing DD $2,500, target $3,000).
//   node step8_intraday.js
const fs = require("fs");
const path = require("path");
const { klines, QT } = require("../../lib/intraday.js");
const CACHE = path.join(__dirname, "..", "..", "data-cache"), OUT = path.join(__dirname, "results");
const COST = 0.00004;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const yahoo = async (sym, interval, range) => {
  const file = path.join(CACHE, `Y_${sym.replace(/[^A-Z]/g, "")}_${interval}.json`);
  if (fs.existsSync(file) && Date.now() - fs.statSync(file).mtimeMs < 12 * 3600e3) return JSON.parse(fs.readFileSync(file, "utf8"));
  const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?interval=${interval}&range=${range}&includePrePost=false`, { headers: { "User-Agent": "Mozilla/5.0" } });
  const j = (await r.json()).chart.result[0], q = j.indicators.quote[0];
  const out = j.timestamp.map((t, i) => [t * 1000, q.open[i], q.high[i], q.low[i], q.close[i]]).filter((b) => b.slice(1).every((x) => x != null));
  fs.writeFileSync(file, JSON.stringify(out)); await sleep(400);
  return out;
};
// group bars into US sessions: { date, bars: Map(minuteOfDay → [o,h,l,c]) }
const sessions = (bars) => {
  const days = new Map();
  for (const b of bars) { const e = QT.et(b[0]); if (e.min < 570 || e.min >= 960) continue; if (!days.has(e.date)) days.set(e.date, { date: e.date, bars: new Map() }); days.get(e.date).bars.set(e.min, b.slice(1).map(Number)); }
  return [...days.values()].sort((a, b) => a.date.localeCompare(b.date));
};
// 5m perps → the same shape (RTH only)
const sessions5 = (k5) => sessions(k5.map((k) => [k[0], +k[1], +k[2], +k[3], +k[4]]));

const stat = (trades, key = "ret") => {
  const r = trades.map((t) => t[key]); if (r.length < 10) return null;
  const n = r.length, m = r.reduce((a, x) => a + x, 0) / n, sd = Math.sqrt(r.reduce((a, x) => a + (x - m) ** 2, 0) / (n - 1));
  let eq = 0, pk = 0, dd = 0; for (const x of r) { eq += x; pk = Math.max(pk, eq); dd = Math.min(dd, eq - pk); }
  const half = Math.floor(n / 2), avg = (a) => a.reduce((s, x) => s + x, 0) / a.length;
  return { n, win: (r.filter((x) => x > 0).length / n) * 100, avg: m, t: sd ? m / (sd / Math.sqrt(n)) : 0, sum: eq, dd, first: avg(r.slice(0, half)), second: avg(r.slice(half)), sd };
};

// ---------------- A. intraday momentum
// first: [min of the bar whose close ends the "first" window]; last: [min of the first bar of the last half-hour, bar size]
function momentum(days, { firstEnd, lastStart, lastEnd, filter = "base" }) {
  const out = [];
  let prevClose = null; const hist = [];
  for (const d of days) {
    const fb = d.bars.get(firstEnd), entryBar = [...d.bars.keys()].filter((m) => m < lastStart).sort((a, b) => b - a)[0], exitBar = d.bars.get(lastEnd);
    const close = d.bars.get(lastEnd)?.[3];
    if (prevClose != null && fb && entryBar != null && exitBar) {
      const r1 = fb[3] / prevClose - 1, entry = d.bars.get(entryBar)[3];
      const prevBarMin = [...d.bars.keys()].filter((m) => m < lastStart && m >= lastStart - 60).sort((a, b) => a - b)[0];
      const r12 = prevBarMin != null ? entry / d.bars.get(prevBarMin)[0] - 1 : 0;
      const sd = hist.length >= 20 ? Math.sqrt(hist.slice(-20).reduce((a, x) => a + x * x, 0) / 20) : null;
      let dir = Math.sign(r1);
      if (filter === "agree") dir = Math.sign(r1) === Math.sign(r12) ? Math.sign(r1) : 0;
      if (filter === "strong") dir = sd != null && Math.abs(r1) > sd ? Math.sign(r1) : 0;
      if (filter === "long") dir = 1;
      if (filter === "r12") dir = Math.sign(r12);
      const move = close / entry - 1;
      if (dir !== 0) out.push({ date: d.date, dir, ret: dir * move - COST, move, entry });
      hist.push(r1);
    }
    if (close != null) prevClose = close;
  }
  return out;
}

// ---------------- B. opening range breakout
// orEnd: first minute after the range; bar: bar size in minutes; stopAt: "other" | "mid"; T: target in R (null = close)
function orb(days, { orEnd, bar, stopAt = "other", T = null, colour = false }) {
  const out = [];
  for (const d of days) {
    const mins = [...d.bars.keys()].sort((a, b) => a - b); if (!mins.length || mins[0] !== 570 || mins[mins.length - 1] < 960 - bar) continue;
    const rng = mins.filter((m) => m < orEnd).map((m) => d.bars.get(m)), after = mins.filter((m) => m >= orEnd);
    const H = Math.max(...rng.map((b) => b[1])), L = Math.min(...rng.map((b) => b[2])), last = d.bars.get(mins[mins.length - 1])[3];
    if ((H - L) / L < 0.0003) continue;              // a flat / dead opening range (perp data gaps): no meaningful stop
    let dir = 0, entry, stop, k0;
    if (colour) {                                  // Zarattini: colour of the first candle, entry at the next open
      const b0 = rng[0]; dir = Math.sign(b0[3] - b0[0]); if (!dir) continue;
      entry = d.bars.get(after[0])[0]; stop = dir > 0 ? L : H; k0 = 0;
      if ((entry - stop) * dir <= 0) continue;
    } else {
      for (let k = 0; k < after.length; k++) {
        const b = d.bars.get(after[k]), up = b[1] > H, dn = b[2] < L;
        if (up && dn) { dir = 2; k0 = k; break; }      // both sides in one bar: order unknown → count as a stop
        if (up || dn) { dir = up ? 1 : -1; k0 = k; entry = up ? Math.max(H, b[0]) : Math.min(L, b[0]); break; }
      }
      if (!dir) continue;
      if (dir === 2) { out.push({ date: d.date, dir: 0, R: -1, ret: -(H - L) / ((H + L) / 2) - COST }); continue; }
      stop = stopAt === "mid" ? (H + L) / 2 : dir > 0 ? L : H;
    }
    const R = Math.abs(entry - stop), tgt = T ? entry + dir * T * R : null;
    let exit = last, how = "close";
    for (let k = k0; k < after.length; k++) {
      const b = d.bars.get(after[k]);
      // entry bar (breakout): only its part after the break is unknown — a stop inside it counts (conservative)
      if (dir > 0 ? b[2] <= stop : b[1] >= stop) { exit = stop; how = "stop"; break; }
      if (tgt != null && (dir > 0 ? b[1] >= tgt : b[2] <= tgt) && !(colour === false && k === k0)) { exit = tgt; how = "target"; break; }
    }
    out.push({ date: d.date, dir, entry, stop, exit, how, R: ((exit - entry) * dir) / R - (COST * entry) / R, ret: ((exit - entry) * dir) / entry - COST });
  }
  return out;
}

// random direction at the same moment with the same stop distance (ORB) — is the breakout direction worth anything?
function orbRandom(days, opts, runs = 200) {
  let seed = 4242; const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  const real = orb(days, opts).filter((t) => t.dir !== 0), byDate = new Map(days.map((d) => [d.date, d])), avgs = [];
  for (let r = 0; r < runs; r++) {
    let s = 0;
    for (const t of real) {
      const d = byDate.get(t.date), dir = rnd() < 0.5 ? 1 : -1, R = Math.abs(t.entry - t.stop), stop = t.entry - dir * R, mins = [...d.bars.keys()].sort((a, b) => a - b).filter((m) => m >= opts.orEnd);
      let exit = d.bars.get(mins[mins.length - 1])[3], started = false;
      for (const m of mins) {
        const b = d.bars.get(m);
        if (!started) { if ((t.dir > 0 ? b[1] >= t.entry : b[2] <= t.entry) || opts.colour) started = true; else continue; }
        if (dir > 0 ? b[2] <= stop : b[1] >= stop) { exit = stop; break; }
      }
      s += ((exit - t.entry) * dir) / R;
    }
    avgs.push(s / real.length);
  }
  return avgs.sort((a, b) => a - b);
}

// Apex-style 50k evaluation: trailing drawdown $2,500 from the highest closed balance, target +$3,000, $ per R fixed.
// Started on every day of the history; pass rate vs the same trades with random signs (a strategy with zero edge).
function propSim(rs, perR = 250) {
  const run = (arr) => {
    let pass = 0, fail = 0, days = [];
    for (let s = 0; s < arr.length; s++) {
      let bal = 0, pk = 0, k = s;
      for (; k < arr.length; k++) { bal += arr[k] * perR; pk = Math.max(pk, bal); if (bal >= 3000) { pass++; days.push(k - s + 1); break; } if (bal <= pk - 2500) { fail++; break; } }
    }
    const done = pass + fail; days.sort((a, b) => a - b);
    return { pass: done ? pass / done : null, tests: done, medDays: days.length ? days[Math.floor(days.length / 2)] : null };
  };
  // zero edge = the same trades, same order and shape, with the average result subtracted
  const m = rs.reduce((a, x) => a + x, 0) / rs.length;
  return { ...run(rs), zeroEdge: run(rs.map((x) => x - m)).pass };
}

(async () => {
  const f = (x, d = 2) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "") + x.toFixed(d));
  const bp = (x) => f(x * 1e4, 1) + " б.п.";
  const H1 = {}, P5 = {};
  for (const s of ["QQQ", "SPY", "IWM", "DIA"]) { H1[s] = sessions(await yahoo(s, "1h", "730d")); }
  for (const s of ["QQQ", "SPY"]) P5[s] = sessions5(await klines(s, "5m"));
  const NAME = { QQQ: "NQ (QQQ)", SPY: "ES (SPY)", IWM: "Russell (IWM)", DIA: "Dow (DIA)" };
  const rep = { A: [], B: [], prop: {} };
  console.log(`1h: ${H1.QQQ[0].date} → ${H1.QQQ[H1.QQQ.length - 1].date}, ${H1.QQQ.length} дней; 5m перпы: ${P5.QQQ.length} дней`);

  // ---- A
  const lineA = (sect, name, tr) => { const st = stat(tr); rep.A.push({ sect, name, ...(st || { n: tr.length }) }); console.log(st ? `  ${name.padEnd(50)} n=${String(st.n).padStart(4)} win ${st.win.toFixed(0)}%  ${bp(st.avg).padStart(10)}  t ${f(st.t, 1)}  Sharpe ${f((st.avg / st.sd) * Math.sqrt(252), 2)}  половины ${bp(st.first)} / ${bp(st.second)}` : `  ${name} мало`); return st; };
  console.log("\n== A. Внутридневной моментум: последние 30 мин по направлению начала дня (1h, 3 года)");
  const A1 = { firstEnd: 570, lastStart: 930, lastEnd: 930 };   // 1h bars: the 9:30 bar closes 10:30; last bar 15:30 is 30 min
  // with 1h bars the entry is the close of the 14:30 bar (15:30) and the exit is the close of the 15:30 bar (16:00)
  const momH = (days, filter) => momentum(days, { ...A1, filter });
  const mainA = [...momH(H1.QQQ, "base"), ...momH(H1.SPY, "base")].sort((a, b) => a.date.localeCompare(b.date));
  lineA("1h", "ОСНОВНАЯ: NQ+ES, первый час → последние 30 мин", mainA);
  for (const s of ["QQQ", "SPY", "IWM", "DIA"]) lineA("1h", `  ${NAME[s]}`, momH(H1[s], "base"));
  lineA("1h", "фильтр: и первый час, и 14:30–15:30 в одну сторону", [...momH(H1.QQQ, "agree"), ...momH(H1.SPY, "agree")]);
  lineA("1h", "фильтр: сильное начало дня (|r| > σ за 20 дней)", [...momH(H1.QQQ, "strong"), ...momH(H1.SPY, "strong")]);
  lineA("1h", "контроль: только 14:30–15:30 (без начала дня)", [...momH(H1.QQQ, "r12"), ...momH(H1.SPY, "r12")]);
  lineA("1h", "контроль: всегда лонг в последние 30 мин", [...momH(H1.QQQ, "long"), ...momH(H1.SPY, "long")]);
  console.log("== A на 5m (перпы, 7 мес.): точно как в статье — первые 30 мин → последние 30 мин");
  const A5 = { firstEnd: 595, lastStart: 930, lastEnd: 955 };
  lineA("5m", "NQ+ES, 16:00 → 10:00 предсказывает 15:30 → 16:00", [...momentum(P5.QQQ, A5), ...momentum(P5.SPY, A5)]);

  // ---- B
  const lineB = (sect, name, tr) => { const all = tr.filter(Boolean), st = stat(all, "R"), sr = stat(all, "ret"); rep.B.push({ sect, name, ...(st || { n: all.length }), sharpe: sr ? (sr.avg / sr.sd) * Math.sqrt(252) : null });
    console.log(st ? `  ${name.padEnd(50)} n=${String(st.n).padStart(4)} win ${st.win.toFixed(0)}%  ${f(st.avg)}R  t ${f(st.t, 1)}  DD ${st.dd.toFixed(1)}R  Sharpe ${f(sr ? (sr.avg / sr.sd) * Math.sqrt(252) : null)}  половины ${f(st.first)}/${f(st.second)}` : `  ${name} мало`); return st; };
  console.log("\n== B. Opening range breakout (стоп на другой стороне диапазона, выход на закрытии)");
  const B60 = { orEnd: 630, bar: 60 };
  const mainB = [...orb(H1.QQQ, B60), ...orb(H1.SPY, B60)].sort((a, b) => a.date.localeCompare(b.date));
  lineB("1h", "ОСНОВНАЯ: ORB-60, NQ+ES, 3 года", mainB);
  for (const s of ["QQQ", "SPY", "IWM", "DIA"]) lineB("1h", `  ${NAME[s]}`, orb(H1[s], B60));
  lineB("1h", "ORB-60, стоп в середине диапазона", [...orb(H1.QQQ, { ...B60, stopAt: "mid" }), ...orb(H1.SPY, { ...B60, stopAt: "mid" })]);
  lineB("1h", "ORB-60, цель 2R", [...orb(H1.QQQ, { ...B60, T: 2 }), ...orb(H1.SPY, { ...B60, T: 2 })]);
  console.log("== B на 5m (перпы, 7 мес.)");
  for (const m of [5, 15, 30]) {
    const o = { orEnd: 570 + m, bar: 5 };
    lineB("5m", `ORB-${m}, стоп на другой стороне`, [...orb(P5.QQQ, o), ...orb(P5.SPY, o)]);
    lineB("5m", `ORB-${m}, стоп в середине`, [...orb(P5.QQQ, { ...o, stopAt: "mid" }), ...orb(P5.SPY, { ...o, stopAt: "mid" })]);
  }
  const Z = { orEnd: 575, bar: 5, colour: true };
  lineB("5m", "ORB-5 по Zarattini (цвет 1-й свечи, цель 10R)", [...orb(P5.QQQ, { ...Z, T: 10 }), ...orb(P5.SPY, { ...Z, T: 10 })]);
  lineB("5m", "ORB-5 по Zarattini, выход на закрытии", [...orb(P5.QQQ, Z), ...orb(P5.SPY, Z)]);
  const rB = [...orbRandom(H1.QQQ, B60), ...orbRandom(H1.SPY, B60)].sort((a, b) => a - b), mB = stat(mainB.filter(Boolean), "R");
  console.log(`  случайное направление в тот же момент, тот же стоп (ORB-60): медиана ${f(rB[Math.floor(rB.length / 2)])}R, 95% ${f(rB[Math.floor(rB.length * 0.95)])}R; модель ${f(mB.avg)}R`);
  rep.randomB = { p50: rB[Math.floor(rB.length / 2)], p95: rB[Math.floor(rB.length * 0.95)] };

  // ---- prop evaluation on NQ alone (one account = one instrument)
  console.log("\n== Проп-челлендж 50k (трейлинг $2,500, цель $3,000), только NQ, старт с каждого дня истории");
  const nqA = momH(H1.QQQ, "base"), sdA = Math.sqrt(nqA.reduce((a, t) => a + t.ret * t.ret, 0) / nqA.length);
  const nqB = orb(H1.QQQ, B60).filter(Boolean);
  for (const [name, rs] of [["A: моментум (размер: 1σ движения = $250)", nqA.map((t) => t.ret / sdA)], ["B: ORB-60 (риск $250 на сделку)", nqB.map((t) => t.R)]]) {
    for (const perR of [250, 500]) {
      const p = propSim(rs, perR); rep.prop[`${name} ${perR}`] = p;
      console.log(`  ${name.replace("250", String(perR)).padEnd(46)} проходит ${(p.pass * 100).toFixed(0)}% (медиана ${p.medDays} дней); стратегия без преимущества: ${(p.zeroEdge * 100).toFixed(0)}%`);
    }
  }
  fs.writeFileSync(path.join(OUT, "step8_intraday.json"), JSON.stringify(rep, null, 1));
})();
