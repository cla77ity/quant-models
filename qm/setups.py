"""
qm.setups — внутридневные ICT-стратегии поверх qm.ict (порт lib/rbfib.js и OTE-модели из lib/intraday.js).

    RBFib   — модель 03: rejection block 5m + фибо OTE на импульсе (вход на открытии RB, 5R)
    RBVid   — модель 04: сначала волна и фибо, потом RB в откате (вход на начале RB, безубыток после внутр. хая)
    OTE     — модели 05 / 06: CE PD-массива на уровне OTE (или просто уровень OTE + тренд)

Как устроено: каждая стратегия — объект с методом on_bar(state), который ICT-движок вызывает после каждой 5m-свечи.
drive(k15, k5, [стратегии]) прогоняет движок ОДИН раз и кормит все стратегии сразу (быстро: десятки вариантов за проход).

Жизненный цикл сделки: сетап → лимитный ордер → исполнение → выход (стоп / цель / по времени / безубыток).
У каждой сделки сразу считаются несколько вариантов выхода (v): цели 2R / 3R / 5R / ... × правила безубытка —
так одна и та же точка входа сравнивается с разными выходами честно.

Правила аккаунта (risk_rules): не больше 3 сделок в торговый день CME, после 2 убытков подряд — стоп до конца дня,
одна позиция на инструмент. Только сделки по тренду (15m: цена выше SMA50 — лонги, ниже — шорты).
"""
import numpy as np

from . import ict
from .intraday import Lcg, ny

MIN5 = 5 * 60_000


def atr_wilder(h, l, c):
    """ATR14 по Уайлдеру на 5m (None, пока нет 14 свечей)."""
    return ict.atr14(h, l, c)


def trend_fn(k15):
    """Тренд на момент t: последняя ЗАКРЫТАЯ 15m-свеча выше своей SMA50 → True (лонги), ниже → False, мало данных → None."""
    t15, c15 = k15["t"], k15["c"].tolist()
    closes_at = t15 + 15 * 60_000

    def at(t):
        i = int(np.searchsorted(closes_at, t, side="right")) - 1
        if i < 50:
            return None
        # Среднюю считаем сложением 50 чисел по порядку (как JS), а не через накопленную сумму всей истории:
        # когда цена ровно равна средней, ошибка округления в 10-м знаке иначе переворачивает сигнал.
        s = 0.0
        for x in c15[i - 49:i + 1]:
            s += x
        return c15[i] > s / 50
    return at


class _Base:
    """Общая часть: данные 5m, ATR, тренд, завершение вариантов выхода."""
    def __init__(self, k15, k5, P):
        self.P = P
        self.t5, self.o, self.h, self.l, self.c = k5["t"], k5["o"], k5["h"], k5["l"], k5["c"]
        self.atr5 = atr_wilder(self.h, self.l, self.c)
        self.trend_at = P["trendOf"](k15) if P.get("trendOf") else trend_fn(k15)
        self.out, self.live = [], []

    def times(self, mn):
        """flat — пора закрывать всё (16:55 NY для фьючерсов / 15:55 для акций); no_fill — новые входы запрещены."""
        rth = self.P.get("rth")
        flat = (mn >= 955 or mn < 570) if rth else (1015 <= mn < 1080)
        return flat, rth

    def fin(self, cd, v, how, px, k):
        """Закрыть вариант выхода: результат в R за вычетом издержек."""
        d = 1 if cd["bull"] else -1
        v.update(done=True, how=how, exitT=int(self.t5[k]), exitPx=px, res=(px - cd["entry"]) * d / cd["R"] - self.P["cost"] * cd["entry"] / cd["R"])

    def hit(self, cd, j, px, below_for_long=True):
        """Задела ли свеча j уровень px со стороны стопа (для лонга — low ≤ px)."""
        return self.l[j] <= px if (cd["bull"] == below_for_long) else self.h[j] >= px


def _context(st, bull, recent, x, wlo, whi, keys):
    """Контекст RB: снятая ликвидность за последние свечи, OB/FVG 15m под тенью, ключевое открытие в тени."""
    sw = [p for r in recent for p in r["swept"] if p["kind"] == ("SSL" if bull else "BSL")]
    pda = None
    for z in x["zones"]:
        if z["kind"] in ("OB", "FVG") and z["bull"] == bull and z["created"] <= x["t"]:
            zt = z["top"] if z["kind"] == "FVG" else max(z["wickExt"], z["bodyExt"])
            zb = z["bottom"] if z["kind"] == "FVG" else min(z["wickExt"], z["bodyExt"])
            if wlo <= zt and whi >= zb:
                pda = z
                break
    ko = next((nm for nm in keys if x["opens"][nm] is not None and wlo <= x["opens"][nm] <= whi), None)
    if sw:
        return f"свип {sw[0]['tag']}", "sweep"
    if pda:
        return f"{pda['kind']} 15m", "pda"
    if ko:
        return f"открытие {ko}", "open"
    return None, None


