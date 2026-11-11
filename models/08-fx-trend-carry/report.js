// Builds results/step9_report.html from results/step9_fx_trend.json (run step9_fx_trend.js first).
//   node step9_report.js
const fs = require("fs");
const path = require("path");
const R = JSON.parse(fs.readFileSync(path.join(__dirname, "results", "step9_fx_trend.json"), "utf8"));
const pc = (x, d = 1) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "−") + Math.abs(x * 100).toFixed(d) + "%");
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const COL = { "Тренд": "var(--c1)", "Керри": "var(--c2)", "Тренд + керри": "var(--fg)", "Золото: купить и держать": "var(--c3)", "EUR/USD: купить и держать": "var(--mut)" };

function curves(keys, W = 760, H = 300) {
  const pad = { l: 46, r: 12, t: 12, b: 26 }, all = keys.flatMap((k) => R.curves[k].map((p) => Math.log(p[1])));
  const lo = Math.min(...all), hi = Math.max(...all), n = R.curves[keys[0]].length;
  const x = (i) => pad.l + (i / (n - 1)) * (W - pad.l - pad.r), y = (v) => pad.t + ((hi - v) / (hi - lo)) * (H - pad.t - pad.b);
  let s = "";
  for (const m of [0.25, 0.5, 1, 2, 4, 8]) { const v = Math.log(m); if (v < lo || v > hi) continue; s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="4" y="${y(v) + 4}" class="ax">${m}×</text>`; }
  const pts = R.curves[keys[0]];
  for (let i = 0; i < n; i++) if (pts[i][0].slice(5, 10) <= "01-07" && (i === 0 || pts[i - 1][0].slice(0, 4) !== pts[i][0].slice(0, 4)) && +pts[i][0].slice(0, 4) % 4 === 0) s += `<text x="${x(i) - 14}" y="${H - 6}" class="ax">${pts[i][0].slice(0, 4)}</text>`;
  for (const k of keys) s += `<path d="${R.curves[k].map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(Math.log(p[1])).toFixed(1)}`).join("")}" fill="none" stroke="${COL[k]}" stroke-width="${k === "Тренд + керри" ? 2.2 : 1.5}"/>`;
  const leg = keys.map((k) => `<span class="lg"><i style="background:${COL[k]}"></i>${esc(k)}</span>`).join("");
  return `<div class="legend">${leg}</div><svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Капитал, логарифмическая шкала">${s}</svg>`;
}
const K4 = ["Тренд", "Керри", "Тренд + керри", "Золото: купить и держать"];
const mainRows = Object.entries(R.main).map(([k, m]) => `<tr><td>${esc(k)}</td><td class="${m.cagr > 0 ? "pos" : "neg"}">${pc(m.cagr)}</td><td>${pc(m.vol)}</td><td>${m.sharpe.toFixed(2)}</td><td>${m.tstat.toFixed(1)}</td><td class="neg">${pc(m.maxDD)}</td><td>${pc(m.total, 0)}</td></tr>`).join("");
const yrs = Object.keys(R.years["Тренд"]);
const yearRows = yrs.map((y) => `<tr><td>${y}</td>${K4.map((k) => { const v = R.years[k][y]; return `<td class="${v > 0 ? "pos" : "neg"}">${pc(v)}</td>`; }).join("")}</tr>`).join("");
const crisisRows = Object.entries(R.crises).map(([nm, v]) => `<tr><td>${esc(nm)}</td>${K4.map((k) => `<td class="${v[k] > 0 ? "pos" : "neg"}">${pc(v[k])}</td>`).join("")}</tr>`).join("");
const sensRows = R.sens.map((s) => `<tr><td>${esc(s.name)}</td><td>${s.sharpe.toFixed(2)}</td><td class="${s.cagr > 0 ? "pos" : "neg"}">${pc(s.cagr)}</td><td class="neg">${pc(s.maxDD)}</td></tr>`).join("");
const m = R.main, combo = m["Тренд + керри"], H = R.halves;

const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FX тренд и керри</title>
<style>
:root{--bg:#0d0d0f;--fg:#e8e8ea;--mut:#8a8a92;--line:#26262b;--card:#141417;--pos:#3ecf8e;--neg:#ff5d5d;--c1:#5b8def;--c2:#e0a03a;--c3:#d4b84a}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#fafafa;--fg:#111;--mut:#777;--line:#e3e3e6;--card:#fff;--pos:#0f9d58;--neg:#d93025;--c1:#2b63d9;--c2:#c47a00;--c3:#a88a10}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,Segoe UI,Inter,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 80px}h1{font-size:28px;margin:0 0 4px;letter-spacing:-.02em}h2{font-size:19px;margin:40px 0 10px}
.sub{color:var(--mut);margin:0 0 24px}.verdict{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--neg);padding:16px 18px;border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums;display:block;overflow-x:auto}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal;min-width:200px}th{color:var(--mut);font-weight:500}.pos{color:var(--pos)}.neg{color:var(--neg)}
.chart{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:6px 0}.ax{fill:var(--mut);font-size:11px}.grid{stroke:var(--line)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--mut);margin-top:8px}.lg i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.note{color:var(--mut);font-size:13.5px}ul li{margin:4px 0}code{background:var(--card);border:1px solid var(--line);padding:1px 5px;border-radius:5px;font-size:13px}
</style></head><body><main>
<h1>Тренд и керри на форексе и золоте</h1>
<p class="sub">9 пар против доллара и золото, дневные данные ${R.start} → ${R.end}. Риск портфеля 10% годовых, ребаланс раз в неделю, розничные спреды и своп-наценка 1% в год.</p>
<div class="verdict"><b>Вердикт: на розничном счёте не работает.</b> «Тренд + керри» за 22 года дал ${pc(combo.cagr)} в год (Sharpe ${combo.sharpe.toFixed(2)}) с просадкой ${pc(combo.maxDD, 0)}. До 2015 года было слабо, но в плюс (Sharpe ${H["Тренд + керри"][0].sharpe.toFixed(2)}), после — минус (${H["Тренд + керри"][1].sharpe.toFixed(2)}). Даже в институциональных условиях без наценки — Sharpe всего ${R.sens[0].sharpe.toFixed(2)}.</div>

