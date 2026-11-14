"""
qm.data — загрузка данных с кэшем.

    yahoo_daily("SPY", adj=True)  → pandas Series цен закрытия по датам (adj=True — с учётом дивидендов)
    fred("IR3TIB01USM156N")       → Series месячной ставки в процентах
    rate_on(series, "2026-11-10") → ставка, известная на эту дату (с задержкой публикации 2 месяца)

Кэш — папка data-cache/ в корне репозитория, формат общий с JS-версией (файлы D_*.json, F_*.csv).
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parent.parent / "data-cache"
CACHE.mkdir(exist_ok=True)
MAX_AGE = float(os.environ.get("QM_CACHE_HOURS", 24)) * 3600   # QM_CACHE_HOURS=100000 — работать только по кэшу


def _fresh(path):
    return path.exists() and time.time() - path.stat().st_mtime < MAX_AGE


def _curl(url):
    """Скачать через системный curl (есть в Windows 10+). FRED не отвечает на запросы из Python urllib,
    но спокойно отдаёт данные curl'у — поэтому для него идём сразу сюда."""
    exe = shutil.which("curl") or r"C:\Windows\System32\curl.exe"
    res = subprocess.run([exe, "-s", "-f", "-m", "60", "-A", "Mozilla/5.0", url], capture_output=True, timeout=90)
    if res.returncode != 0:
        raise OSError(f"curl код {res.returncode}")
    return res.stdout.decode("utf-8")


def _node(url):
    """Скачать через Node.js fetch. FRED отвечает Node, но часто «подвешивает» Python urllib и curl
    (фильтр по отпечатку соединения), поэтому для FRED сначала пробуем Node, если он установлен."""
    exe = shutil.which("node")
    if not exe:
        raise OSError("node не установлен")
    js = ("fetch(process.argv[1],{headers:{'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'}})"
          ".then(r=>{if(!r.ok)process.exit(2);return r.text()}).then(t=>process.stdout.write(t)).catch(()=>process.exit(3))")
    res = subprocess.run([exe, "-e", js, url], capture_output=True, timeout=90)
    if res.returncode != 0:
        raise OSError(f"node код {res.returncode}")
    return res.stdout.decode("utf-8")


def _get(url, tries=3):
    """Скачать текст по URL: 3 попытки с паузой; если сеть не отвечает — исключение (вызывающий возьмёт старый кэш)."""
    if "stlouisfed.org" in url:
        for fetcher in (_node, _curl):
            try:
                return fetcher(url)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise OSError("FRED не ответил ни через node, ни через curl")
    for k in range(tries):
        try:
            # полный заголовок браузера: FRED «подвешивает» запросы с коротким User-Agent
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except OSError:
            if k == tries - 1:
                return _curl(url)                     # последняя попытка — через curl
            time.sleep(2 * (k + 1))


def _refresh(path, fetch):
    """Обновить кэш-файл, если он устарел. Нет сети, но есть старый файл — работаем по нему (с предупреждением)."""
    if _fresh(path):
        return
    try:
        fetch()
    except OSError as e:
        if not path.exists():
            raise
        print(f"  (нет сети для {path.name}: {e.__class__.__name__} — беру кэш)")


def yahoo_daily(sym, adj=False):
    """Дневные закрытия с Yahoo за всю историю. Дата — по Нью-Йорку (сдвиг +6 часов, как в JS-версии)."""
    name = "".join(ch for ch in sym if ch.isupper())
    path = CACHE / f"D_{name}{'_adj' if adj else ''}.json"
    def fetch():
        j = json.loads(_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&period1=0&period2=9999999999"))["chart"]["result"][0]
        closes = j["indicators"]["adjclose"][0]["adjclose"] if adj else j["indicators"]["quote"][0]["close"]
        rows = [[time.strftime("%Y-%m-%d", time.gmtime(t + 6 * 3600)), c] for t, c in zip(j["timestamp"], closes) if c is not None and c > 0]
        path.write_text(json.dumps(rows))
        time.sleep(0.3)
    _refresh(path, fetch)
    rows = json.loads(path.read_text())
    s = pd.Series({d: p for d, p in rows}, dtype=float)   # при повторе даты остаётся последняя, как в JS (Map)
    return s.sort_index()


def fred(series_id):
    """Месячный ряд FRED (без ключа API). Ключ — 'YYYY-MM', значение — ставка в процентах."""
    path = CACHE / f"F_{series_id}.csv"
    _refresh(path, lambda: path.write_text(_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}")))
    out = {}
    for line in path.read_text().strip().split("\n")[1:]:
        d, v = line.split(",")[:2]
        if v and v != ".":
            out[d[:7]] = float(v)
    return out


def rate_on(monthly, date):
    """Ставка, которую реально можно было знать в день `date`: значение за месяц на 2 месяца раньше
    (статистика публикуется с задержкой). Если его нет — ищем ещё раньше, до 24 месяцев назад.
    Это защита от «заглядывания в будущее» (lookahead bias)."""
    y, m = int(date[:4]), int(date[5:7]) - 2
    for _ in range(24):
        if m < 1:
            m += 12
            y -= 1
        v = monthly.get(f"{y}-{m:02d}")
        if v is not None:
            return v
        m -= 1
    return None
