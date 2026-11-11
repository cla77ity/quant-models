// Step 6c — forward test (paper) of the OTE model and its two step-6 hypotheses, rules frozen on 2026-11-10.
// Every run re-reads NQ/ES (QQQ/SPY perps), runs the unchanged step-6 bot and appends trades placed after the freeze
// to results/forward_ote.json. A trade, once written, is never changed (no rewriting history). Summary: forward_ote.md.
//   H1: a key open (00:00 / 09:30 / 10:00) at the entry      (backtest +0.51R, n 26; stocks −0.12R, n 66)
//   H2: the order placed in London, 02:00–07:00 NY             (backtest +0.75R, n 15)
// Decision rule, fixed in advance: at ≥ 30 trades in a group — average ≥ +0.2R and t ≥ 2 → confirmed; average ≤ 0 →
// rejected; otherwise keep collecting. Until 30 trades nothing is concluded.
// Daily via Windows task "OTE_Forward_Journal".   node step6c_forward.js
const fs = require("fs");
const path = require("path");
const { oteRun, klines, bot, pick, stat, P0 } = require("../../lib/intraday.js");

const FREEZE = +process.env.OTE_TEST_FREEZE || Date.UTC(2026, 10, 11, 5, 0);   // 2026-11-11 00:00 New York: first moment that counts
const DIR = process.env.OTE_TEST_DIR || path.join(__dirname, "results");          // the env overrides are for a dry run only
const FILE = path.join(DIR, "forward_ote.json"), MD = path.join(DIR, "forward_ote.md"), LOG = path.join(DIR, "forward_log.txt");
const NAME = { QQQ: "NQ", SPY: "ES" };
const isLondon = (m) => m >= 120 && m < 420;

(async () => {
  const j = fs.existsSync(FILE) ? JSON.parse(fs.readFileSync(FILE, "utf8")) : { frozen: "2026-11-10", rules: P0, trades: [] };
  const have = new Set(j.trades.map((t) => t.id));
  const all = [];
  for (const sym of ["QQQ", "SPY"]) {
    const k15 = await klines(sym, "15m"), k5 = await klines(sym, "5m");
    if (!k5.length) throw new Error(`нет данных ${sym}`);
    all.push(...oteRun(k15, k5, {}).map((s) => ({ ...s, sym })));
  }
  const fresh = bot(pick(all)).filter((s) => s.t >= FREEZE);
  let added = 0;
  for (const s of fresh) {
    const id = `${s.sym}|${s.t}|${s.bull ? "L" : "S"}`;
    if (have.has(id)) continue;
    j.trades.push({ id, sym: NAME[s.sym], dir: s.bull ? "лонг" : "шорт", placed: new Date(s.t).toISOString(), filled: new Date(s.fillT).toISOString(), exit: new Date(s.exitT).toISOString(),
      entry: +s.entry.toFixed(2), stop: +s.stop.toFixed(2), level: s.lv, how: s.how, conf: s.conf, res: +s.res.toFixed(3), outcome: s.state,
      H1: s.kinds.includes("open"), H2: isLondon(s.min), recorded: new Date().toISOString() });
    added++;
  }
  j.trades.sort((a, b) => a.filled.localeCompare(b.filled));
  j.updated = new Date().toISOString();
  fs.writeFileSync(FILE, JSON.stringify(j, null, 1));

  const f = (x, d = 2) => (x == null ? "—" : (x >= 0 ? "+" : "") + x.toFixed(d));
  const verdict = (st, n) => n < 30 ? `собираем (${n}/30)` : st.avgR >= 0.2 && st.t >= 2 ? "ПОДТВЕРЖДЕНА" : st.avgR <= 0 ? "ОТВЕРГНУТА" : "неясно — продолжаем";
  const groups = [["Вся модель (бот)", j.trades], ["H1: ключевое открытие у входа", j.trades.filter((t) => t.H1)], ["H2: ордер в Лондон (02:00–07:00 NY)", j.trades.filter((t) => t.H2)]];
  let md = `# Проверка вперёд: OTE top/bottom tick\n\nПравила заморожены 2026-11-10 (models/05-ote-tick). Считаются только сделки, выставленные с 2026-11-11 00:00 NY.\nОбновлено: ${j.updated.slice(0, 16).replace("T", " ")} UTC.\n\n`;
  md += `Решение (задано заранее): от 30 сделок в группе — средний ≥ +0.2R и t ≥ 2 → подтверждена; средний ≤ 0 → отвергнута.\n\n| Группа | Сделок | Win | Средний R | Сумма R | t | Статус |\n|---|---|---|---|---|---|---|\n`;
  const lines = [];
  for (const [name, list] of groups) {
    const r = list.map((t) => t.res), n = r.length, st = n >= 8 ? stat(r) : null, sum = r.reduce((a, x) => a + x, 0), avg = n ? sum / n : null, win = n ? (r.filter((x) => x > 0.05).length / n) * 100 : null;
    md += `| ${name} | ${n} | ${win == null ? "—" : win.toFixed(0) + "%"} | ${f(avg)} | ${f(sum, 1)} | ${st ? f(st.t, 1) : "—"} | ${verdict(st || { avgR: avg ?? 0, t: 0 }, n)} |\n`;
    lines.push(`${name}: n ${n}, ${f(avg)}R`);
  }
  md += `\n## Сделки\n\n| Заполнено (UTC) | Инстр. | Напр. | Уровень | Вход | Итог | R | H1 | H2 |\n|---|---|---|---|---|---|---|---|---|\n`;
  for (const t of j.trades) md += `| ${t.filled.slice(0, 16).replace("T", " ")} | ${t.sym} | ${t.dir} | ${t.level} | ${t.how} | ${t.outcome} | ${f(t.res)} | ${t.H1 ? "✓" : ""} | ${t.H2 ? "✓" : ""} |\n`;
  fs.writeFileSync(MD, md);
  const line = `${new Date().toISOString()}  +${added} новых, всего ${j.trades.length}. ${lines.join("; ")}`;
  fs.appendFileSync(LOG, line + "\n");
  console.log(line);
})().catch((e) => { fs.appendFileSync(LOG, `${new Date().toISOString()}  ОШИБКА: ${e.message}\n`); console.error(e); process.exit(1); });
