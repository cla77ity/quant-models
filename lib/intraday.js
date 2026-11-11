// Shared intraday engine: data loader (Binance perps, cached in data-cache/), the OTE model, account rules,
// statistics, random-entry baseline and Monte Carlo. Used by the intraday models in models/.
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
//   (library — the OTE backtest itself is models/05-ote-tick/backtest.js)
const fs = require("fs");
const path = require("path");
const CACHE = path.join(__dirname, "..", "data-cache");
fs.mkdirSync(CACHE, { recursive: true });

const etFmt = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", weekday: "short" });
const QT = { PROXY: {}, et: (ms) => { const o = {}; for (const p of etFmt.formatToParts(ms)) o[p.type] = p.value; return { date: `${o.year}-${o.month}-${o.day}`, min: +o.hour * 60 + +o.minute, dow: o.weekday }; } };
global.window = { QT };
eval(fs.readFileSync(path.join(__dirname, "ictengine.js"), "utf8"));
eval(fs.readFileSync(path.join(__dirname, "rbfib.js"), "utf8"));

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const klines = async (sym, tf) => {
  const file = path.join(CACHE, `${sym}_${tf}.json`);
  if (fs.existsSync(file) && Date.now() - fs.statSync(file).mtimeMs < 12 * 3600e3) return JSON.parse(fs.readFileSync(file, "utf8"));
  let out = [], end = null;
  try {
    for (;;) {
      const r = await fetch(`https://fapi.binance.com/fapi/v1/klines?symbol=${sym}USDT&interval=${tf}&limit=1500${end ? "&endTime=" + end : ""}`);
      if (r.status === 429 || r.status === 418) { await sleep(30000); continue; }
      const k = await r.json();
      if (!Array.isArray(k) || !k.length) break;
      out = k.concat(out); end = k[0][0] - 1;
      await sleep(250);
      if (k.length < 1500) break;
    }
  } catch (e) { console.log(`  (сеть: ${e.message})`); }
  if (out.length) { fs.writeFileSync(file, JSON.stringify(out)); return out; }
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf8")) : [];
};

const P0 = { legBars: 48, legATR: 3, levels: [0.62, 0.705, 0.79], tolATR: 0.15, minConf: 3, mode: "agg", stopMode: "rule", stopBuf: 0.1,
  minR_ATR: 0.5, maxPivotATR: 3, wait: 48, trigBars: 6, ihBars: 6, hold: 96, cost: 0.00004, T: 5, be: "ih", rth: false };
const VT = [2, 3, 5, "leg"], VB = ["none", "1R", "ih"], key = (T, be) => `${T}|${be}`;

const atrOf = (h, l, c) => {
  const n = h.length, a5 = new Array(n).fill(null);
  for (let i = 1, a = 0; i < n; i++) { const tr = Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])); a = i <= 14 ? a + tr / 14 : a + (tr - a) / 14; if (i >= 14) a5[i] = a; }
  return a5;
};
const trendFn = (k15) => {
  const t15 = k15.map((k) => k[0]), c15 = k15.map((k) => +k[4]);
  return (t) => {
    let lo = 0, hi = t15.length - 1, i = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (t15[m] + 15 * 60e3 <= t) { i = m; lo = m + 1; } else hi = m - 1; }
    if (i < 50) return null;
    let s = 0; for (let q = i - 49; q <= i; q++) s += c15[q];
    return c15[i] > s / 50;
  };
};