# ====================================================================== модель 03: RB + фибо
class RBFib(_Base):
    P0 = dict(wickPct=0.5, wickATR=0.5, impATR=2.0, fibLo=0.62, fibHi=0.79, stopBuf=0.1, T=5, wait=48, hold=96, ctxBars=3, cost=0.00004, rth=False, entryAt="open")
    TS = [2, 3, 5, "pool"]

    def __init__(self, k15, k5, **opts):
        super().__init__(k15, k5, {**self.P0, **opts})
        self.recent = []

    def sync(self, cd):
        v = cd["v"].get(self.P["T"])
        if v is None:
            return
        if v["done"]:
            cd.update(state=v["how"], res=v["res"], exitT=v["exitT"], exitPx=v["exitPx"], target=v["tgt"])
        else:
            cd["state"] = "open"

    def on_bar(self, x):
        P, j, mn = self.P, x["j"], x["min"]
        o, h, l, c, t5 = self.o, self.h, self.l, self.c, self.t5
        self.recent.append(dict(j=j, swept=x["swept"]))
        while self.recent and self.recent[0]["j"] <= j - P["ctxBars"]:
            self.recent.pop(0)
        rth = P["rth"]
        flat = (mn >= 955 or mn < 570) if rth else (1015 <= mn < 1080)
        no_fill = (1005 <= mn < 1080) or (rth and not (570 <= mn < 955))

        for cd in self.live:
            d = 1 if cd["bull"] else -1
            if cd["state"] in ("watch", "limit"):
                if not rth and flat:
                    cd["state"] = "expired" if cd["armedOnce"] else "dead"; continue
                if cd["state"] == "limit" and not no_fill and (l[j] <= cd["entry"] if d > 0 else h[j] >= cd["entry"]):
                    cd.update(fillJ=j, fillT=int(t5[j]))
                    R = cd["R"]
                    pool = sorted([p["price"] for p in x["pools"] if p["kind"] == ("BSL" if d > 0 else "SSL") and (p["price"] - cd["entry"]) * d >= 2 * R], key=lambda v: v * d)
                    cd["v"] = {T: dict(tgt=(pool[0] if pool else None) if T == "pool" else cd["entry"] + d * T * R, done=False) for T in self.TS}
                    if l[j] <= cd["stop"] if d > 0 else h[j] >= cd["stop"]:
                        for v in cd["v"].values():
                            self.fin(cd, v, "stop", cd["stop"], j)
                    self.sync(cd); continue
                if l[j] <= cd["stop"] if d > 0 else h[j] >= cd["stop"]:
                    cd["state"] = "cancel"; continue
                if j - cd["j0"] >= P["wait"]:
                    cd["state"] = "expired" if (cd["state"] == "limit" or cd["armedOnce"]) else "dead"; continue
                cd["ext"] = max(cd["ext"], h[j]) if d > 0 else min(cd["ext"], l[j])
                imp = abs(cd["ext"] - cd["anchor"])
                f = abs(cd["ext"] - cd["entry"]) / imp if imp > 0 else 0.0
                armed = imp >= P["impATR"] * cd["atr"] and P["fibLo"] <= f <= P["fibHi"]   # ордер живёт, пока RB в зоне OTE
                if armed and not cd["armedOnce"]:
                    cd["armedOnce"] = True; self.out.append(cd)
                cd["state"] = "limit" if armed else "watch"
            elif cd["state"] == "open":
                for v in cd["v"].values():
                    if v["done"]:
                        continue
                    if l[j] <= cd["stop"] if d > 0 else h[j] >= cd["stop"]:
                        self.fin(cd, v, "stop", cd["stop"], j)
                    elif v["tgt"] is not None and (h[j] >= v["tgt"] if d > 0 else l[j] <= v["tgt"]):
                        self.fin(cd, v, "target", v["tgt"], j)
                    elif flat or j - cd["fillJ"] >= P["hold"]:
                        self.fin(cd, v, "time", c[j], j)
                self.sync(cd)
        self.live = [cd for cd in self.live if cd["state"] in ("watch", "limit", "open")]

        # новый rejection block на свече j
        A5 = self.atr5[j]
        rng = h[j] - l[j]
        if A5 is None or not rng > 0:
            return
        body_top, body_bot = max(o[j], c[j]), min(o[j], c[j])
        for bull in (True, False):
            wick = body_bot - l[j] if bull else h[j] - body_top
            if not (c[j] > o[j] if bull else c[j] < o[j]) or wick < P["wickPct"] * rng or wick < P["wickATR"] * A5:
                continue
            ext = l[j] if bull else h[j]
            wlo, whi = (l[j], body_bot) if bull else (body_top, h[j])
            ctx, kind = _context(self, bull, self.recent, x, wlo, whi, ("00:00", "10:00"))
            if not ctx:
                continue
            d = 1 if bull else -1
            entry = (ext + o[j]) / 2 if P["entryAt"] == "ce" else o[j]
            stop = ext - d * P["stopBuf"] * A5
            R = abs(entry - stop)
            if not R > 0:
                continue
            self.live.append(dict(setup="RBF", bull=bull, t=int(t5[j]), j0=j, entry=entry, stop=stop, R=R, anchor=ext, ext=h[j] if bull else l[j], atr=A5,
                                  ctx=ctx, ctxKind=kind, trend=self.trend_at(int(t5[j]) + MIN5) == bull, state="watch", armedOnce=False, v={}))


