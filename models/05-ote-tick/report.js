// Builds results/step6_report.html from results/step6_ote_tick.json + the cached 5m candles (run step6_ote_tick.js first).
//   node step6_report.js
const fs = require("fs");
const path = require("path");
const R = JSON.parse(fs.readFileSync(path.join(__dirname, "results", "step6_ote_tick.json"), "utf8"));
const k5 = { QQQ: JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "data-cache", "QQQ_5m.json"), "utf8")), SPY: JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "data-cache", "SPY_5m.json"), "utf8")) };
const NAME = { QQQ: "NQ", SPY: "ES" };
const etFmt = new Intl.DateTimeFormat("ru-RU", { timeZone: "America/New_York", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const ny = (t) => etFmt.format(t).replace(",", "");
const f = (x, d = 2) => (x == null || !Number.isFinite(x) ? "—" : (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(d));
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const main = R.rows.find((r) => r.name.startsWith("БОТ: по тренду"));

// ---------- equity curve
function equity(trades, W = 720, H = 220) {
  const pts = [0]; for (const t of trades) pts.push(pts[pts.length - 1] + t.res);
  const lo = Math.min(...pts, 0), hi = Math.max(...pts, 0), pad = 24;
  const x = (i) => pad + (i / (pts.length - 1)) * (W - 2 * pad), y = (v) => pad + ((hi - v) / (hi - lo || 1)) * (H - 2 * pad);
  let pk = 0; const dd = pts.map((v) => { pk = Math.max(pk, v); return v; });
  const line = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const ddArea = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(dd[i]).toFixed(1)}`).join("") + pts.slice().reverse().map((v, k) => `L${x(pts.length - 1 - k).toFixed(1)},${y(v).toFixed(1)}`).join("") + "Z";
  const ticks = [lo, 0, hi].map((v) => `<text x="4" y="${y(v) + 4}" class="ax">${f(v, 0)}R</text><line x1="${pad}" x2="${W - pad}" y1="${y(v)}" y2="${y(v)}" class="grid"/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Кривая капитала в R">${ticks}<path d="${ddArea}" class="dd"/><path d="${line}" class="eq"/></svg>`;
}
// ---------- histogram with a marker
function hist(values, marker, label, W = 720, H = 200, bins = 30) {
  const lo = Math.min(...values, marker ?? Infinity), hi = Math.max(...values, marker ?? -Infinity), bw = (hi - lo) / bins || 1, cnt = new Array(bins).fill(0);
  for (const v of values) cnt[Math.min(bins - 1, Math.floor((v - lo) / bw))]++;
  const mx = Math.max(...cnt), pad = 24, x = (v) => pad + ((v - lo) / (hi - lo || 1)) * (W - 2 * pad);
  const bars = cnt.map((c, i) => { const bh = (c / mx) * (H - 2 * pad - 14); return `<rect x="${(x(lo + i * bw) + 1).toFixed(1)}" y="${(H - pad - bh).toFixed(1)}" width="${Math.max(1, (W - 2 * pad) / bins - 2).toFixed(1)}" height="${bh.toFixed(1)}" class="bar"/>`; }).join("");
  const m = marker != null ? `<line x1="${x(marker)}" x2="${x(marker)}" y1="${pad - 6}" y2="${H - pad}" class="mk"/><text x="${Math.min(W - 150, x(marker) + 6)}" y="${pad + 4}" class="mkt">${esc(label)}</text>` : "";
  const ax = [lo, (lo + hi) / 2, hi].map((v) => `<text x="${x(v) - 14}" y="${H - 6}" class="ax">${f(v, 2)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img">${bars}${m}${ax}</svg>`;
}
// ---------- one trade on 5m candles with the fib grid
function tradeChart(tr, W = 680, H = 300) {
  const ks = k5[tr.sym], idx = (t) => { let lo = 0, hi = ks.length - 1; while (lo < hi) { const m = (lo + hi) >> 1; if (ks[m][0] < t) lo = m + 1; else hi = m; } return lo; };
  const a = Math.max(0, idx(tr.t) - 40), b = Math.min(ks.length - 1, idx(tr.exitT) + 10), seg = ks.slice(a, b + 1);
  const d = tr.bull ? 1 : -1, size = Math.abs(tr.legX - tr.legO), lvl = (q) => tr.legX - d * q * size;
  const prices = seg.flatMap((k) => [+k[2], +k[3]]).concat([tr.stop, tr.tgt, tr.legX, tr.legO]);
  const lo = Math.min(...prices), hi = Math.max(...prices), padL = 8, padR = 64, padY = 12;
  const y = (p) => padY + ((hi - p) / (hi - lo)) * (H - 2 * padY), cw = (W - padL - padR) / seg.length, x = (i) => padL + i * cw + cw / 2;
  const iF = idx(tr.fillT) - a, iE = idx(tr.exitT) - a;
  let s = `<rect x="${x(iF) - cw / 2}" y="${padY}" width="${Math.max(cw, x(iE) - x(iF) + cw)}" height="${H - 2 * padY}" class="inTrade"/>`;
  for (const [q, cls] of [[0, "fib0"], [0.5, "fibm"], [0.62, "fib"], [0.705, "fib"], [0.79, "fib"], [1, "fib0"]]) s += `<line x1="${padL}" x2="${W - padR}" y1="${y(lvl(q))}" y2="${y(lvl(q))}" class="${cls}"/><text x="${W - padR + 4}" y="${y(lvl(q)) + 3}" class="lv">${q}</text>`;
  seg.forEach((k, i) => { const o = +k[1], h = +k[2], l = +k[3], c = +k[4], up = c >= o; s += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(h)}" y2="${y(l)}" class="${up ? "wu" : "wd"}"/><rect x="${x(i) - cw * 0.35}" y="${y(Math.max(o, c))}" width="${cw * 0.7}" height="${Math.max(1, Math.abs(y(o) - y(c)))}" class="${up ? "cu" : "cd"}"/>`; });
  for (const [p, cls, t] of [[tr.entry, "en", "вход"], [tr.stop, "st", "стоп"], [tr.tgt, "tg", "5R"]]) s += `<line x1="${x(iF) - cw / 2}" x2="${x(iE) + cw / 2}" y1="${y(p)}" y2="${y(p)}" class="${cls}"/><text x="${x(iF)}" y="${y(p) - 3}" class="tl ${cls}t">${t}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" class="chart trade" role="img">${s}</svg>`;
}

const T = R.trades.slice().sort((a, b) => a.fillT - b.fillT);
const wins = T.filter((t) => t.state === "target").slice(0, 2), losses = T.filter((t) => t.state === "stop" && t.res < -0.5).slice(0, 2), bes = T.filter((t) => t.state === "stop" && Math.abs(t.res) < 0.25).slice(0, 1), times = T.filter((t) => t.state === "time").slice(0, 1);
const examples = [...wins, ...losses, ...bes, ...times];
const outcomeRu = (t) => (t.state === "target" ? "цель 5R" : t.state === "time" ? "выход по времени" : Math.abs(t.res) < 0.25 ? "безубыток" : "стоп");

const tbl = (rows) => `<table><thead><tr><th>Вариант</th><th>Сделок</th><th>Win</th><th>Средний R</th><th>t</th><th>PF</th><th>Макс. просадка</th><th>1-я / 2-я половина</th></tr></thead><tbody>${rows.map((r) => r.avgR == null ? `<tr><td>${esc(r.name)}</td><td>${r.n}</td><td colspan="6" class="muted">мало сделок</td></tr>` : `<tr><td>${esc(r.name)}</td><td>${r.n}</td><td>${r.win.toFixed(0)}%</td><td class="${r.avgR > 0 ? "pos" : "neg"}">${f(r.avgR)}R</td><td>${f(r.t, 1)}</td><td>${r.pf ? r.pf.toFixed(2) : "—"}</td><td>${r.maxDD.toFixed(1)}R</td><td>${f(r.first)} / ${f(r.second)}</td></tr>`).join("")}</tbody></table>`;
const sect = (name) => tbl(R.rows.filter((r) => r.sect === name));
const rnd = R.random, mc = R.mc;

const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OTE бэктест</title>
<style>
:root{--bg:#0d0d0f;--fg:#e8e8ea;--mut:#8a8a92;--line:#26262b;--card:#141417;--pos:#3ecf8e;--neg:#ff5d5d;--acc:#e8e8ea;--fib:#5b8def}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){--bg:#fafafa;--fg:#111;--mut:#666;--line:#e3e3e6;--card:#fff;--pos:#0f9d58;--neg:#d93025;--acc:#111;--fib:#2b63d9}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,Segoe UI,Inter,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 80px}h1{font-size:28px;margin:0 0 4px;letter-spacing:-.02em}h2{font-size:19px;margin:40px 0 10px}
.sub{color:var(--mut);margin:0 0 24px}.verdict{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--neg);padding:16px 18px;border-radius:10px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:18px 0}.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.kpi span{color:var(--mut);font-size:12.5px}
table{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums;display:block;overflow-x:auto}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal;min-width:220px}th{color:var(--mut);font-weight:500}.pos{color:var(--pos)}.neg{color:var(--neg)}.muted{color:var(--mut)}
.chart{width:100%;height:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}
.ax{fill:var(--mut);font-size:11px}.grid{stroke:var(--line)}.eq{fill:none;stroke:var(--acc);stroke-width:2}.dd{fill:var(--neg);opacity:.15}
.bar{fill:var(--mut);opacity:.6}.mk{stroke:var(--neg);stroke-width:2}.mkt{fill:var(--neg);font-size:12px}
.wu{stroke:var(--pos)}.wd{stroke:var(--neg)}.cu{fill:var(--pos)}.cd{fill:var(--neg)}.fib{stroke:var(--fib);stroke-dasharray:4 3;opacity:.8}.fibm{stroke:var(--mut);stroke-dasharray:2 4}.fib0{stroke:var(--mut);opacity:.6}
.lv{fill:var(--fib);font-size:11px}.inTrade{fill:var(--fg);opacity:.04}.en{stroke:var(--fg);stroke-width:1.5}.st{stroke:var(--neg);stroke-width:1.5}.tg{stroke:var(--pos);stroke-width:1.5}
.tl{font-size:11px}.ent{fill:var(--fg)}.stt{fill:var(--neg)}.tgt{fill:var(--pos)}
.ex{margin:22px 0}.ex h3{font-size:15px;margin:0 0 2px}.ex p{margin:0;color:var(--mut);font-size:13px}
.note{color:var(--mut);font-size:13.5px}ol li,ul li{margin:4px 0}code{background:var(--card);border:1px solid var(--line);padding:1px 5px;border-radius:5px;font-size:13px}
</style></head><body><main>
<h1>OTE «top / bottom tick»: бэктест</h1>
<p class="sub">NQ и ES (через перпы QQQ/SPY), 5m, ${R.period.join(" → ")}. Правила взяты из coach/ПРАВИЛА.md заранее и не подгонялись.</p>

<div class="verdict"><b>Вердикт: преимущества нет.</b> Бот по твоим правилам заработал ${f(main.avgR)}R на сделку за ${main.n} сделок. Это неотличимо от нуля (t = ${f(main.t, 1)}): случайные входы с тем же риском и выходами дают такой же или лучший результат в ${(rnd.pBeat * 100).toFixed(0)}% прогонов. Модель не убыточна, но и не доказана.</div>

<div class="kpis">
<div class="kpi"><b>${main.n}</b><span>сделок у бота</span></div>
<div class="kpi"><b>${main.win.toFixed(0)}%</b><span>прибыльных</span></div>
<div class="kpi"><b class="${main.avgR > 0 ? "pos" : "neg"}">${f(main.avgR)}R</b><span>в среднем на сделку</span></div>
<div class="kpi"><b>${main.pf.toFixed(2)}</b><span>profit factor</span></div>
<div class="kpi"><b class="neg">${main.maxDD.toFixed(1)}R</b><span>макс. просадка</span></div>
<div class="kpi"><b>${main.maxLossStreak}</b><span>стопов подряд (макс.)</span></div>
</div>

<h2>1. Кривая капитала бота</h2>
<p class="note">Сумма R по сделкам в порядке времени. Красным — просадка от пика. Первая половина в минусе (${f(main.first)}R на сделку), вторая в плюсе (${f(main.second)}R): результат держится на одном отрезке времени.</p>
${equity(T)}

<h2>2. Модель против случайности</h2>
<p class="note">${rnd.runs} раз брали случайные бары по тренду с тем же числом сделок, тем же стопом в ATR, целью 5R и безубытком. Серое — распределение среднего R случайных входов, красная линия — твоя модель. Модель стоит в середине распределения, то есть логика OTE пока ничего не добавляет к простому «войти по тренду с 5R».</p>
${hist(rnd.hist, main.avgR, `модель ${f(main.avgR)}R`)}

<h2>3. Монте-Карло: чего ждать на реальном счёте</h2>
<p class="note">10 000 раз перемешали и пересобрали те же ${main.n} сделок. Это показывает, насколько результат зависит от везения в порядке сделок.</p>
<div class="kpis">
<div class="kpi"><b>${(mc.pLoss * 100).toFixed(0)}%</b><span>шанс закончить в минусе</span></div>
<div class="kpi"><b class="neg">${mc.dd50.toFixed(1)}R</b><span>типичная просадка</span></div>
<div class="kpi"><b class="neg">${mc.dd5.toFixed(1)}R</b><span>просадка в худших 5%</span></div>
<div class="kpi"><b>${mc.streak95}</b><span>стопов подряд (95% случаев)</span></div>
</div>
${hist(R.mcDD, null, "")}
<p class="note">Распределение максимальной просадки в R. При риске 1% на сделку типичная просадка — около ${Math.abs(mc.dd50).toFixed(0)}% счёта, а на Apex с трейлинг-просадкой это означает слив аккаунта. Отсюда правило: риск на сделку должен выдерживать ${mc.streak95} стопов подряд.</p>

<h2>4. Разрезы</h2>
${sect("Модель")}
<h3>По инструменту</h3>${sect("По инструменту")}
<h3>Уровень входа</h3>${sect("Уровень входа")}
<h3>Совпадения</h3>${sect("Совпадения")}
<h3>Сессия</h3>${sect("Сессия (время установки ордера, NY)")}
<h3>Выход: цель × безубыток</h3>${sect("Выход (цель × безубыток)")}
<p class="note">Важно: если смотреть десятки разрезов, какие-то обязательно окажутся «зелёными» случайно. «Ключевое открытие» (+0.51R) и «Лондон» (+0.75R) — это 15–26 сделок, а первая и вторая половины у них противоположные. Это идеи для следующей проверки, а не найденный edge.</p>

<h2>5. Устойчивость: что если чуть поменять правила</h2>
<p class="note">У настоящего преимущества результат меняется плавно. Здесь он скачет: допуск 0.15×ATR даёт +0.03R, 0.25 — +0.40R, 0.4 — +0.12R. Скачки — признак шума. Выбрать «лучший» допуск после этого — классическая подгонка (overfitting).</p>
<table><thead><tr><th>Изменение</th><th>Сделок</th><th>Средний R</th><th>t</th><th>Макс. просадка</th></tr></thead><tbody>
${R.robust.map((r) => r.avgR == null ? `<tr><td>${esc(r.name)}</td><td colspan="4" class="muted">мало сделок</td></tr>` : `<tr><td>${esc(r.name)}</td><td>${r.n}</td><td class="${r.avgR > 0 ? "pos" : "neg"}">${f(r.avgR)}R</td><td>${f(r.t, 1)}</td><td>${r.maxDD.toFixed(1)}R</td></tr>`).join("")}
</tbody></table>

<h2>6. Примеры сделок</h2>
<p class="note">Синие пунктиры — 0.62 / 0.705 / 0.79 от волны (0 — хай, 1 — начало волны). Подсвечен участок, пока сделка открыта. Сверь, совпадает ли разметка с тем, как ты видишь сетап, — если нет, значит формализация расходится с твоим глазом, и это надо править в первую очередь.</p>
${examples.map((t) => `<div class="ex"><h3>${NAME[t.sym]} · ${t.bull ? "лонг" : "шорт"} · ${outcomeRu(t)} (${f(t.res)}R)</h3><p>ордер ${ny(t.t)} NY · вход ${t.how} на ${t.lv} · ${esc(t.conf.join(", "))}</p>${tradeChart(t)}</div>`).join("")}

<h2>Правила, как их понял бот</h2>
<ol>
<li>Bias: 15m цена выше SMA50 — ищем лонг, ниже — шорт.</li>
<li>Волна: свинг за последние 4 часа по тренду, не меньше 3×ATR (5m), фибо после первой встречной свечи. Новый экстремум — сетка перерисовывается, старый ордер снимается.</li>
<li>Обязательно: CE 15m RB / OB / FVG в той же стороне в пределах 0.15×ATR от уровня 0.62 / 0.705 / 0.79 и дисконт. Нужно минимум 3 совпадения: третье — второй CE, ключевое открытие 00:00 / 09:30 / 10:00 у входа или неснятые лои перед уровнем.</li>
<li>Вход: лимит на самом мелком из совпавших уровней; в 9:30–10:00 — только после касания и 5m-свечи, закрывшейся за предыдущей.</li>
<li>Стоп за началом волны + 0.1×ATR (если шире 3×ATR — за 0.79). Цель 5R, безубыток после снятия внутреннего хая.</li>
<li>Не больше 3 сделок в день, после 2 стопов подряд — стоп. Выход до 16:55 NY. Издержки CME.</li>
</ol>
<p class="note">Что бот не умеет и что может отличать его от тебя: SMT, AMD, std dev, PDH/PDL как цель, ATH-фильтр, «ощущение» чистоты волны. Данные: 7 месяцев 5m — мало для окончательного вывода. Запуск: <code>node step6_ote_tick.js</code>, затем <code>node step6_report.js</code>.</p>
</main></body></html>`;
fs.writeFileSync(path.join(__dirname, "results", "step6_report.html"), html);
console.log("results/step6_report.html");