function oteRun(k15, k5, opts = {}) {
  const P = { ...P0, ...opts };
  const t5 = k5.map((k) => k[0]), o = k5.map((k) => +k[1]), h = k5.map((k) => +k[2]), l = k5.map((k) => +k[3]), c = k5.map((k) => +k[4]);
  const atr5 = atrOf(h, l, c), trendAt = P.trendOf ? P.trendOf(k15) : trendFn(k15);
  const legAt = (j, bull) => {
    const a = Math.max(0, j - P.legBars);
    let iX = -1; for (let q = a; q <= j; q++) if (iX < 0 || (bull ? h[q] > h[iX] : l[q] < l[iX])) iX = q;
    if (iX <= a || iX === j) return null;
    let iO = a; for (let q = a; q <= iX; q++) if (bull ? l[q] < l[iO] : h[q] > h[iO]) iO = q;
    const X = bull ? h[iX] : l[iX], O = bull ? l[iO] : h[iO];
    let counter = false, pb = bull ? Infinity : -Infinity;
    for (let q = iX + 1; q <= j; q++) { if (bull ? c[q] < o[q] : c[q] > o[q]) counter = true; pb = bull ? Math.min(pb, l[q]) : Math.max(pb, h[q]); }
    return { X, O, iX, iO, size: Math.abs(X - O), counter, pb };
  };
  const out = [], live = [], used = new Set();
  const fin = (cd, v, how, px, k) => { const d = cd.bull ? 1 : -1; v.done = true; v.how = how; v.exitT = t5[k]; v.exitPx = px; v.res = ((px - cd.entry) * d) / cd.R - (P.cost * cd.entry) / cd.R; };
  const PK = key(P.T, P.be);
  const sync = (cd) => {
    const v = cd.v[PK]; if (!v) return;
    if (v.done) Object.assign(cd, { state: v.how === "be" ? "stop" : v.how, res: v.res, exitT: v.exitT, exitPx: v.exitPx });
    else cd.state = "open";
  };
  const fill = (cd, j, px) => {
    const d = cd.bull ? 1 : -1;
    cd.entry = px; cd.R = Math.abs(px - cd.stop); cd.fillJ = j; cd.fillT = t5[j]; cd.v = {};
    let ih = null; for (let q = Math.max(0, j - P.ihBars); q < j; q++) ih = ih == null ? (cd.bull ? h[q] : l[q]) : cd.bull ? Math.max(ih, h[q]) : Math.min(ih, l[q]);
    cd.ih = ih != null && (ih - px) * d > 0 ? ih : null;
    for (const T of VT) for (const be of VB) {
      const tgt = T === "leg" ? ((cd.legX - px) * d >= 1.5 * cd.R ? cd.legX : null) : px + d * T * cd.R;
      cd.v[key(T, be)] = { tgt, stop: cd.stop, be, done: tgt == null };
    }
  };

  QT.ictRun(k15, k5, true, null, (x) => {
    const { j, e, zones, pools, opens } = x;
    // stocks (rth): US hours only, flat by 15:55; index futures: flat 16:55, no fills 16:45–18:00
    const flatTime = P.rth ? e.min >= 955 || e.min < 570 : e.min >= 1015 && e.min < 1080,
      noFill = P.rth ? !(e.min >= 570 && e.min < 945) : e.min >= 1005 && e.min < 1080, inOpen = e.min >= 570 && e.min < 600;

    for (const cd of live) {
      const d = cd.bull ? 1 : -1;
      if (cd.state === "limit" || cd.state === "touch") {
        if (flatTime) { cd.state = "expired"; continue; }
        if (d > 0 ? h[j] > cd.legX : l[j] < cd.legX) { cd.state = "redraw"; continue; }     // new extreme: the grid moves
        const touched = d > 0 ? l[j] <= cd.lim : h[j] >= cd.lim;
        if (cd.state === "limit") {
          if (touched && !noFill && cd.mode === "agg" && !inOpen) {
            fill(cd, j, cd.lim); cd.how = "лимит";
            if (d > 0 ? l[j] <= cd.stop : h[j] >= cd.stop) for (const v of Object.values(cd.v)) if (!v.done) fin(cd, v, "stop", cd.stop, j);
            sync(cd); continue;
          }
          if (touched) { cd.state = "touch"; cd.touchJ = j; if (d > 0 ? l[j] <= cd.stop : h[j] >= cd.stop) cd.state = "cancel"; continue; }
          if (j - cd.j0 >= P.wait) cd.state = "expired";
        } else {
          if (d > 0 ? l[j] <= cd.stop : h[j] >= cd.stop) { cd.state = "cancel"; continue; }
          if (j - cd.touchJ > P.trigBars) { cd.state = "expired"; continue; }
          const trig = d > 0 ? c[j] > o[j] && c[j] > h[j - 1] : c[j] < o[j] && c[j] < l[j - 1];
          if (trig && !noFill && (c[j] - cd.stop) * d >= P.minR_ATR * cd.atr) { fill(cd, j, c[j]); cd.how = "триггер"; sync(cd); }   // exits from the next bar
        }
      } else if (cd.state === "open") {
        for (const v of Object.values(cd.v)) {
          if (v.done) continue;
          if (d > 0 ? l[j] <= v.stop : h[j] >= v.stop) fin(cd, v, v.stop === cd.stop ? "stop" : "be", v.stop, j);
          else if (d > 0 ? h[j] >= v.tgt : l[j] <= v.tgt) fin(cd, v, "target", v.tgt, j);
          else if (flatTime || j - cd.fillJ >= P.hold) fin(cd, v, "time", c[j], j);
          else if (v.stop === cd.stop && ((v.be === "1R" && (d > 0 ? h[j] - cd.entry : cd.entry - l[j]) >= cd.R) || (v.be === "ih" && cd.ih != null && (d > 0 ? h[j] > cd.ih : l[j] < cd.ih)))) v.stop = cd.entry;
        }
        sync(cd);
      }
    }
    for (let i = live.length - 1; i >= 0; i--) if (!["limit", "touch", "open"].includes(live[i].state)) live.splice(i, 1);

    const A5 = atr5[j]; if (A5 == null || flatTime) return;
    for (const bull of [true, false]) {
      const L = legAt(j, bull); if (!L || L.size < P.legATR * A5 || !L.counter) continue;
      const lkey = `${bull}|${L.iX}|${L.iO}`; if (used.has(lkey)) continue;
      const d = bull ? 1 : -1, lvl = (f) => L.X - d * f * L.size, tol = P.tolATR * A5;
      if ((L.pb - lvl(0.79)) * d < 0) { used.add(lkey); continue; }               // the pullback already ran through OTE
      // pda: false — plain "OTE + trend": a limit at the first of P.levels the pullback hasn't reached yet, no PD array needed
      if (P.pda === false) {
        const lv = P.levels.find((q) => (lvl(q) - L.pb) * d < 0); if (lv == null) continue;
        const lim = lvl(lv), pivot = L.O - d * P.stopBuf * A5, R = (lim - pivot) * d;
        if (!(R >= P.minR_ATR * A5)) continue;
        used.add(lkey);
        const cd = { bull, t: t5[j], j0: j, lim, entry: lim, stop: pivot, R, legX: L.X, legO: L.O, iX: L.iX, iO: L.iO, lv, conf: ["тренд", `уровень ${lv}`], kinds: [], nConf: 2,
          trend: trendAt(t5[j] + 5 * 60e3) === bull, min: e.min, mode: P.mode, state: "limit", atr: A5 };
        live.push(cd); out.push(cd);
        continue;
      }
      const m = zones.filter((z) => z.bull === bull && z.created <= x.t && (z.ce - L.pb) * d < 0).map((z) => {
        const lv = P.levels.find((q) => Math.abs(z.ce - lvl(q)) <= tol);
        return lv != null ? { z, lv, f: ((L.X - z.ce) * d) / L.size } : null;
      }).filter(Boolean).sort((a, b) => (b.z.ce - a.z.ce) * d);
      if (!m.length) continue;
      const top = m[0], lim = top.z.ce;
      const conf = ["дисконт", `CE ${top.z.kind} 15m на ${top.lv}`], kinds = ["disc", "pda"];
      const second = m.find((q) => q.z !== top.z && Math.abs(q.z.ce - top.z.ce) > tol * 0.2);
      if (second) { conf.push(`ещё CE ${second.z.kind} на ${second.lv}`); kinds.push("stack"); }
      const ko = ["00:00", "09:30", "10:00"].find((nm) => opens[nm] != null && Math.abs(opens[nm] - lim) <= tol);
      if (ko) { conf.push(`открытие ${ko}`); kinds.push("open"); }
      const liq = pools.find((p) => p.kind === (bull ? "SSL" : "BSL") && (p.price - lim) * d > 0 && (L.pb - p.price) * d > 0);
      if (liq) { conf.push(`ликвидность ${liq.tag}`); kinds.push("liq"); }
      if (conf.length < P.minConf) continue;
      const pivot = L.O - d * P.stopBuf * A5, s79 = lvl(0.79) - d * P.stopBuf * A5;
      let stop = P.stopMode === "pivot" ? pivot : P.stopMode === "79" ? s79 : Math.abs(lim - pivot) <= P.maxPivotATR * A5 ? pivot : s79;
      if ((lim - stop) * d < P.minR_ATR * A5) stop = pivot;
      const R = (lim - stop) * d; if (!(R >= P.minR_ATR * A5)) continue;
      used.add(lkey);
      const cd = { bull, t: t5[j], j0: j, lim, entry: lim, stop, R, legX: L.X, legO: L.O, iX: L.iX, iO: L.iO, lv: top.lv, conf, kinds, nConf: conf.length,
        trend: trendAt(t5[j] + 5 * 60e3) === bull, min: e.min, mode: P.mode, state: "limit", atr: A5 };
      live.push(cd); out.push(cd);
    }
  });
  return out;
}