# ====================================================================== модель 04: RB по видео
class RBVid(_Base):
    P0 = dict(wickPct=0.5, wickATR=0.5, legBars=48, legATR=3, oteLo=0.62, discLo=0.5, confirmBars=2, wait=3, ihBars=6, stopBuf=0.1, hold=96,
              cost=0.00004, rth=False, T=5, be="ih", zone="ote")
    VT, VB = [2, 3, 5, "leg"], ["none", "1R", "ih"]

    def __init__(self, k15, k5, **opts):
        super().__init__(k15, k5, {**self.P0, **opts})
        self.recent, self.pend = [], []
        self.PK = f"{self.P['T']}|{self.P['be']}"

    def leg_at(self, j, bull):
        """Волна до свечи j: экстремум за legBars свечей, её начало и была ли встречная свеча после экстремума."""
        P, h, l, o, c = self.P, self.h, self.l, self.o, self.c
        a = max(0, j - P["legBars"])
        seg = h[a:j] if bull else -l[a:j]
        if len(seg) == 0:
            return None
        iX = a + int(np.argmax(seg))                      # первый максимум (как строгое «>» в JS)
        if iX <= a:
            return None
        seg_o = l[a:iX + 1] if bull else -h[a:iX + 1]
        iO = a + int(np.argmin(seg_o))
        X, O = (h[iX], l[iO]) if bull else (l[iX], h[iO])
        q = np.arange(iX + 1, j + 1)
        counter = bool(((c[q] < o[q]) if bull else (c[q] > o[q])).any())
        return dict(X=X, O=O, iX=iX, iO=iO, size=abs(X - O), counter=counter)

    def sync(self, cd):
        v = cd["v"].get(self.PK)
        if v is None:
            return
        if v["done"]:
            cd.update(state="stop" if v["how"] == "be" else v["how"], be=v["how"] == "be", res=v["res"], exitT=v["exitT"], exitPx=v["exitPx"], target=v["tgt"])
        else:
            cd["state"] = "open"

    def arm(self, p, k):
        R = abs(p["entry"] - p["stop"])
        if not R > 0:
            return
        cd = dict(p, R=R, armJ=k, state="limit", v={})
        self.live.append(cd); self.out.append(cd)

    def on_bar(self, x):
        P, j, mn = self.P, x["j"], x["min"]
        o, h, l, c, t5 = self.o, self.h, self.l, self.c, self.t5
        self.recent.append(dict(j=j, swept=x["swept"]))
        while self.recent and self.recent[0]["j"] <= j - 3:
            self.recent.pop(0)
        rth = P["rth"]
        flat = (mn >= 955 or mn < 570) if rth else (1015 <= mn < 1080)
        no_fill = (1005 <= mn < 1080) or (rth and not (570 <= mn < 955))

        for cd in self.live:
            d = 1 if cd["bull"] else -1
            if cd["state"] == "limit":
                if not rth and flat:
                    cd["state"] = "expired"; continue
                if not no_fill and (l[j] <= cd["entry"] if d > 0 else h[j] >= cd["entry"]):
                    cd.update(fillJ=j, fillT=int(t5[j]))
                    for T in self.VT:
                        for be in self.VB:
                            tgt = (cd["legX"] if (cd["legX"] - cd["entry"]) * d >= 1.5 * cd["R"] else None) if T == "leg" else cd["entry"] + d * T * cd["R"]
                            cd["v"][f"{T}|{be}"] = dict(tgt=tgt, stop=cd["stop"], be=be, done=tgt is None)
                    if l[j] <= cd["stop"] if d > 0 else h[j] >= cd["stop"]:
                        for v in cd["v"].values():
                            if not v["done"]:
                                self.fin(cd, v, "stop", cd["stop"], j)
                    self.sync(cd); continue
                if l[j] <= cd["stop"] if d > 0 else h[j] >= cd["stop"]:
                    cd["state"] = "cancel"; continue
                if j - cd["armJ"] >= P["wait"]:
                    cd["state"] = "expired"; continue
            elif cd["state"] == "open":
                for v in cd["v"].values():
                    if v["done"]:
                        continue
                    if l[j] <= v["stop"] if d > 0 else h[j] >= v["stop"]:
                        self.fin(cd, v, "stop" if v["stop"] == cd["stop"] else "be", v["stop"], j)
                    elif h[j] >= v["tgt"] if d > 0 else l[j] <= v["tgt"]:
                        self.fin(cd, v, "target", v["tgt"], j)
                    elif flat or j - cd["fillJ"] >= P["hold"]:
                        self.fin(cd, v, "time", c[j], j)
                    elif v["stop"] == cd["stop"] and ((v["be"] == "1R" and (h[j] - cd["entry"] if d > 0 else cd["entry"] - l[j]) >= cd["R"]) or
                                                      (v["be"] == "ih" and cd["ih"] is not None and (h[j] > cd["ih"] if d > 0 else l[j] < cd["ih"]))):
                        v["stop"] = cd["entry"]                    # безубыток со следующей свечи
                self.sync(cd)
        self.live = [cd for cd in self.live if cd["state"] in ("limit", "open")]

        # RB, ждущие подтверждающего закрытия
        for p in list(reversed(self.pend)):
            d = 1 if p["bull"] else -1
            if l[j] < p["ext"] if d > 0 else h[j] > p["ext"]:
                self.pend.remove(p); continue                      # тень пробита — это уже не отказ
            if c[j] > o[j] if d > 0 else c[j] < o[j]:
                self.pend.remove(p); self.arm(p, j)
            elif j - p["j"] >= P["confirmBars"]:
                self.pend.remove(p)

        A5 = self.atr5[j]
        if A5 is None:
            return
        rng = h[j] - l[j]
        if not rng > 0:
            return
        body_top, body_bot = max(o[j], c[j]), min(o[j], c[j])
        for bull in (True, False):
            wick = body_bot - l[j] if bull else h[j] - body_top
            if wick < P["wickPct"] * rng or wick < P["wickATR"] * A5:
                continue
            L = self.leg_at(j, bull)
            if not L or L["size"] < P["legATR"] * A5 or not L["counter"]:
                continue
            ext, entry = (l[j], body_bot) if bull else (h[j], body_top)
            f_ext, f_entry = abs(L["X"] - ext) / L["size"], abs(L["X"] - entry) / L["size"]
            if f_ext > 1 or f_entry < P["discLo"] or (P["zone"] == "ote" and f_ext < P["oteLo"]):
                continue
            wlo, whi = (l[j], body_bot) if bull else (body_top, h[j])
            ctx, kind = _context(self, bull, self.recent, x, wlo, whi, ("00:00", "10:00"))
            if not ctx:
                continue
            lo_i = max(0, j - P["ihBars"])
            ih = (h[lo_i:j].max() if bull else l[lo_i:j].min()) if j > lo_i else None
            d = 1 if bull else -1
            p = dict(setup="RBV", bull=bull, t=int(t5[j]), j=j, ext=ext, entry=entry, stop=ext - d * P["stopBuf"] * A5, atr=A5, ctx=ctx, ctxKind=kind,
                     legX=L["X"], legO=L["O"], fib=f_ext, ih=ih if ih is not None and (ih - entry) * d > 0 else None,
                     trend=self.trend_at(int(t5[j]) + MIN5) == bull, min=mn)
            if c[j] > o[j] if bull else c[j] < o[j]:
                self.arm(p, j)
            else:
                self.pend.append(p)


