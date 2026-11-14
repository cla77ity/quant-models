"""
qm.ict — ICT-движок (порт lib/ictengine.js = Pine-индикатор «ICT Quantum Probability Suite v21»).

Что он делает, по шагам, на каждой 5-минутной свече (время Нью-Йорка):
    1. Когда закрывается 15-минутная свеча — ищет новые зоны:
         RB  (rejection block) — свеча-экстремум с длинной тенью и повышенным объёмом;
         OB  (order block)     — последняя встречная свеча перед импульсом, который закрылся за ней;
         FVG (fair value gap)  — разрыв между свечами 1 и 3 при импульсной средней свече;
       и свинг-пивоты 5/5 → пулы ликвидности (SH / SL, равные хаи/лои EQH / EQL).
    2. Пулы ликвидности дня / недели / сессий: PDH/PDL, PWH/PWL, хай-лой Азии, Лондона, NY AM.
    3. Снятие пулов (sweep): цена прошла за уровень — пул удаляется и попадает в swept (список «снято на этой свече»).
    4. Зоны: касание края → (для trace) запись сделки; зона удаляется при пробое закрытием, «неудаче» (касание без
       реакции 1.5×ATR за 60 мин) или истечении срока.
    5. Ключевые открытия 00:00 / 09:30 / 10:00: «свип + реклейм» — цену выносят за уровень и она возвращается.
    6. hook(state) — стратегии (qm.setups) читают зоны / пулы / открытия после каждой свечи.

proxy=True — данные перпа QQQ/SPY как прокси NQ/ES: свечи вне часов CME (выходные, перерыв 17:00–18:00) пропускаются.
Статистика вероятностей и «план дня» из терминала здесь не нужны (на сделки не влияют) и не перенесены.
"""
from datetime import date as _date, timedelta

import numpy as np

from .intraday import ny

MIN, HR, DAY = 60_000, 3_600_000, 86_400_000
P = dict(rbL=3, wickATR=0.5, wickPct=35, volZ=0.5, volBase=20, rbMaxH=24, obDispATR=1.0, obVolZ=0.5, obMaxH=48,
         fvgMinATR=0.3, fvgMaxH=24, piv=5, poolMaxH=72, maxPools=60, reactATR=1.5, reactMin=60, koMinExcATR=0.15, koVolZ=0.3)
KO_T = {"00:00": "KO00", "09:30": "KO0930", "10:00": "KO1000"}


def sess_of(m):
    """Сессия по минуте дня (NY): AM 09:30–12:00, PM 13:00–16:00, остальное OTHER."""
    return "AM" if 570 <= m < 720 else "PM" if 780 <= m < 960 else "OTHER"


def atr14(h, l, c):
    """ATR по Уайлдеру за 14 свечей (первые 14 — простое среднее). None, пока не набралось 14 свечей."""
    n, out, a = len(h), [None] * len(h), 0.0
    for i in range(1, n):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        a = a + tr / 14 if i <= 14 else a + (tr - a) / 14
        if i >= 14:
            out[i] = a
    return out


def mean_sd(arr, i, length):
    """Среднее и стандартное отклонение `length` значений ДО индекса i (не включая i). None, если не хватает."""
    if i < length:
        return None
    s = s2 = 0.0
    for k in range(i - 1, i - length - 1, -1):       # тот же порядок сложения, что в JS
        x = float(arr[k])
        s += x
        s2 += x * x
    m = s / length
    return m, np.sqrt(max(0.0, s2 / length - m * m))


def vol_z(v, length=20):
    """Z-оценка объёма: насколько объём свечи выше среднего за предыдущие `length` свечей (в сигмах)."""
    # Суммы считаем напрямую по окну, от свечи i-1 назад (как JS). Накопленная сумма квадратов объёмов за всю историю
    # дошла бы до 1e15 и теряла точность — а z сравнивается с порогом, ошибка могла бы перевернуть сигнал.
    vl = v.tolist()
    z = np.zeros(len(vl))
    for i in range(length, len(vl)):
        s = s2 = 0.0
        for k in range(i - 1, i - length - 1, -1):
            x = vl[k]
            s += x
            s2 += x * x
        m = s / length
        sd = np.sqrt(max(0.0, s2 / length - m * m))
        z[i] = (vl[i] - m) / sd if sd > 0 else 0.0
    return z