const asV = (s, k) => { const v = s.v && s.v[k]; if (!v || v.tgt == null || !v.done) return null; return { ...s, res: v.res, exitT: v.exitT, exitPx: v.exitPx, state: v.how, tgt: v.tgt }; };
const bot = (list) => [...new Set(list.map((s) => s.sym))].flatMap((sym) => QT.rbfibRisk(list.filter((s) => s.sym === sym), { trend: true })).sort((a, b) => a.fillT - b.fillT);
const pick = (list, k = "5|ih", f = () => true) => list.filter(f).map((s) => asV(s, k)).filter(Boolean).sort((a, b) => a.fillT - b.fillT);
const stat = (r) => {
  if (r.length < 8) return null;
  const n = r.length, m = r.reduce((a, x) => a + x, 0) / n, sd = Math.sqrt(r.reduce((a, x) => a + (x - m) ** 2, 0) / (n - 1));
  let eq = 0, pk = 0, dd = 0, ls = 0, maxLs = 0; for (const x of r) { eq += x; pk = Math.max(pk, eq); dd = Math.min(dd, eq - pk); ls = x < -0.25 ? ls + 1 : 0; maxLs = Math.max(maxLs, ls); }
  const half = Math.floor(n / 2), avg = (a) => a.reduce((s, x) => s + x, 0) / a.length;
  const gw = r.filter((x) => x > 0).reduce((a, x) => a + x, 0), gl = -r.filter((x) => x < 0).reduce((a, x) => a + x, 0);
  return { n, win: (r.filter((x) => x > 0.05).length / n) * 100, avgR: m, t: sd ? m / (sd / Math.sqrt(n)) : 0, sumR: eq, maxDD: dd, pf: gl ? gw / gl : null, maxLossStreak: maxLs, first: avg(r.slice(0, half)), second: avg(r.slice(half)) };
};