# ====================================================================== модели 05 / 06: OTE
class OTE(_Base):
    P0 = dict(legBars=48, legATR=3, levels=(0.62, 0.705, 0.79), tolATR=0.15, minConf=3, mode="agg", stopMode="rule", stopBuf=0.1, minR_ATR=0.5,
              maxPivotATR=3, wait=48, trigBars=6, ihBars=6, hold=96, cost=0.00004, T=5, be="ih", rth=False, pda=True)
    VT, VB = [2, 3, 5, "leg"], ["none", "1R", "ih"]

    def __init__(self, k15, k5, **opts):
        super().__init__(k15, k5, {**self.P0, **opts})
        self.used = set()
        self.PK = f"{self.P['T']}|{self.P['be']}"

    def leg_at(self, j, bull):
        """Волна OTE: экстремум за 4 часа (включая свечу j), её начало, была ли встречная свеча и глубина отката."""
        P, h, l, o, c = self.P, self.h, self.l, self.o, self.c
        a = max(0, j - P["legBars"])
        iX = a + int(np.argmax(h[a:j + 1] if bull else -l[a:j + 1]))
        if iX <= a or iX == j:
            return None
        iO = a + int(np.argmin(l[a:iX + 1] if bull else -h[a:iX + 1]))
        X, O = (h[iX], l[iO]) if bull else (l[iX], h[iO])
        q = slice(iX + 1, j + 1)
        counter = bool(((c[q] < o[q]) if bull else (c[q] > o[q])).any())
        pb = l[q].min() if bull else h[q].max()
        return dict(X=X, O=O, iX=iX, iO=iO, size=abs(X - O), counter=counter, pb=pb)

    def sync(self, cd):
        v = cd["v"].get(self.PK)
        if v is None:
            return
        if v["done"]:
            cd.update(state="stop" if v["how"] == "be" else v["how"], res=v["res"], exitT=v["exitT"], exitPx=v["exitPx"])
        else:
            cd["state"] = "open"

    def fill(self, cd, j, px):
        P, h, l = self.P, self.h, self.l
        d = 1 if cd["bull"] else -1
        cd.update(entry=px, R=abs(px - cd["stop"]), fillJ=j, fillT=int(self.t5[j]), v={})
        lo_i = max(0, j - P["ihBars"])
        ih = (h[lo_i:j].max() if cd["bull"] else l[lo_i:j].min()) if j > lo_i else None
        cd["ih"] = ih if ih is not None and (ih - px) * d > 0 else None
        for T in self.VT:
            for be in self.VB:
                tgt = (cd["legX"] if (cd["legX"] - px) * d >= 1.5 * cd["R"] else None) if T == "leg" else px + d * T * cd["R"]
                cd["v"][f"{T}|{be}"] = dict(tgt=tgt, stop=cd["stop"], be=be, done=tgt is None)

    def on_bar(self, x):
        P, j, mn = self.P, x["j"], x["min"]
        o, h, l, c, t5 = self.o, self.h, self.l, self.c, self.t5
        rth = P["rth"]
        flat = (mn >= 955 or mn < 570) if rth else (1015 <= mn < 1080)
        no_fill = (not (570 <= mn < 945)) if rth else (1005 <= mn < 1080)
        in_open = 570 <= mn < 600

        for cd in self.live:
            d = 1 if cd["bull"] else -1
            if cd["state"] in ("limit", "touch"):
                if flat:
                    cd["state"] = "expired"; continue
                if h[j] > cd["legX"] if d > 0 else l[j] < cd["legX"]:
                    cd["state"] = "redraw"; continue               # новый экстремум — сетка фибо переезжает
                touched = l[j] <= cd["lim"] if d > 0 else h[j] >= cd["lim"]
                if cd["state"] == "limit":
                    if touched and not no_fill and cd["mode"] == "agg" and not in_open:
                        self.fill(cd, j, cd["lim"]); cd["how"] = "лимит"
                        if l[j] <= cd["stop"] if d > 0 else h[j] >= cd["stop"]:
                            for v in cd["v"].values():
                                if not v["done"]:
                                    self.fin(cd, v, "stop", cd["stop"], j)
                        self.sync(cd); continue
                    if touched:
                        cd.update(state="touch", touchJ=j)
                        if l[j] <= cd["stop"] if d > 0 else h[j] >= cd["stop"]:
                            cd["state"] = "cancel"
                        continue
                    if j - cd["j0"] >= P["wait"]:
                        cd["state"] = "expired"
                else:
                    if l[j] <= cd["stop"] if d > 0 else h[j] >= cd["stop"]:
                        cd["state"] = "cancel"; continue
                    if j - cd["touchJ"] > P["trigBars"]:
                        cd["state"] = "expired"; continue
                    trig = (c[j] > o[j] and c[j] > h[j - 1]) if d > 0 else (c[j] < o[j] and c[j] < l[j - 1])
                    if trig and not no_fill and (c[j] - cd["stop"]) * d >= P["minR_ATR"] * cd["atr"]:
                        self.fill(cd, j, c[j]); cd["how"] = "триггер"; self.sync(cd)
            elif cd["state"] == "open":
                for v in cd["v"].values():
                    if v["done"]:
                        continue
                    if l[j] <= v["stop"] if d > 0 else h[j] >= v["stop"]:
                        self.fin(cd, v, "stop" if v["stop"] == cd["stop"] else "be", v["stop"], j)
                    elif h[j] >= v["tgt"] if d > 0 else l[j] <= v["tgt"]:
                        self.fin(cd, v, "target", v["tgt"], j)
                    elif flat or j - cd["fillJ"] >= P["hold"]:
                        self.fin(cd, v, "time", c[j], j)
                    elif v["stop"] == cd["stop"] and ((v["be"] == "1R" and (h[j] - cd["entry"] if d > 0 else cd["entry"] - l[j]) >= cd["R"]) or
                                                      (v["be"] == "ih" and cd["ih"] is not None and (h[j] > cd["ih"] if d > 0 else l[j] < cd["ih"]))):
                        v["stop"] = cd["entry"]
                self.sync(cd)
        self.live = [cd for cd in self.live if cd["state"] in ("limit", "touch", "open")]

        A5 = self.atr5[j]
        if A5 is None or flat:
            return
        for bull in (True, False):
            L = self.leg_at(j, bull)
            if not L or L["size"] < P["legATR"] * A5 or not L["counter"]:
                continue
            lkey = (bull, L["iX"], L["iO"])
            if lkey in self.used:
                continue
            d = 1 if bull else -1
            lvl = lambda f: L["X"] - d * f * L["size"]
            tol = P["tolATR"] * A5
            if (L["pb"] - lvl(0.79)) * d < 0:
                self.used.add(lkey); continue                       # откат уже прошёл всю зону OTE
            base = dict(bull=bull, t=int(t5[j]), j0=j, legX=L["X"], legO=L["O"], iX=L["iX"], iO=L["iO"], trend=self.trend_at(int(t5[j]) + MIN5) == bull,
                        min=mn, mode=P["mode"], state="limit", atr=A5, v={})
            if not P["pda"]:                                         # «OTE + тренд»: лимит на первом ещё не достигнутом уровне
                lv = next((q for q in P["levels"] if (lvl(q) - L["pb"]) * d < 0), None)
                if lv is None:
                    continue
                lim, pivot = lvl(lv), L["O"] - d * P["stopBuf"] * A5
                R = (lim - pivot) * d
                if not R >= P["minR_ATR"] * A5:
                    continue
                self.used.add(lkey)
                cd = dict(base, lim=lim, entry=lim, stop=pivot, R=R, lv=lv, conf=["тренд", f"уровень {lv}"], kinds=[], nConf=2)
                self.live.append(cd); self.out.append(cd)
                continue
            m = []
            for z in x["zones"]:
                if z["bull"] == bull and z["created"] <= x["t"] and (z["ce"] - L["pb"]) * d < 0:
                    lv = next((q for q in P["levels"] if abs(z["ce"] - lvl(q)) <= tol), None)
                    if lv is not None:
                        m.append(dict(z=z, lv=lv))
            if not m:
                continue
            m.sort(key=lambda q: -q["z"]["ce"] * d)                  # самый мелкий уровень (ближе к цене) первым
            top, lim = m[0], m[0]["z"]["ce"]
            conf, kinds = ["дисконт", f"CE {top['z']['kind']} 15m на {top['lv']}"], ["disc", "pda"]
            second = next((q for q in m if q["z"] is not top["z"] and abs(q["z"]["ce"] - top["z"]["ce"]) > tol * 0.2), None)
            if second:
                conf.append(f"ещё CE {second['z']['kind']} на {second['lv']}"); kinds.append("stack")
            ko = next((nm for nm in ("00:00", "09:30", "10:00") if x["opens"][nm] is not None and abs(x["opens"][nm] - lim) <= tol), None)
            if ko:
                conf.append(f"открытие {ko}"); kinds.append("open")
            liq = next((p for p in x["pools"] if p["kind"] == ("SSL" if bull else "BSL") and (p["price"] - lim) * d > 0 and (L["pb"] - p["price"]) * d > 0), None)
            if liq:
                conf.append(f"ликвидность {liq['tag']}"); kinds.append("liq")
            if len(conf) < P["minConf"]:
                continue
            pivot, s79 = L["O"] - d * P["stopBuf"] * A5, lvl(0.79) - d * P["stopBuf"] * A5
            stop = pivot if P["stopMode"] == "pivot" else s79 if P["stopMode"] == "79" else (pivot if abs(lim - pivot) <= P["maxPivotATR"] * A5 else s79)
            if (lim - stop) * d < P["minR_ATR"] * A5:
                stop = pivot
            R = (lim - stop) * d
            if not R >= P["minR_ATR"] * A5:
                continue
            self.used.add(lkey)
            cd = dict(base, lim=lim, entry=lim, stop=stop, R=R, lv=top["lv"], conf=conf, kinds=kinds, nConf=len(conf))
            self.live.append(cd); self.out.append(cd)