def run(k15, k5, proxy=True, trace=None, hook=None):
    """Прогон движка. k15 / k5 — dict массивов t, o, h, l, c, v (qm.intraday.klines).
    trace — список, куда пишутся первые касания зон и реклеймы открытий (для бэктестов модели 02).
    hook(state) — вызывается после каждой 5m-свечи: state = dict(j, t, min, A, zones, pools, opens, swept)."""
    t15, o, h, l, c, v = k15["t"], k15["o"], k15["h"], k15["l"], k15["c"], k15["v"]
    n15 = len(t15)
    atr = atr14(h, l, c)
    z15 = vol_z(v, P["volBase"])
    min15 = ny(t15)[1]

    t5, o5, h5, l5, c5, v5 = k5["t"], k5["o"], k5["h"], k5["l"], k5["c"], k5["v"]
    z5 = vol_z(v5, 20)
    d5, m5, w5 = ny(t5)

    def closed(j):
        """Часы, когда фьючерсы CME не торгуются (перп при этом печатает свечи — их пропускаем)."""
        if not proxy:
            return False
        dw, mn = w5[j], m5[j]
        return dw == 5 or (dw == 6 and mn < 1080) or (dw == 4 and mn >= 1020) or (1020 <= mn < 1080)

    st = dict(atr=None, lastPh=None, lastPl=None, lastBslT=None, lastSslT=None)
    zones, pools = [], []
    tol = lambda: 0.03 * (st["atr"] or 1)

    def add_pool(price, kind, tag, start, now):
        """Новый пул ликвидности. Если рядом (в пределах 0.03 ATR) уже есть такой же — усиливаем его (равные хаи/лои)."""
        if price is None or not np.isfinite(price):
            return
        for p in pools:
            if p["kind"] == kind and abs(p["price"] - price) <= tol():
                p["touches"] += 1
                p["price"] = (p["price"] * (p["touches"] - 1) + price) / p["touches"]
                if tag in ("SH", "SL"):
                    if p["tag"] in ("SH", "SL", "EQH", "EQL"):
                        p["tag"] = "EQH" if kind == "BSL" else "EQL"
                else:
                    p["tag"] = tag
                return
        pools.append(dict(kind=kind, tag=tag, price=price, touches=1, start=start, created=now))
        if len(pools) > P["maxPools"]:                    # слишком много пулов — выкидываем самый «слабый»
            score = lambda p: p["touches"] * 10 + (now - p["created"]) / HR * 0.5
            wi = 0
            for i, p in enumerate(pools):
                if score(p) < score(pools[wi]):
                    wi = i
            pools.pop(wi)

    def dup_zone(kind, bull, ce):
        return any(z["kind"] == kind and z["bull"] == bull and abs(z["ce"] - ce) <= tol() * 5 for z in zones)

    def detect15(i, now):
        """Поиск зон на закрытии 15m-свечи i."""
        A = atr[i - 1]
        if A is None or i < 22:
            return
        st["atr"] = A
        hi_all, lo_all = h[i - P["rbL"]:i + 1].max(), l[i - P["rbL"]:i + 1].min()
        body_top, body_bot, rng = max(o[i], c[i]), min(o[i], c[i]), h[i] - l[i]
        wick_ok = lambda w: w >= P["wickATR"] * A and rng > 0 and w / rng * 100 >= P["wickPct"]
        m0, span = min15[i], 15 * MIN
        in_macro = m0 + 15 > 590 and m0 < 610
        swept_near = lambda s: s is not None and t15[i] - span <= s < t15[i] + 2 * span
        # RB: экстремум последних rbL свечей + длинная тень + объём выше среднего
        if h[i] >= hi_all and wick_ok(h[i] - body_top) and z15[i] >= P["volZ"] and not dup_zone("RB", False, (h[i] + body_top) / 2):
            zones.append(dict(kind="RB", bull=False, wickExt=h[i], bodyExt=body_top, ce=(h[i] + body_top) / 2, start=t15[i], created=now,
                              maxH=P["rbMaxH"], inMacro=in_macro, swept=swept_near(st["lastBslT"]), vz=z15[i]))
        if l[i] <= lo_all and wick_ok(body_bot - l[i]) and z15[i] >= P["volZ"] and not dup_zone("RB", True, (l[i] + body_bot) / 2):
            zones.append(dict(kind="RB", bull=True, wickExt=l[i], bodyExt=body_bot, ce=(l[i] + body_bot) / 2, start=t15[i], created=now,
                              maxH=P["rbMaxH"], inMacro=in_macro, swept=swept_near(st["lastSslT"]), vz=z15[i]))
        # OB: встречная свеча, после которой импульс закрылся за её экстремумом
        if c[i - 1] < o[i - 1] and c[i] > h[i - 1] and c[i] - l[i - 1] >= P["obDispATR"] * A and z15[i] >= P["obVolZ"] and not dup_zone("OB", True, (h[i - 1] + l[i - 1]) / 2):
            zones.append(dict(kind="OB", bull=True, wickExt=l[i - 1], bodyExt=h[i - 1], ce=(h[i - 1] + l[i - 1]) / 2, start=t15[i - 1], created=now, maxH=P["obMaxH"]))
        if c[i - 1] > o[i - 1] and c[i] < l[i - 1] and h[i - 1] - c[i] >= P["obDispATR"] * A and z15[i] >= P["obVolZ"] and not dup_zone("OB", False, (h[i - 1] + l[i - 1]) / 2):
            zones.append(dict(kind="OB", bull=False, wickExt=h[i - 1], bodyExt=l[i - 1], ce=(h[i - 1] + l[i - 1]) / 2, start=t15[i - 1], created=now, maxH=P["obMaxH"]))
        # FVG: разрыв между свечами i-2 и i, средняя свеча с объёмом выше среднего
        vb = mean_sd(v, i - 1, P["volBase"])
        vol_ok = vb is not None and v[i - 1] > vb[0]
        if vol_ok and h[i - 2] < l[i] and l[i] - h[i - 2] >= P["fvgMinATR"] * A:
            zones.append(dict(kind="FVG", bull=True, top=l[i], bottom=h[i - 2], ce=(l[i] + h[i - 2]) / 2, start=t15[i - 2], created=now, maxH=P["fvgMaxH"]))
        if vol_ok and l[i - 2] > h[i] and l[i - 2] - h[i] >= P["fvgMinATR"] * A:
            zones.append(dict(kind="FVG", bull=False, top=l[i - 2], bottom=h[i], ce=(l[i - 2] + h[i]) / 2, start=t15[i - 2], created=now, maxH=P["fvgMaxH"]))
        # свинг-пивот 5/5 (подтверждается через 5 свечей) → пул ликвидности
        p = i - P["piv"]
        if p >= P["piv"]:
            win_h, win_l = np.delete(h[p - P["piv"]:p + P["piv"] + 1], P["piv"]), np.delete(l[p - P["piv"]:p + P["piv"] + 1], P["piv"])
            if (win_h < h[p]).all() and st["lastPh"] != t15[p]:
                st["lastPh"] = t15[p]; add_pool(h[p], "BSL", "SH", t15[p], now)
            if (win_l > l[p]).all() and st["lastPl"] != t15[p]:
                st["lastPl"] = t15[p]; add_pool(l[p], "SSL", "SL", t15[p], now)

    def day_key(j):
        """Торговый день CME начинается в 18:00 NY: вечерние свечи относятся к следующей дате."""
        if proxy and m5[j] >= 1080:
            return (_date.fromisoformat(d5[j]) + timedelta(days=1)).isoformat()
        return d5[j]

    def week_key(dk):
        d = _date.fromisoformat(dk)
        return (d - timedelta(days=d.weekday())).isoformat()

    cur_day = dH = dL = cur_week = wH = wL = None
    sess = {"ASIA": dict(a=1200, b=1440, inside=False), "LDN": dict(a=120, b=300, inside=False), "AM": dict(a=570, b=720, inside=False)}
    opens = {"00:00": None, "09:30": None, "10:00": None}
    ko, mid_date, p15 = {}, None, 0

    for j in range(len(t5)):
        t = int(t5[j])
        while p15 < n15 and t15[p15] + 15 * MIN <= t:          # все 15m-свечи, закрывшиеся к этому моменту
            detect15(p15, int(t15[p15]) + 15 * MIN)
            p15 += 1
        if st["atr"] is None or closed(j):
            continue
        A, H5, L5, C5, mn = st["atr"], h5[j], l5[j], c5[j], int(m5[j])
        S, swept_now = sess_of(mn), []

        # ключевые открытия: новый день по Нью-Йорку — сброс
        if d5[j] != mid_date:
            mid_date = d5[j]
            opens = dict.fromkeys(opens)
            ko = {}
        if mn == 0: opens["00:00"] = o5[j]
        if mn == 570: opens["09:30"] = o5[j]
        if mn == 600: opens["10:00"] = o5[j]

        # пулы дня и недели
        dk = day_key(j)
        if dk != cur_day:
            if cur_day is not None and dH is not None:
                add_pool(dH, "BSL", "PDH", t, t); add_pool(dL, "SSL", "PDL", t, t)
            cur_day, dH, dL = dk, H5, L5
            wk = week_key(dk)
            if wk != cur_week:
                if cur_week is not None and wH is not None:
                    add_pool(wH, "BSL", "PWH", t, t); add_pool(wL, "SSL", "PWL", t, t)
                cur_week, wH, wL = wk, H5, L5
        dH, dL, wH, wL = max(dH, H5), min(dL, L5), max(wH, H5), min(wL, L5)
        # пулы сессий: при выходе из сессии её хай и лой становятся уровнями ликвидности
        for name, s in sess.items():
            ins = s["a"] <= mn < s["b"]
            if ins and not s["inside"]:
                s["hi"], s["lo"] = H5, L5
            elif ins:
                s["hi"], s["lo"] = max(s["hi"], H5), min(s["lo"], L5)
            if not ins and s["inside"]:
                add_pool(s["hi"], "BSL", "LDN H" if name == "LDN" else name + " H", t, t)
                add_pool(s["lo"], "SSL", "LDN L" if name == "LDN" else name + " L", t, t)
            s["inside"] = ins

        # снятие пулов: цена прошла за уровень (или пул устарел — 72 часа)
        for i in range(len(pools) - 1, -1, -1):
            p = pools[i]
            swept = H5 > p["price"] if p["kind"] == "BSL" else L5 < p["price"]
            if swept:
                st["lastBslT" if p["kind"] == "BSL" else "lastSslT"] = t
                swept_now.append(dict(kind=p["kind"], tag=p["tag"], price=p["price"]))
            if swept or t - p["created"] > P["poolMaxH"] * HR:
                pools.pop(i)

        # зоны: касание, реакция, пробой / неудача / истечение
        for i in range(len(zones) - 1, -1, -1):
            z = zones[i]
            if t < z["created"]:
                continue
            fvg = z["kind"] == "FVG"
            edge = (z["top"] if z["bull"] else z["bottom"]) if fvg else z["bodyExt"]
            if not z.get("touched") and (L5 <= edge if z["bull"] else H5 >= edge):
                z["touched"], z["touchT"] = True, t
                if trace is not None:
                    trace.append(dict(kind=z["kind"], bull=z["bull"], t=t, j=j, entry=edge, stop=((z["bottom"] if z["bull"] else z["top"]) if fvg else z["wickExt"]),
                                      ce=z["ce"], atr=A, sess=S, min=mn, inMacro=bool(z.get("inMacro")), swept=bool(z.get("swept")), vz=z.get("vz"), created=z["created"]))
            if z.get("touched"):
                z["mfe"] = max(z.get("mfe", 0.0), H5 - edge if z["bull"] else edge - L5)
                if not z.get("reacted") and z["mfe"] >= P["reactATR"] * A:
                    z["reacted"] = True
            invalid = ((C5 < z["bottom"]) if z["bull"] else (C5 > z["top"])) if fvg else ((C5 < z["wickExt"]) if z["bull"] else (C5 > z["wickExt"]))
            failed = z.get("touched") and not z.get("reacted") and t - z["touchT"] > P["reactMin"] * MIN
            expired = (not z.get("touched") and t - z["created"] > z["maxH"] * HR) or t - z["created"] > 3 * z["maxH"] * HR
            if invalid or failed or expired:
                zones.pop(i)

        # ключевые открытия: свип через уровень в одну сторону, затем реклейм обратно
        for name, px in opens.items():
            if px is None:
                continue
            s = ko.setdefault(name, dict(side=None, armed=False, dir=None, ext=None))
            cur = "above" if C5 > px else "below"
            if s["side"] is None:
                s["side"] = cur
            elif cur != s["side"]:
                depth = 0.0 if s["ext"] is None else abs(s["ext"] - px)
                z_ok, fire = z5[j] >= P["koVolZ"], None
                if cur == "above":
                    if s["armed"] and s["dir"] == "down" and depth >= P["koMinExcATR"] * A and z_ok:
                        fire, s["armed"] = "LONG", False
                    else:
                        s["armed"], s["dir"], s["ext"] = True, "up", H5
                else:
                    if s["armed"] and s["dir"] == "up" and depth >= P["koMinExcATR"] * A and z_ok:
                        fire, s["armed"] = "SHORT", False
                    else:
                        s["armed"], s["dir"], s["ext"] = True, "down", L5
                s["side"] = cur
                if fire and trace is not None:      # вход на закрытии свечи-реклейма, стоп 0.5 ATR за уровнем
                    trace.append(dict(kind=KO_T[name], bull=fire == "LONG", t=t, j=j, entry=C5, stop=px - 0.5 * A if fire == "LONG" else px + 0.5 * A,
                                      level=px, ext=s["ext"], atr=A, sess=S, min=mn, atClose=True))
            elif s["armed"]:
                s["ext"] = min(s["ext"], L5) if s["dir"] == "down" else max(s["ext"], H5)

        if hook:
            hook(dict(j=j, t=t, min=mn, A=A, zones=zones, pools=pools, opens=opens, swept=swept_now))