// ---- random entries with the same direction, trend, risk in ATR and exits: does the OTE logic beat luck?
function randomBaseline(data, trades, runs, trendOf = trendFn) {
  const prep = {};
  for (const sym of Object.keys(data)) {
    const { k15, k5 } = data[sym];
    const t5 = k5.map((k) => k[0]), o = k5.map((k) => +k[1]), h = k5.map((k) => +k[2]), l = k5.map((k) => +k[3]), c = k5.map((k) => +k[4]);
    const atr5 = atrOf(h, l, c), tr = trendOf(k15), mins = t5.map((t) => QT.et(t)), cand = { true: [], false: [] };
    for (let i = 60; i < k5.length - 100; i++) {
      const e = mins[i]; if (atr5[i] == null || e.dow === "Sat" || (e.dow === "Sun" && e.min < 1080) || (e.dow === "Fri" && e.min >= 1005) || (e.min >= 1005 && e.min < 1080)) continue;
      const up = tr(t5[i] + 5 * 60e3); if (up == null) continue; cand[up].push(i);
    }
    prep[sym] = { t5, o, h, l, c, atr5, mins, cand };
  }
  let seed = 12345; const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  const sim = (p, i, bull, rATR) => {
    const d = bull ? 1 : -1, entry = p.c[i], R = rATR * p.atr5[i], stop = entry - d * R, tgt = entry + d * 5 * R;
    let ih = null; for (let q = i - 6; q < i; q++) ih = ih == null ? (bull ? p.h[q] : p.l[q]) : bull ? Math.max(ih, p.h[q]) : Math.min(ih, p.l[q]);
    if (ih != null && (ih - entry) * d <= 0) ih = null;
    let s = stop;
    for (let k = i + 1; k < Math.min(p.c.length, i + 97); k++) {
      const m = p.mins[k].min, res = (px) => ((px - entry) * d) / R - (P0.cost * entry) / R;
      if (d > 0 ? p.l[k] <= s : p.h[k] >= s) return res(s);
      if (d > 0 ? p.h[k] >= tgt : p.l[k] <= tgt) return res(tgt);
      if (m >= 1015 && m < 1080) return res(p.c[k]);
      if (s === stop && ih != null && (d > 0 ? p.h[k] > ih : p.l[k] < ih)) s = entry;
    }
    return ((p.c[Math.min(p.c.length - 1, i + 96)] - entry) * d) / R;
  };
  const avgs = [];
  for (let r = 0; r < runs; r++) {
    let s = 0;
    for (const tq of trades) { const p = prep[tq.sym], arr = p.cand[tq.bull]; const i = arr[Math.floor(rnd() * arr.length)]; s += sim(p, i, tq.bull, tq.R / tq.atr); }
    avgs.push(s / trades.length);
  }
  return avgs.sort((a, b) => a - b);
}