# ====================================================================== прогон, выборка, правила аккаунта, статистика
def drive(k15, k5, strategies, proxy=True, trace=None):
    """Один проход ICT-движка, все стратегии получают каждую свечу."""
    def hook(x):
        for s in strategies:
            s.on_bar(x)
    ict.run(k15, k5, proxy=proxy, trace=trace, hook=hook)
    return strategies


def as_variant(s, key):
    """Сделка с результатом конкретного варианта выхода (например '5|ih' или 5). None — вариант не исполнился / не закрыт."""
    v = s.get("v", {}).get(key)
    if not v or v["tgt"] is None or not v["done"]:
        return None
    return dict(s, res=v["res"], exitT=v["exitT"], exitPx=v["exitPx"], state=v["how"], tgt=v["tgt"])


def pick(sigs, key="5|ih", filt=lambda s: True):
    """Все закрытые сделки варианта `key`, отсортированные по времени входа."""
    out = [as_variant(s, key) for s in sigs if filt(s)]
    return sorted([s for s in out if s], key=lambda s: s["fillT"])


def _cme_day(t, d_cache={}):
    """Торговый день CME (18:00 NY — начало следующего дня)."""
    if t not in d_cache:
        d_cache[t] = ny([t + 6 * 3_600_000])[0][0]
    return d_cache[t]


