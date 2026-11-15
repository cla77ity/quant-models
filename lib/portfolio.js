// Shared daily portfolio engine: Yahoo / FRED loaders (cached in data-cache/), returns + carry + volatility on one
// calendar, the weekly-rebalanced vol-targeted backtest (trend, carry, risk parity, SMA filters, fixed weights) and metrics.
// Step 9 — how an FX fund would trade a retail account: trend + carry on 9 USD pairs and gold, daily data 2004 → now.
//  TREND: per asset the average of sign(return over 1 / 3 / 12 months) (−1…+1), sized by inverse volatility.
//  CARRY: rank the 9 currencies by (3-month rate − USD rate); long the top 3, short the bottom 3 against USD.
//  COMBO: half of each. Every portfolio is scaled to 10% annual volatility (ex-ante, 120-day covariance), gross ≤ 6x,
//  rebalanced weekly. All fixed in advance; the sensitivity block shows how much the result moves when they move.
//  Retail reality: spread + commission per unit traded (1–5 bp by pair, gold 3 bp), a broker swap markup of 1%/yr on
//  every held position on top of the true rate differential (0% / 2% as variants), rates used with a 2-month lag.
//  Data: Yahoo daily closes (FX pairs, GC=F front-month gold futures), FRED OECD 3-month interbank rates.
//   (library — the FX trend + carry backtest itself is models/08-fx-trend-carry (удалена, есть в истории git))
const fs = require("fs");
const path = require("path");
const CACHE = path.join(__dirname, "..", "data-cache");
fs.mkdirSync(CACHE, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const ASSETS = [
  { key: "EUR", sym: "EURUSD=X", inv: false, rate: "IR3TIB01EZM156N", cost: 1.0 },
  { key: "GBP", sym: "GBPUSD=X", inv: false, rate: "IR3TIB01GBM156N", cost: 1.5 },
  { key: "AUD", sym: "AUDUSD=X", inv: false, rate: "IR3TIB01AUM156N", cost: 1.5 },
  { key: "NZD", sym: "NZDUSD=X", inv: false, rate: "IR3TIB01NZM156N", cost: 2.5 },
  { key: "JPY", sym: "JPY=X", inv: true, rate: "IR3TIB01JPM156N", cost: 1.2 },
  { key: "CAD", sym: "CAD=X", inv: true, rate: "IR3TIB01CAM156N", cost: 1.5 },
  { key: "CHF", sym: "CHF=X", inv: true, rate: "IR3TIB01CHM156N", cost: 2.0 },
  { key: "NOK", sym: "NOK=X", inv: true, rate: "IR3TIB01NOM156N", cost: 5.0 },
  { key: "SEK", sym: "SEK=X", inv: true, rate: "IR3TIB01SEM156N", cost: 5.0 },
  { key: "XAU", sym: "GC=F", inv: false, rate: null, cost: 3.0 },
];
const USD_RATE = "IR3TIB01USM156N";

// adj: dividend-adjusted closes (ETFs — total return), otherwise plain closes
const yahooDaily = async (sym, adj = false) => {
  const file = path.join(CACHE, `D_${sym.replace(/[^A-Z]/g, "")}${adj ? "_adj" : ""}.json`);
  if (fs.existsSync(file) && Date.now() - fs.statSync(file).mtimeMs < 24 * 3600e3) return JSON.parse(fs.readFileSync(file, "utf8"));
  const r = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?interval=1d&period1=0&period2=9999999999`, { headers: { "User-Agent": "Mozilla/5.0" } });
  const j = (await r.json()).chart.result[0], c = adj ? j.indicators.adjclose[0].adjclose : j.indicators.quote[0].close;
  const out = j.timestamp.map((t, i) => [new Date((t + 6 * 3600) * 1000).toISOString().slice(0, 10), c[i]]).filter((x) => x[1] != null && x[1] > 0);
  fs.writeFileSync(file, JSON.stringify(out)); await sleep(300);
  return out;
};
const fred = async (id) => {
  const file = path.join(CACHE, `F_${id}.csv`);
  if (!(fs.existsSync(file) && Date.now() - fs.statSync(file).mtimeMs < 24 * 3600e3)) fs.writeFileSync(file, await (await fetch(`https://fred.stlouisfed.org/graph/fredgraph.csv?id=${id}`)).text());
  const m = new Map();
  for (const line of fs.readFileSync(file, "utf8").trim().split("\n").slice(1)) { const [d, v] = line.split(","); if (v && v !== ".") m.set(d.slice(0, 7), +v); }
  return m;
};
// rate known on a given day: the monthly value two months back (publication lag), carried forward if missing
const rateOn = (m, date) => {
  let y = +date.slice(0, 4), mo = +date.slice(5, 7) - 2;
  for (let k = 0; k < 24; k++) { if (mo < 1) { mo += 12; y--; } const v = m.get(`${y}-${String(mo).padStart(2, "0")}`); if (v != null) return v; mo--; }
  return null;
};

function backtest(D, opts) {
  const P = { target: 0.1, rebal: 5, markup: 0.01, costMult: 1, gross: 6, horizons: [21, 63, 252], strategy: "combo", nCarry: 3, ...opts };
  const { dates, R, RL, carry, active, vol } = D, ASSETS = D.assets, N = ASSETS.length, T = dates.length;   // R: simple daily returns (P&L), RL: log (signals)
  let w = new Array(N).fill(0), eq = 1;
  const ret = new Array(T).fill(0), lev = new Array(T).fill(0), wHist = [];
  const cum = RL.map((r) => { const c = [0]; for (let t = 1; t < T; t++) c.push(c[t - 1] + r[t]); return c; });
  for (let t = 260; t < T; t++) {
    // P&L of yesterday's weights over today
    let r = 0;
    for (let i = 0; i < N; i++) if (w[i]) r += w[i] * (R[i][t] + carry[i][t]) - (Math.abs(w[i]) * (P.markup === 0 ? 0 : (ASSETS[i].markup ?? P.markup) * (P.markupMult || 1))) / 252;   // per-asset broker financing markup
    // rebalance at today's close
    if ((t - 260) % P.rebal === 0) {
      const raw = new Array(N).fill(0), act = [];
      for (let i = 0; i < N; i++) if (active[i][t] && vol[i][t] > 0) act.push(i);
      const sTrend = new Array(N).fill(0), sCarry = new Array(N).fill(0);
      for (const i of act) { let s = 0; for (const h of P.horizons) s += Math.sign(cum[i][t] - cum[i][t - h]); sTrend[i] = s / P.horizons.length; }
      const cur = act.filter((i) => ASSETS[i].rate && D.diff[i][t] != null).sort((a, b) => D.diff[b][t] - D.diff[a][t]);
      if (cur.length >= 2 * P.nCarry) { cur.slice(0, P.nCarry).forEach((i) => (sCarry[i] = 1)); cur.slice(-P.nCarry).forEach((i) => (sCarry[i] = -1)); }
      // single-asset index strategies (step 10): "long" always in, "sma" in while price > its N-day average,
      // "trendLO" the trend signal but never short; P.only limits the book to one asset key
      const sma = (i, n) => { let s = 0; for (let q = t - n + 1; q <= t; q++) s += Math.exp(cum[i][q]); return Math.exp(cum[i][t]) > s / n ? 1 : 0; };
      for (const i of act) {
        if (P.only && ASSETS[i].key !== P.only) continue;
        const s = P.strategy === "trend" ? sTrend[i] : P.strategy === "carry" ? sCarry[i] : P.strategy === "gold" ? (ASSETS[i].key === "XAU" ? 1 : 0)
          : P.strategy === "long" ? 1 : P.strategy === "sma" ? sma(i, P.smaN) : P.strategy === "trendLO" ? Math.max(0, sTrend[i])
          // step 11: risk parity over the non-FX assets (.rp), with an SMA200 switch, blended with trend, fixed weights
          : P.strategy === "rp" ? (ASSETS[i].rp ? 1 : 0) : P.strategy === "rpsma" ? (ASSETS[i].rp ? sma(i, 200) : 0)
          : P.strategy === "rptrend" ? 0.5 * (ASSETS[i].rp ? 1 : 0) + 0.5 * sTrend[i] : P.strategy === "weights" ? (P.weights[ASSETS[i].key] || 0)
          : 0.5 * sTrend[i] + 0.5 * sCarry[i];
        raw[i] = (P.fixed ? s : s / vol[i][t]) * (P.wts ? P.wts[ASSETS[i].key] ?? 1 : 1);   // P.wts: class-balance multipliers
      }
      // scale to the target with the 120-day covariance (ex-ante portfolio vol); P.fixed: 1x notional, no scaling
      let k = 0;
      if (P.fixed) k = 1;
      else if (P.strategy === "gold") k = 1 / Math.max(...raw.map(Math.abs));
      else {
        let s1 = 0, s2 = 0; const L = P.covL || 120;
        for (let q = t - L + 1; q <= t; q++) { let x = 0; for (const i of act) x += raw[i] * R[i][q]; s1 += x; s2 += x * x; }
        const pv = Math.sqrt(Math.max(0, s2 / L - (s1 / L) ** 2) * 252);
        k = pv > 0 ? P.target / pv : 0;
      }
      let nw = raw.map((x) => x * k), gross = nw.reduce((a, x) => a + Math.abs(x), 0);
      if (gross > P.gross) nw = nw.map((x) => (x * P.gross) / gross);
      if (P.band) for (let i = 0; i < N; i++) if (nw[i] !== 0 && w[i] !== 0 && Math.abs(nw[i] - w[i]) < P.band * Math.abs(w[i])) nw[i] = w[i];   // no-trade band: skip small resizes
      let tc = 0; for (let i = 0; i < N; i++) tc += Math.abs(nw[i] - w[i]) * ASSETS[i].cost * 1e-4 * P.costMult;
      r -= tc; w = nw;
      wHist.push([dates[t], ...w.map((x) => +x.toFixed(3))]);
    }
    ret[t] = r; lev[t] = w.reduce((a, x) => a + Math.abs(x), 0); eq *= 1 + r;
  }
  return { ret, lev, wHist };
}

const metrics = (dates, ret, from, to) => {
  const idx = []; for (let t = 0; t < dates.length; t++) if (dates[t] >= from && dates[t] < to && ret[t] !== undefined) idx.push(t);
  const r = idx.map((t) => ret[t]); if (r.length < 60) return null;
  const n = r.length, m = r.reduce((a, x) => a + x, 0) / n, sd = Math.sqrt(r.reduce((a, x) => a + (x - m) ** 2, 0) / (n - 1));
  let eq = 1, pk = 1, dd = 0; for (const x of r) { eq *= 1 + x; pk = Math.max(pk, eq); dd = Math.min(dd, eq / pk - 1); }
  const yrs = n / 252, cagr = eq ** (1 / yrs) - 1;
  return { cagr, vol: sd * Math.sqrt(252), sharpe: sd ? (m / sd) * Math.sqrt(252) : 0, maxDD: dd, calmar: dd ? cagr / -dd : null, total: eq - 1, tstat: sd ? (m / sd) * Math.sqrt(n) : 0 };
};

// daily returns, carry, volatility for a list of assets on a common weekday calendar (a.maxMove: bad-tick filter)
async function buildData(ASSETS, from = "2003-01-01") {
  const px = {}, rates = {};
  for (const a of ASSETS) { px[a.key] = new Map(await yahooDaily(a.sym, !!a.fund)); if (a.rate) rates[a.key] = await fred(a.rate); }
  rates.USD = await fred(USD_RATE);
  const dset = new Set(); for (const a of ASSETS) for (const d of px[a.key].keys()) { const wd = new Date(d + "T12:00:00Z").getUTCDay(); if (wd > 0 && wd < 6 && d >= from) dset.add(d); }
  const dates = [...dset].sort(), T = dates.length, N = ASSETS.length;
  const R = [], RL = [], carry = [], active = [], vol = [], diff = [], bad = {};
  for (let i = 0; i < N; i++) {
    const a = ASSETS[i], m = px[a.key], r = new Array(T).fill(0), c = new Array(T).fill(0), act = new Array(T).fill(false), v = new Array(T).fill(0), df = new Array(T).fill(null);
    let last = null, first = null; bad[a.key] = 0;
    for (let t = 0; t < T; t++) {
      const p = m.get(dates[t]);
      if (p != null) {
        if (last != null) { let x = Math.log(p / last) * (a.inv ? -1 : 1); if (Math.abs(x) > (a.maxMove || 0.15)) { x = 0; bad[a.key]++; } r[t] = x; }
        if (first == null) first = t;
        last = p;
      }
      if (first != null && t - first > 260) act[t] = true;
      if (a.rate) { const rf = rateOn(rates[a.key], dates[t]), ru = rateOn(rates.USD, dates[t]); if (rf != null && ru != null) { df[t] = rf - ru; c[t] = (rf - ru) / 100 / 252; } }
      // a CFD on a fund / index: total return of the ETF, the holder of a long pays the USD rate (a short receives it)
      if (a.fund) { const ru = rateOn(rates.USD, dates[t]); if (ru != null) c[t] = -ru / 100 / 252; }
      if (t >= 60) { let s2 = 0; for (let q = t - 59; q <= t; q++) s2 += r[q] * r[q]; v[t] = Math.sqrt((s2 / 60) * 252); }
    }
    // gold is a futures series: its returns already net of financing, no separate carry
    RL.push(r); R.push(r.map(Math.expm1)); carry.push(c); active.push(act); vol.push(v); diff.push(df);
  }
  return { dates, R, RL, carry, active, vol, diff, assets: ASSETS, bad };
}
module.exports = { ASSETS, buildData, backtest, metrics };