// ---- Monte Carlo: same trades, shuffled order / resampled — what drawdown and losing streak to expect
function monteCarlo(r, runs = 10000) {
  let seed = 777; const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  const dds = [], sums = [], streaks = [];
  for (let k = 0; k < runs; k++) {
    let eq = 0, pk = 0, dd = 0, ls = 0, mls = 0;
    for (let i = 0; i < r.length; i++) { const x = r[Math.floor(rnd() * r.length)]; eq += x; pk = Math.max(pk, eq); dd = Math.min(dd, eq - pk); ls = x < -0.25 ? ls + 1 : 0; mls = Math.max(mls, ls); }
    dds.push(dd); sums.push(eq); streaks.push(mls);
  }
  const q = (a, p) => [...a].sort((x, y) => x - y)[Math.floor(p * (a.length - 1))];
  return { runs, pLoss: sums.filter((s) => s <= 0).length / runs, sum5: q(sums, 0.05), sum50: q(sums, 0.5), sum95: q(sums, 0.95), dd50: q(dds, 0.5), dd5: q(dds, 0.05), streak50: q(streaks, 0.5), streak95: q(streaks, 0.95),
    ddHist: dds };
}

module.exports = { oteRun, klines, bot, pick, stat, asV, P0, QT, randomBaseline, monteCarlo, trendFn, atrOf, VT, VB, key };