def risk_rules(sigs, trend=True, max_day=3, max_loss_streak=2):
    """Правила аккаунта: ≤ max_day сделок в день, после max_loss_streak убытков подряд — стоп до конца дня,
    одна позиция на инструмент, (опционально) только по тренду. Возвращает принятые сделки."""
    filled = sorted([s for s in sigs if s.get("fillT") is not None and (not trend or s["trend"])], key=lambda s: s["fillT"])
    taken = []
    for s in filled:
        day = _cme_day(s["fillT"])
        today = [x for x in taken if _cme_day(x["fillT"]) == day]
        closed = sorted([x for x in today if x.get("exitT") is not None and x["exitT"] <= s["fillT"] and x["state"] != "open"], key=lambda x: x["exitT"])
        streak = 0
        for x in reversed(closed):
            if x["res"] < -0.25:
                streak += 1
            else:
                break
        busy = any(x["sym"] == s["sym"] and (x["state"] == "open" or (x.get("exitT") or 0) > s["fillT"]) for x in taken)
        if len(today) < max_day and streak < max_loss_streak and not busy:
            taken.append(s)
    return taken


def bot(trades):
    """Бот = правила аккаунта отдельно на каждом инструменте (у каждого свой счёт), потом всё по времени."""
    syms = list(dict.fromkeys(s["sym"] for s in trades))
    out = [x for sym in syms for x in risk_rules([s for s in trades if s["sym"] == sym])]
    return sorted(out, key=lambda s: s["fillT"])