<h2>1. Капитал (логарифмическая шкала)</h2>
${curves([...K4, "EUR/USD: купить и держать"])}
<p class="note">Золото за этот период — лучший актив (${pc(m["Золото: купить и держать"].cagr)} в год), но это результат одного рынка в один удачный период, а не стратегия: заранее его было не выбрать.</p>

<h2>2. Итоги</h2>
<table><thead><tr><th>Стратегия</th><th>В год</th><th>Волатильность</th><th>Sharpe</th><th>t</th><th>Макс. просадка</th><th>Итого</th></tr></thead><tbody>${mainRows}</tbody></table>
<p class="note">Тренд и керри почти не коррелируют (${R.corr.toFixed(2)}), поэтому вместе просадка меньше, чем у каждой по отдельности. Это работающий принцип диверсификации, но складываются две стратегии без преимущества.</p>

<h2>3. Где съедаются деньги: своп-наценка</h2>
<p class="note">Портфель держит в среднем 2.5x плеча, поэтому наценка брокера 1% в год на каждую позицию стоит около 2.5% в год всему счёту. Это больше, чем весь доход стратегий.</p>
<table><thead><tr><th>Вариант «тренд + керри»</th><th>Sharpe</th><th>В год</th><th>Просадка</th></tr></thead><tbody>${sensRows}</tbody></table>
<p class="note">Без наценки по отдельности: керри Sharpe ${R.noMarkup.carry.sharpe.toFixed(2)} — в фонде с доступом к межбанковским ставкам это хоть что-то; тренд ${R.noMarkup.trend.sharpe.toFixed(2)} — не работает даже бесплатно.</p>

<h2>4. Кризисы: зачем фондам тренд</h2>
<table><thead><tr><th>Период</th>${K4.map((k) => `<th>${esc(k.split(":")[0])}</th>`).join("")}</tr></thead><tbody>${crisisRows}</tbody></table>
<p class="note">Тренд зарабатывает в больших кризисах (2008, 2022), когда остальное падает, — поэтому фонды держат его как страховку портфеля. Керри, наоборот, проваливается именно в кризисы. В спокойные годы тренд на валютах медленно теряет.</p>

<h2>5. По годам</h2>
<table><thead><tr><th>Год</th>${K4.map((k) => `<th>${esc(k.split(":")[0])}</th>`).join("")}</tr></thead><tbody>${yearRows}</tbody></table>

<h2>Что это значит</h2>
<ul>
<li>Валютный тренд в 2004–2014 был около нуля, а после 2015 — в минусе. Это известная история: доходность валютных CTA сильно упала в 2010-х.</li>
<li>Керри без наценки ещё даёт немного (Sharpe ${R.noMarkup.carry.sharpe.toFixed(2)}), но розничные свопы его съедают. Отсюда практическое правило: до запуска любой swing-стратегии на форексе сравни свопы брокера со ставками ЦБ.</li>
<li>Тренд в фонде — это не источник прибыли сам по себе, а страховка от кризисов в большом портфеле из десятков рынков (индексы, облигации, сырьё), где он работает лучше, чем на одних валютах.</li>
</ul>
<p class="note">Данные: Yahoo (дневные закрытия; GC=F — непрерывный фьючерс на золото), FRED (ставки OECD за 3 месяца, с задержкой 2 месяца; ставки евро и фунта последний раз обновлены в январе 2026 и дальше продлены). Запуск: <code>node step9_fx_trend.js</code>, затем <code>node step9_report.js</code>.</p>
</main></body></html>`;
fs.writeFileSync(path.join(__dirname, "results", "step9_report.html"), html);
console.log("results/step9_report.html");