def stat(r, win_cut=0.05):
    """Сводка сделок в R: n, % прибыльных, средний R, t, profit factor, сумма, просадка, самая длинная серия убытков, половины."""
    r = np.asarray(r, float)
    n = len(r)
    if n < 8:
        return None
    m, sd = r.mean(), r.std(ddof=1)
    eq = np.cumsum(r)
    dd = (eq - np.maximum.accumulate(np.maximum(eq, 0))).min()
    streak = best = 0
    for x in r:
        streak = streak + 1 if x < -0.25 else 0
        best = max(best, streak)
    gw, gl = r[r > 0].sum(), -r[r < 0].sum()
    half = n // 2
    return dict(n=n, win=(r > win_cut).mean() * 100, avgR=m, t=m / (sd / np.sqrt(n)) if sd else 0.0, sumR=eq[-1], maxDD=min(dd, 0.0),
                pf=gw / gl if gl else None, maxLossStreak=best, first=r[:half].mean(), second=r[half:].mean())


def fmt(s):
    if not s:
        return "мало сделок"
    return (f"n={s['n']:>4} win {s['win']:>2.0f}%  {s['avgR']:+.2f}R  t {s['t']:+.1f}  PF {s['pf']:.2f}  сумма {s['sumR']:+.0f}R  DD {s['maxDD']:.1f}R  "
            f"серия {s['maxLossStreak']}  половины {s['first']:+.2f}/{s['second']:+.2f}") if s.get("pf") else f"n={s['n']} {s['avgR']:+.2f}R"


def random_baseline(data, trades, runs, trend_of=trend_fn):
    """Случайные входы: тот же инструмент и направление по тому же тренду, тот же стоп в ATR, цель 5R, безубыток после
    внутреннего хая. Если модель не лучше случайных входов — её «логика» ничего не добавляет."""
    prep = {}
    for sym, (k15, k5) in data.items():
        h, l, c, t5 = k5["h"], k5["l"], k5["c"], k5["t"]
        atr5, tr = atr_wilder(h, l, c), trend_of(k15)
        _, mins, dows = ny(t5)
        cand = {True: [], False: []}
        for i in range(60, len(t5) - 100):
            mn, dw = mins[i], dows[i]
            if atr5[i] is None or dw == 5 or (dw == 6 and mn < 1080) or (dw == 4 and mn >= 1005) or (1005 <= mn < 1080):
                continue
            up = tr(int(t5[i]) + MIN5)
            if up is not None:
                cand[up].append(i)
        prep[sym] = dict(h=h, l=l, c=c, atr5=atr5, mins=mins, cand=cand)
    rnd = Lcg(12345)

    def sim(p, i, bull, r_atr):
        d = 1 if bull else -1
        entry, R = p["c"][i], r_atr * p["atr5"][i]
        stop, tgt = entry - d * R, entry + d * 5 * R
        ih = p["h"][i - 6:i].max() if bull else p["l"][i - 6:i].min()
        if (ih - entry) * d <= 0:
            ih = None
        s = stop
        res = lambda px: (px - entry) * d / R - 0.00004 * entry / R
        for k in range(i + 1, min(len(p["c"]), i + 97)):
            if p["l"][k] <= s if d > 0 else p["h"][k] >= s:
                return res(s)
            if p["h"][k] >= tgt if d > 0 else p["l"][k] <= tgt:
                return res(tgt)
            if 1015 <= p["mins"][k] < 1080:
                return res(p["c"][k])
            if s == stop and ih is not None and (p["h"][k] > ih if d > 0 else p["l"][k] < ih):
                s = entry
        return (p["c"][min(len(p["c"]) - 1, i + 96)] - entry) * d / R

    avgs = []
    for _ in range(runs):
        tot = 0.0
        for tq in trades:
            p = prep[tq["sym"]]
            arr = p["cand"][tq["bull"]]
            i = arr[int(rnd() * len(arr))]
            tot += sim(p, i, tq["bull"], tq["R"] / tq["atr"])
        avgs.append(tot / len(trades))
    return sorted(avgs)


def monte_carlo(r, runs=10000):
    """Монте-Карло порядка сделок: выборка с возвращением. Какие итоги, просадки и серии стопов ждать."""
    rnd = Lcg(777)
    r = list(r)
    n = len(r)
    dds, sums, streaks = [], [], []
    for _ in range(runs):
        eq = pk = dd = 0.0
        ls = mls = 0
        for _ in range(n):
            x = r[int(rnd() * n)]
            eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
            ls = ls + 1 if x < -0.25 else 0; mls = max(mls, ls)
        dds.append(dd); sums.append(eq); streaks.append(mls)
    q = lambda a, p: sorted(a)[int(p * (len(a) - 1))]
    return dict(pLoss=sum(s <= 0 for s in sums) / runs, sum5=q(sums, 0.05), sum50=q(sums, 0.5), sum95=q(sums, 0.95),
                dd50=q(dds, 0.5), dd5=q(dds, 0.05), streak50=q(streaks, 0.5), streak95=q(streaks, 0.95))
