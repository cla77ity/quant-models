//+------------------------------------------------------------------+
//| RBV_Prop.mq5 — RB entry model (RBV, quant-models/models/04) for a |
//| prop account (NextLevelFunded Instant Flex 10k rules).           |
//|                                                                  |
//| Strategy (exactly the backtested rules):                         |
//|  1. Trend: last closed M15 bar vs its SMA50 — only trades with it.|
//|  2. Leg: swing of the last 48 M5 bars (4 h) in the trend's        |
//|     direction, size >= 3 ATR(M5), after a counter-coloured close. |
//|  3. Rejection block on M5 in the pullback: wick >= 50% of the bar |
//|     and >= 0.5 ATR, wick reaches OTE (>= 0.62 of the leg, not past|
//|     its origin), block start in discount (>= 0.5), confirming     |
//|     close on that bar or one of the next two.                     |
//|  4. Context: a liquidity pool swept in the last 3 bars, or a      |
//|     same-way M15 OB / FVG under the wick, or the 00:00 / 10:00 NY |
//|     open inside the wick (ICT engine state, ported below).        |
//|  5. Limit at the start of the RB for 3 bars, stop beyond the wick |
//|     + 0.1 ATR, target 5R, break-even once the internal high is    |
//|     taken, flat by 16:55 NY, at most 8 h in the trade.            |
//|  6. Bot rules per symbol: <= 3 fills per CME day, done after two  |
//|     losses in a row, one position per symbol.                     |
//|                                                                  |
//| Prop guards: fixed risk per trade (default 0.20%, hard cap 1%),   |
//| daily loss stop (1%), daily profit cap (1%, consistency 15%),     |
//| total stop (-3% from the start balance; the firm's static limit is|
//| -4%), TP set only 130 s after the fill and no own exit before     |
//| 130 s (two-minute rule; the stop-loss always works), <= 1 trade   |
//| request per second, <= 10 trades a day, spread filter, optional   |
//| high-impact US news block, trades only on the login you enter.    |
//+------------------------------------------------------------------+
#property copyright "quant-models / RBV"
#property version   "1.00"
#property description "RBV strategy with prop-firm risk guards. Set InpAccountLogin to your account number to enable trading."

#include <Trade\Trade.mqh>

//--- account & risk
input long   InpAccountLogin   = 0;        // trade only on this login (0 = do not trade, just log)
input double InpStartBalance   = 10000;    // account size the static drawdown is measured from
input double InpRiskPct        = 0.20;     // risk per trade, % of equity (hard cap 1%)
input double InpDailyStopPct   = 1.0;      // stop for the day after this loss (firm: 3%)
input double InpDailyCapPct    = 1.0;      // stop for the day after this profit (consistency 15%)
input double InpTotalStopPct   = 3.0;      // close all and halt below start balance − this % (firm: 4%)
input int    InpMaxTradesDay   = 10;       // fills per prop day, all symbols together
input int    InpMinHoldSec     = 130;      // two-minute rule: no own exit / TP before this
input double InpMaxSpreadBp    = 0.8;      // skip a new order if spread > this (basis points of price)
input double InpMaxSpreadR     = 0.20;     // ... or if the spread costs more than this share of 1R
input bool   InpNewsFilter     = true;     // block new orders around high-impact US news
input int    InpNewsMinutes    = 5;        // minutes before and after the release
//--- markets
input string InpSymbols        = "NAS100,US500";  // comma separated, broker names
input ulong  InpMagic          = 20261115;

//--- RBV parameters (as backtested; change only to test)
input double InpWickPct  = 0.50, InpWickATR = 0.50, InpLegATR = 3.0, InpOteLo = 0.62, InpDiscLo = 0.50, InpStopBuf = 0.10, InpTargetR = 5.0;
input int    InpLegBars  = 48, InpConfirmBars = 2, InpWaitBars = 3, InpIhBars = 6, InpHoldBars = 96;

#define MAXB 600
CTrade trade;
datetime g_lastReq = 0; uint g_lastReqMs = 0;
string   g_propDay = ""; double g_dayStartEq = 0; bool g_dayBlocked = false; int g_dayTrades = 0;
bool     g_halted = false;
long     g_srvOff = 0;          // server time − UTC, seconds

//+------------------------------------------------------------------+
//| time helpers: server → UTC → New York (US daylight saving rules) |
//+------------------------------------------------------------------+
datetime NthSunday(int y, int mon, int n)
  {
   MqlDateTime d; d.year = y; d.mon = mon; d.day = 1; d.hour = 0; d.min = 0; d.sec = 0;
   datetime t = StructToTime(d); MqlDateTime w; TimeToStruct(t, w);
   int add = (7 - w.day_of_week) % 7 + 7 * (n - 1);
   return t + add * 86400;
  }
datetime UtcToNy(datetime utc)
  {
   MqlDateTime d; TimeToStruct(utc, d);
   datetime s = NthSunday(d.year, 3, 2) + 7 * 3600, e = NthSunday(d.year, 11, 1) + 6 * 3600;
   return utc - ((utc >= s && utc < e) ? 4 : 5) * 3600;
  }
datetime SrvToNy(datetime srv) { return UtcToNy(srv - (datetime)g_srvOff); }
int      NyMin(datetime ny)    { MqlDateTime d; TimeToStruct(ny, d); return d.hour * 60 + d.min; }
int      NyDow(datetime ny)    { MqlDateTime d; TimeToStruct(ny, d); return d.day_of_week; }   // 0 = Sun
string   NyDate(datetime ny)   { return TimeToString(ny, TIME_DATE); }

void Log(string m) { Print("RBV: ", m); }

//+------------------------------------------------------------------+
//| per-symbol state: ICT engine pieces + RBV                         |
//+------------------------------------------------------------------+
class CSym
  {
public:
   string            sym;
   //--- M15 history (chronological) for zones and pivots
   double            h15[], l15[], o15[], c15[], v15[], atr15[]; datetime t15[]; int n15; double atrNow; bool atrOk; double atrAcc;
   //--- zones OB / FVG
   int               zKind[]; bool zBull[], zTouched[], zReacted[]; double zTop[], zBot[], zWick[], zBody[], zCe[], zMfe[]; datetime zCreated[], zTouchT[]; int zMaxH[];
   //--- liquidity pools
   bool              pBsl[]; string pTag[]; double pPrice[]; int pTouch[]; datetime pCreated[]; datetime lastPh, lastPl;
   string            curDay, curWeek; double dH, dL, wH, wL; bool dOk, wOk;
   bool              sIn[3]; double sHi[3], sLo[3];
   double            open00, open1000; string midDate;
   //--- M5 history (chronological)
   double            h5[], l5[], o5[], c5[], atr5[]; datetime t5[]; int n5; double atr5Acc; int barNo;
   //--- swept pools of the last 3 M5 bars: bar number and side
   int               swBar[]; bool swBsl[];
   //--- RBV pending confirmations
   bool              pdBull[]; int pdBar[]; double pdExt[], pdEntry[], pdStop[], pdAtr[], pdLegX[], pdIh[];
   //--- live orders / position
   bool              oBull[]; int oState[], oArmBar[], oFillBar[]; ulong oTicket[]; double oEntry[], oStop[], oR[], oIh[], oRisk[]; datetime oFillTime[]; bool oTp[], oBe[];
   //--- bot day rules
   string            botDay; int botFills, botLossStreak;

   void              Init(string s) { sym = s; n15 = n5 = 0; atrOk = false; atrNow = 0; atrAcc = 0; atr5Acc = 0; barNo = 0; lastPh = lastPl = 0; curDay = curWeek = midDate = botDay = ""; dOk = wOk = false;
                                      open00 = open1000 = EMPTY_VALUE; for(int k = 0; k < 3; k++) sIn[k] = false; botFills = botLossStreak = 0; }
  };
CSym *g[];

//--- array helpers
template<typename T> void Push(T &a[], T v) { int n = ArraySize(a); ArrayResize(a, n + 1); a[n] = v; }
template<typename T> void Del(T &a[], int i) { ArrayRemove(a, i, 1); }

//+------------------------------------------------------------------+
//| liquidity pools (as qm/ict.py add_pool)                          |
//+------------------------------------------------------------------+
void AddPool(CSym *s, double price, bool bsl, string tag, datetime now)
  {
   double tol = 0.03 * (s.atrNow > 0 ? s.atrNow : 1);
   for(int i = 0; i < ArraySize(s.pPrice); i++)
      if(s.pBsl[i] == bsl && MathAbs(s.pPrice[i] - price) <= tol)
        {
         s.pTouch[i]++; s.pPrice[i] = (s.pPrice[i] * (s.pTouch[i] - 1) + price) / s.pTouch[i];
         if(tag == "SH" || tag == "SL") { if(s.pTag[i] == "SH" || s.pTag[i] == "SL" || s.pTag[i] == "EQH" || s.pTag[i] == "EQL") s.pTag[i] = bsl ? "EQH" : "EQL"; }
         else s.pTag[i] = tag;
         return;
        }
   Push(s.pBsl, bsl); Push(s.pTag, tag); Push(s.pPrice, price); Push(s.pTouch, 1); Push(s.pCreated, now);
   if(ArraySize(s.pPrice) > 60)
     {
      int wi = 0; double ws = 1e18;
      for(int i = 0; i < ArraySize(s.pPrice); i++) { double sc = s.pTouch[i] * 10 + (double)(now - s.pCreated[i]) / 3600.0 * 0.5; if(sc < ws) { ws = sc; wi = i; } }
      Del(s.pBsl, wi); Del(s.pTag, wi); Del(s.pPrice, wi); Del(s.pTouch, wi); Del(s.pCreated, wi);
     }
  }

bool DupZone(CSym *s, int kind, bool bull, double ce)
  {
   double tol = 0.03 * (s.atrNow > 0 ? s.atrNow : 1);
   for(int i = 0; i < ArraySize(s.zCe); i++) if(s.zKind[i] == kind && s.zBull[i] == bull && MathAbs(s.zCe[i] - ce) <= tol * 5) return true;
   return false;
  }

void AddZone(CSym *s, int kind, bool bull, double top, double bot, double wick, double body, double ce, datetime created, int maxH)
  {
   Push(s.zKind, kind); Push(s.zBull, bull); Push(s.zTop, top); Push(s.zBot, bot); Push(s.zWick, wick); Push(s.zBody, body); Push(s.zCe, ce);
   Push(s.zCreated, created); Push(s.zMaxH, maxH); Push(s.zTouched, false); Push(s.zReacted, false); Push(s.zTouchT, (datetime)0); Push(s.zMfe, 0.0);
  }
void DelZone(CSym *s, int i)
  {
   Del(s.zKind, i); Del(s.zBull, i); Del(s.zTop, i); Del(s.zBot, i); Del(s.zWick, i); Del(s.zBody, i); Del(s.zCe, i);
   Del(s.zCreated, i); Del(s.zMaxH, i); Del(s.zTouched, i); Del(s.zReacted, i); Del(s.zTouchT, i); Del(s.zMfe, i);
  }

//--- volume baseline of the `len` bars before i
bool MeanSd(const double &v[], int i, int len, double &m, double &sd)
  {
   if(i < len) return false;
   double s1 = 0, s2 = 0;
   for(int k = i - 1; k >= i - len; k--) { s1 += v[k]; s2 += v[k] * v[k]; }
   m = s1 / len; sd = MathSqrt(MathMax(0, s2 / len - m * m)); return true;
  }

//+------------------------------------------------------------------+
//| a closed M15 bar: ATR, OB / FVG zones, swing pivots → pools       |
//+------------------------------------------------------------------+
void OnM15(CSym *s, const MqlRates &r)
  {
   int i = s.n15;
   ArrayResize(s.h15, i + 1); ArrayResize(s.l15, i + 1); ArrayResize(s.o15, i + 1); ArrayResize(s.c15, i + 1); ArrayResize(s.v15, i + 1); ArrayResize(s.atr15, i + 1); ArrayResize(s.t15, i + 1);
   s.h15[i] = r.high; s.l15[i] = r.low; s.o15[i] = r.open; s.c15[i] = r.close; s.v15[i] = (double)r.tick_volume; s.t15[i] = r.time; s.atr15[i] = 0;
   if(i >= 1)
     {
      double tr = MathMax(r.high - r.low, MathMax(MathAbs(r.high - s.c15[i - 1]), MathAbs(r.low - s.c15[i - 1])));
      s.atrAcc = i <= 14 ? s.atrAcc + tr / 14 : s.atrAcc + (tr - s.atrAcc) / 14;
      if(i >= 14) s.atr15[i] = s.atrAcc;
     }
   s.n15++;
   datetime now = r.time + 900;
   if(i < 22 || s.atr15[i - 1] <= 0) return;
   double A = s.atr15[i - 1]; s.atrNow = A; s.atrOk = true;
   double h[], l[], o[], c[], v[]; ArrayCopy(h, s.h15); ArrayCopy(l, s.l15); ArrayCopy(o, s.o15); ArrayCopy(c, s.c15); ArrayCopy(v, s.v15);
   //--- volume z of bar i (tick volume — CFDs have no exchange volume; OB filter z >= 0.5 as in the engine)
   double m, sd, z = 0; if(MeanSd(v, i, 20, m, sd) && sd > 0) z = (v[i] - m) / sd;
   //--- order blocks
   if(c[i - 1] < o[i - 1] && c[i] > h[i - 1] && c[i] - l[i - 1] >= A && z >= 0.5 && !DupZone(s, 1, true, (h[i - 1] + l[i - 1]) / 2))
      AddZone(s, 1, true, 0, 0, l[i - 1], h[i - 1], (h[i - 1] + l[i - 1]) / 2, now, 48);
   if(c[i - 1] > o[i - 1] && c[i] < l[i - 1] && h[i - 1] - c[i] >= A && z >= 0.5 && !DupZone(s, 1, false, (h[i - 1] + l[i - 1]) / 2))
      AddZone(s, 1, false, 0, 0, h[i - 1], l[i - 1], (h[i - 1] + l[i - 1]) / 2, now, 48);
   //--- fair value gaps (middle bar with above-average volume)
   bool volOk = MeanSd(v, i - 1, 20, m, sd) && v[i - 1] > m;
   if(volOk && h[i - 2] < l[i] && l[i] - h[i - 2] >= 0.3 * A) AddZone(s, 2, true, l[i], h[i - 2], 0, 0, (l[i] + h[i - 2]) / 2, now, 24);
   if(volOk && l[i - 2] > h[i] && l[i - 2] - h[i] >= 0.3 * A) AddZone(s, 2, false, l[i - 2], h[i], 0, 0, (l[i - 2] + h[i]) / 2, now, 24);
   //--- swing pivots 5/5 → pools
   int p = i - 5;
   if(p >= 5)
     {
      bool isH = true, isL = true;
      for(int q = p - 5; q <= p + 5; q++) { if(q == p) continue; if(h[q] >= h[p]) isH = false; if(l[q] <= l[p]) isL = false; }
      if(isH && s.lastPh != s.t15[p]) { s.lastPh = s.t15[p]; AddPool(s, h[p], true, "SH", now); }
      if(isL && s.lastPl != s.t15[p]) { s.lastPl = s.t15[p]; AddPool(s, l[p], false, "SL", now); }
     }
  }

//+------------------------------------------------------------------+
//| trend: last M15 bar closed by time t vs its SMA50                 |
//+------------------------------------------------------------------+
int TrendAt(CSym *s, datetime t)          // 1 up, 0 down, -1 unknown
  {
   int i = -1;
   for(int k = s.n15 - 1; k >= 0; k--) if(s.t15[k] + 900 <= t) { i = k; break; }
   if(i < 50) return -1;
   double sum = 0; for(int q = i - 49; q <= i; q++) sum += s.c15[q];
   return s.c15[i] > sum / 50 ? 1 : 0;
  }

//+------------------------------------------------------------------+
//| bot day rules (per symbol): <= 3 fills per CME day, 2 losses stop |
//+------------------------------------------------------------------+
string CmeDay(datetime srv) { return NyDate(SrvToNy(srv) + 6 * 3600); }
bool   BotAllows(CSym *s, datetime srv)
  {
   string d = CmeDay(srv);
   if(d != s.botDay) { s.botDay = d; s.botFills = 0; s.botLossStreak = 0; }
   return s.botFills < 3 && s.botLossStreak < 2;
  }

//+------------------------------------------------------------------+
//| trade request plumbing: rate limit, spread, news, risk sizing    |
//+------------------------------------------------------------------+
bool RateOk() { uint ms = GetTickCount(); if(ms - g_lastReqMs < 1100) return false; g_lastReqMs = ms; return true; }

bool NewsBlocked()
  {
   if(!InpNewsFilter || MQLInfoInteger(MQL_TESTER)) return false;
   MqlCalendarValue vals[]; datetime now = TimeTradeServer();
   if(CalendarValueHistory(vals, now - InpNewsMinutes * 60, now + InpNewsMinutes * 60, "US") <= 0) return false;
   for(int i = 0; i < ArraySize(vals); i++)
     {
      MqlCalendarEvent ev;
      if(CalendarEventById(vals[i].event_id, ev) && ev.importance == CALENDAR_IMPORTANCE_HIGH) return true;
     }
   return false;
  }

bool TradingAllowed()
  {
   if(MQLInfoInteger(MQL_TESTER)) return !g_halted && !g_dayBlocked;
   if(InpAccountLogin == 0 || AccountInfoInteger(ACCOUNT_LOGIN) != InpAccountLogin) return false;
   return !g_halted && !g_dayBlocked && TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && MQLInfoInteger(MQL_TRADE_ALLOWED);
  }

double LotsFor(string sym, double R, double &riskMoney)
  {
   double tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE), ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP), vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN), vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(tv <= 0 || ts <= 0 || R <= 0 || step <= 0) return 0;
   double pct = MathMin(InpRiskPct, 1.0);
   riskMoney = AccountInfoDouble(ACCOUNT_EQUITY) * pct / 100.0;
   double perLot = R / ts * tv;
   double lots = MathFloor(riskMoney / perLot / step + 1e-9) * step;
   if(lots < vmin) return 0;                                   // even the minimum lot would risk more than allowed
   lots = MathMin(lots, vmax);
   riskMoney = lots * perLot;
   return NormalizeDouble(lots, 2);
  }

//+------------------------------------------------------------------+
//| RBV: arm a limit at the start of a confirmed rejection block      |
//+------------------------------------------------------------------+
void Arm(CSym *s, int k)                              // k = pending index
  {
   bool bull = s.pdBull[k]; double entry = s.pdEntry[k], stop = s.pdStop[k], R = MathAbs(entry - stop);
   if(R <= 0) return;
   if(!TradingAllowed())
     { Log(StringFormat("%s: СИГНАЛ (без сделки) %s лимит @ %.2f, стоп %.2f, цель %.2f", s.sym, bull ? "BUY" : "SELL", entry, stop, entry + (bull ? 1 : -1) * InpTargetR * R)); return; }
   if(!BotAllows(s, s.t5[s.n5 - 1])) { Log(s.sym + ": правила дня (3 сделки / 2 убытка подряд) — пропуск"); return; }
   if(g_dayTrades >= InpMaxTradesDay) { Log(s.sym + ": лимит сделок на день"); return; }
   for(int i = 0; i < ArraySize(s.oState); i++) if(s.oState[i] == 1) return;          // one position per symbol
   double bid = SymbolInfoDouble(s.sym, SYMBOL_BID), ask = SymbolInfoDouble(s.sym, SYMBOL_ASK), mid = (bid + ask) / 2;
   if(mid <= 0) return;
   double spread = ask - bid;
   if(spread / mid * 1e4 > InpMaxSpreadBp || spread > InpMaxSpreadR * R) { Log(s.sym + StringFormat(": спред %.1f б.п. — пропуск", spread / mid * 1e4)); return; }
   if(NewsBlocked()) { Log(s.sym + ": красные новости США рядом — пропуск"); return; }
   if(bull ? ask <= entry : bid >= entry) return;      // price already past the limit: no chase
   double riskMoney; double lots = LotsFor(s.sym, R, riskMoney);
   if(lots <= 0) { Log(s.sym + ": риск на минимальный лот больше заданного — пропуск"); return; }
   if(!RateOk()) return;
   int dg = (int)SymbolInfoInteger(s.sym, SYMBOL_DIGITS);
   trade.SetExpertMagicNumber(InpMagic);
   bool ok = bull ? trade.BuyLimit(lots, NormalizeDouble(entry, dg), s.sym, NormalizeDouble(stop, dg), 0, ORDER_TIME_GTC, 0, "RBV")
                  : trade.SellLimit(lots, NormalizeDouble(entry, dg), s.sym, NormalizeDouble(stop, dg), 0, ORDER_TIME_GTC, 0, "RBV");
   if(!ok) { Log(s.sym + ": ордер не выставлен, код " + (string)trade.ResultRetcode()); return; }
   Push(s.oBull, bull); Push(s.oState, 0); Push(s.oArmBar, s.barNo); Push(s.oFillBar, 0); Push(s.oTicket, trade.ResultOrder()); Push(s.oEntry, entry); Push(s.oStop, stop);
   Push(s.oR, R); Push(s.oIh, s.pdIh[k]); Push(s.oRisk, riskMoney); Push(s.oFillTime, (datetime)0); Push(s.oTp, false); Push(s.oBe, false);
   Log(StringFormat("%s: %s LIMIT %.2f лота @ %.2f, стоп %.2f, риск $%.0f", s.sym, bull ? "BUY" : "SELL", lots, entry, stop, riskMoney));
  }

void DelOrder(CSym *s, int i)
  {
   Del(s.oBull, i); Del(s.oState, i); Del(s.oArmBar, i); Del(s.oFillBar, i); Del(s.oTicket, i); Del(s.oEntry, i); Del(s.oStop, i);
   Del(s.oR, i); Del(s.oIh, i); Del(s.oRisk, i); Del(s.oFillTime, i); Del(s.oTp, i); Del(s.oBe, i);
  }

//+------------------------------------------------------------------+
//| a closed M5 bar: engine state, then the RBV hook                  |
//+------------------------------------------------------------------+
void OnM5(CSym *s, const MqlRates &r, bool live)
  {
   //--- M15 bars closed by this bar's open time (as the engine)
   MqlRates q[];
   datetime from = s.n15 > 0 ? s.t15[s.n15 - 1] + 1 : r.time - 3 * 86400;
   if(r.time - 900 >= from && CopyRates(s.sym, PERIOD_M15, from, r.time - 900, q) > 0)
      for(int k = 0; k < ArraySize(q); k++) if(q[k].time + 900 <= r.time && (s.n15 == 0 || q[k].time > s.t15[s.n15 - 1])) OnM15(s, q[k]);
   //--- M5 history + ATR(M5)
   int j = s.n5;
   ArrayResize(s.h5, j + 1); ArrayResize(s.l5, j + 1); ArrayResize(s.o5, j + 1); ArrayResize(s.c5, j + 1); ArrayResize(s.atr5, j + 1); ArrayResize(s.t5, j + 1);
   s.h5[j] = r.high; s.l5[j] = r.low; s.o5[j] = r.open; s.c5[j] = r.close; s.t5[j] = r.time; s.atr5[j] = 0;
   if(j >= 1)
     {
      double tr = MathMax(r.high - r.low, MathMax(MathAbs(r.high - s.c5[j - 1]), MathAbs(r.low - s.c5[j - 1])));
      s.atr5Acc = j <= 14 ? s.atr5Acc + tr / 14 : s.atr5Acc + (tr - s.atr5Acc) / 14;
      if(j >= 14) s.atr5[j] = s.atr5Acc;
     }
   s.n5++; s.barNo++;
   if(!s.atrOk) return;
   datetime ny = SrvToNy(r.time); int mn = NyMin(ny), dw = NyDow(ny);
   bool closed = dw == 6 || (dw == 0 && mn < 1080) || (dw == 5 && mn >= 1020) || (mn >= 1020 && mn < 1080);
   if(closed) return;
   double A = s.atrNow, H5 = r.high, L5 = r.low, C5 = r.close; datetime t = r.time;
   //--- key opens (reset at NY midnight)
   string nd = NyDate(ny);
   if(nd != s.midDate) { s.midDate = nd; s.open00 = s.open1000 = EMPTY_VALUE; }
   if(mn == 0) s.open00 = r.open; if(mn == 600) s.open1000 = r.open;
   //--- day / week pools (CME day starts 18:00 NY)
   string dk = mn >= 1080 ? NyDate(ny + 86400) : nd;
   if(dk != s.curDay)
     {
      if(s.dOk) { AddPool(s, s.dH, true, "PDH", t); AddPool(s, s.dL, false, "PDL", t); }
      s.curDay = dk; s.dH = H5; s.dL = L5; s.dOk = true;
      MqlDateTime dd; TimeToStruct(StringToTime(dk), dd); string wk = NyDate(StringToTime(dk) - ((dd.day_of_week + 6) % 7) * 86400);
      if(wk != s.curWeek) { if(s.wOk) { AddPool(s, s.wH, true, "PWH", t); AddPool(s, s.wL, false, "PWL", t); } s.curWeek = wk; s.wH = H5; s.wL = L5; s.wOk = true; }
     }
   s.dH = MathMax(s.dH, H5); s.dL = MathMin(s.dL, L5); s.wH = MathMax(s.wH, H5); s.wL = MathMin(s.wL, L5);
   int sa[3] = {1200, 120, 570}, sb[3] = {1440, 300, 720}; string sn[3] = {"ASIA", "LDN", "AM"};
   for(int k = 0; k < 3; k++)
     {
      bool ins = mn >= sa[k] && mn < sb[k];
      if(ins && !s.sIn[k]) { s.sHi[k] = H5; s.sLo[k] = L5; } else if(ins) { s.sHi[k] = MathMax(s.sHi[k], H5); s.sLo[k] = MathMin(s.sLo[k], L5); }
      if(!ins && s.sIn[k]) { AddPool(s, s.sHi[k], true, sn[k] + " H", t); AddPool(s, s.sLo[k], false, sn[k] + " L", t); }
      s.sIn[k] = ins;
     }
   //--- pools: swept / expired (72 h)
   for(int i = ArraySize(s.pPrice) - 1; i >= 0; i--)
     {
      bool sw = s.pBsl[i] ? H5 > s.pPrice[i] : L5 < s.pPrice[i];
      if(sw) { Push(s.swBar, s.barNo); Push(s.swBsl, s.pBsl[i]); }
      if(sw || t - s.pCreated[i] > 72 * 3600) { Del(s.pBsl, i); Del(s.pTag, i); Del(s.pPrice, i); Del(s.pTouch, i); Del(s.pCreated, i); }
     }
   //--- zones: touch / reaction / invalidation / failure / expiry
   for(int i = ArraySize(s.zCe) - 1; i >= 0; i--)
     {
      if(t < s.zCreated[i]) continue;
      bool fvg = s.zKind[i] == 2, bull = s.zBull[i];
      double edge = fvg ? (bull ? s.zTop[i] : s.zBot[i]) : s.zBody[i];
      if(!s.zTouched[i] && (bull ? L5 <= edge : H5 >= edge)) { s.zTouched[i] = true; s.zTouchT[i] = t; }
      if(s.zTouched[i]) { s.zMfe[i] = MathMax(s.zMfe[i], bull ? H5 - edge : edge - L5); if(!s.zReacted[i] && s.zMfe[i] >= 1.5 * A) s.zReacted[i] = true; }
      bool invalid = fvg ? (bull ? C5 < s.zBot[i] : C5 > s.zTop[i]) : (bull ? C5 < s.zWick[i] : C5 > s.zWick[i]);
      bool failed = s.zTouched[i] && !s.zReacted[i] && t - s.zTouchT[i] > 3600;
      bool expired = (!s.zTouched[i] && t - s.zCreated[i] > s.zMaxH[i] * 3600) || t - s.zCreated[i] > 3 * s.zMaxH[i] * 3600;
      if(invalid || failed || expired) DelZone(s, i);
     }
   //--- swept history: keep the last 3 bars
   for(int i = ArraySize(s.swBar) - 1; i >= 0; i--) if(s.swBar[i] <= s.barNo - 3) { Del(s.swBar, i); Del(s.swBsl, i); }
   RbvHook(s, j, mn, live);
  }

//+------------------------------------------------------------------+
//| RBV on bar j (indices into the M5 history)                        |
//+------------------------------------------------------------------+
void RbvHook(CSym *s, int j, int mn, bool live)
  {
   bool flat = mn >= 1015 && mn < 1080;
   //--- pending confirmations: wick broken → drop; confirming close → arm; too late → drop
   for(int k = ArraySize(s.pdBull) - 1; k >= 0; k--)
     {
      bool bull = s.pdBull[k];
      if(bull ? s.l5[j] < s.pdExt[k] : s.h5[j] > s.pdExt[k]) { RemovePend(s, k); continue; }
      if(bull ? s.c5[j] > s.o5[j] : s.c5[j] < s.o5[j]) { if(live) Arm(s, k); RemovePend(s, k); }
      else if(j - s.pdBar[k] >= InpConfirmBars) RemovePend(s, k);
     }
   double A5 = s.atr5[j], rng = s.h5[j] - s.l5[j];
   if(A5 <= 0 || rng <= 0 || flat) return;
   double bodyTop = MathMax(s.o5[j], s.c5[j]), bodyBot = MathMin(s.o5[j], s.c5[j]);
   for(int b = 0; b < 2; b++)
     {
      bool bull = b == 0;
      double wick = bull ? bodyBot - s.l5[j] : s.h5[j] - bodyTop;
      if(wick < InpWickPct * rng || wick < InpWickATR * A5) continue;
      //--- the leg before bar j
      int a = MathMax(0, j - InpLegBars), iX = -1;
      for(int q = a; q < j; q++) if(iX < 0 || (bull ? s.h5[q] > s.h5[iX] : s.l5[q] < s.l5[iX])) iX = q;
      if(iX <= a) continue;
      int iO = a; for(int q = a; q <= iX; q++) if(bull ? s.l5[q] < s.l5[iO] : s.h5[q] > s.h5[iO]) iO = q;
      double X = bull ? s.h5[iX] : s.l5[iX], O = bull ? s.l5[iO] : s.h5[iO], size = MathAbs(X - O);
      bool counter = false; for(int q = iX + 1; q <= j; q++) if(bull ? s.c5[q] < s.o5[q] : s.c5[q] > s.o5[q]) { counter = true; break; }
      if(size < InpLegATR * A5 || !counter) continue;
      double ext = bull ? s.l5[j] : s.h5[j], entry = bull ? bodyBot : bodyTop;
      double fExt = MathAbs(X - ext) / size, fEntry = MathAbs(X - entry) / size;
      if(fExt > 1 || fEntry < InpDiscLo || fExt < InpOteLo) continue;
      //--- trend at the bar's close
      int tr = TrendAt(s, s.t5[j] + 300); if(tr < 0 || (tr == 1) != bull) continue;
      //--- context: sweep in the last 3 bars, OB / FVG under the wick, key open in the wick
      double wLo = bull ? s.l5[j] : bodyTop, wHi = bull ? bodyBot : s.h5[j];
      bool ctx = false;
      for(int k = 0; k < ArraySize(s.swBar) && !ctx; k++) if(s.swBsl[k] == !bull) ctx = true;
      for(int k = 0; k < ArraySize(s.zCe) && !ctx; k++)
        {
         if(s.zBull[k] != bull || s.zCreated[k] > s.t5[j]) continue;
         double zt = s.zKind[k] == 2 ? s.zTop[k] : MathMax(s.zWick[k], s.zBody[k]), zb = s.zKind[k] == 2 ? s.zBot[k] : MathMin(s.zWick[k], s.zBody[k]);
         if(wLo <= zt && wHi >= zb) ctx = true;
        }
      if(!ctx && s.open00 != EMPTY_VALUE && s.open00 >= wLo && s.open00 <= wHi) ctx = true;
      if(!ctx && s.open1000 != EMPTY_VALUE && s.open1000 >= wLo && s.open1000 <= wHi) ctx = true;
      if(!ctx) continue;
      //--- internal high: extreme of the 6 bars before the RB, beyond the entry
      double ih = 0; bool ihOk = false;
      for(int q = MathMax(0, j - InpIhBars); q < j; q++) { double x = bull ? s.h5[q] : s.l5[q]; if(!ihOk) { ih = x; ihOk = true; } else ih = bull ? MathMax(ih, x) : MathMin(ih, x); }
      if(ihOk && (ih - entry) * (bull ? 1 : -1) <= 0) ihOk = false;
      int k = ArraySize(s.pdBull);
      Push(s.pdBull, bull); Push(s.pdBar, j); Push(s.pdExt, ext); Push(s.pdEntry, entry); Push(s.pdStop, ext - (bull ? 1 : -1) * InpStopBuf * A5);
      Push(s.pdAtr, A5); Push(s.pdLegX, X); Push(s.pdIh, ihOk ? ih : 0.0);
      if(bull ? s.c5[j] > s.o5[j] : s.c5[j] < s.o5[j]) { if(live) Arm(s, k); RemovePend(s, k); }
     }
  }
void RemovePend(CSym *s, int k) { Del(s.pdBull, k); Del(s.pdBar, k); Del(s.pdExt, k); Del(s.pdEntry, k); Del(s.pdStop, k); Del(s.pdAtr, k); Del(s.pdLegX, k); Del(s.pdIh, k); }

//+------------------------------------------------------------------+
//| live management of orders and positions (every timer tick)        |
//+------------------------------------------------------------------+
bool PosSelect(ulong ticket) { return PositionSelectByTicket(ticket); }

double ClosedResult(ulong posId)
  {
   if(!HistorySelectByPosition(posId)) return 0;
   double p = 0;
   for(int i = 0; i < HistoryDealsTotal(); i++) { ulong d = HistoryDealGetTicket(i); p += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_SWAP); }
   return p;
  }

void Manage(CSym *s, bool newBar)
  {
   datetime nowSrv = TimeTradeServer(); int mn = NyMin(SrvToNy(nowSrv));
   bool flat = mn >= 1015 && mn < 1080, noFill = mn >= 1005 && mn < 1080;
   double bid = SymbolInfoDouble(s.sym, SYMBOL_BID), ask = SymbolInfoDouble(s.sym, SYMBOL_ASK);
   int dg = (int)SymbolInfoInteger(s.sym, SYMBOL_DIGITS);
   for(int i = ArraySize(s.oState) - 1; i >= 0; i--)
     {
      ulong tk = s.oTicket[i]; bool bull = s.oBull[i];
      if(s.oState[i] == 0)
        {
         if(OrderSelect(tk))
           {
            //--- cancel: 3 bars passed, stop level traded before the fill, no-fill window, news, day blocked
            bool stopHit = bull ? bid <= s.oStop[i] : ask >= s.oStop[i];
            bool cancel = (newBar && s.barNo - s.oArmBar[i] >= InpWaitBars) || stopHit || noFill || g_dayBlocked || g_halted || (newBar && NewsBlocked());
            if(cancel && RateOk()) { if(trade.OrderDelete(tk)) { Log(s.sym + ": лимит снят"); DelOrder(s, i); } }
            continue;
           }
         if(PosSelect(tk))
           {
            s.oState[i] = 1; s.oFillBar[i] = s.barNo; s.oFillTime[i] = (datetime)PositionGetInteger(POSITION_TIME); s.oEntry[i] = PositionGetDouble(POSITION_PRICE_OPEN);
            s.botFills++; g_dayTrades++;
            Log(StringFormat("%s: исполнен @ %.2f", s.sym, s.oEntry[i]));
            // one position per symbol: cancel the other limits; they leave the list on the next tick (OrderSelect fails)
            for(int k = 0; k < ArraySize(s.oState); k++) if(k != i && s.oState[k] == 0) trade.OrderDelete(s.oTicket[k]);
           }
         else DelOrder(s, i);                                                                // cancelled by the broker
         continue;
        }
      //--- open position
      if(!PosSelect(tk))
        {
         double pnl = ClosedResult(tk), r = s.oRisk[i] > 0 ? pnl / s.oRisk[i] : 0;
         if(r < -0.25) s.botLossStreak++; else s.botLossStreak = 0;
         Log(StringFormat("%s: сделка закрыта %+.2fR ($%.2f)", s.sym, r, pnl));
         DelOrder(s, i); continue;
        }
      int held = (int)(TimeCurrent() - s.oFillTime[i]);
      double tgt = s.oEntry[i] + (bull ? 1 : -1) * InpTargetR * s.oR[i];
      //--- TP only after the minimum hold (two-minute rule); if price is already beyond it, take it at market
      if(!s.oTp[i] && held >= InpMinHoldSec && RateOk())
        {
         bool beyond = bull ? bid >= tgt : ask <= tgt;
         if(beyond) { trade.PositionClose(tk); continue; }
         if(trade.PositionModify(tk, PositionGetDouble(POSITION_SL), NormalizeDouble(tgt, dg))) s.oTp[i] = true;
        }
      //--- break-even once the internal high is taken (decided on closed bars, as backtested)
      if(newBar && !s.oBe[i] && s.oIh[i] != 0 && s.n5 > 0)
        {
         int j = s.n5 - 1;
         if((bull ? s.h5[j] > s.oIh[i] : s.l5[j] < s.oIh[i]) && RateOk())
            if(trade.PositionModify(tk, NormalizeDouble(s.oEntry[i], dg), PositionGetDouble(POSITION_TP))) { s.oBe[i] = true; Log(s.sym + ": стоп в безубыток"); }
        }
      //--- time exit: flat by 16:55 NY or after 8 h — never before the minimum hold
      if((flat || (newBar && s.barNo - s.oFillBar[i] >= InpHoldBars)) && held >= InpMinHoldSec && RateOk()) { if(trade.PositionClose(tk)) Log(s.sym + ": выход по времени"); }
     }
  }

//+------------------------------------------------------------------+
//| account guards: daily loss / daily profit / total stop            |
//+------------------------------------------------------------------+
void CloseAll(string why)
  {
   for(int i = OrdersTotal() - 1; i >= 0; i--) { ulong t = OrderGetTicket(i); if(OrderGetInteger(ORDER_MAGIC) == (long)InpMagic) trade.OrderDelete(t); }
   for(int i = PositionsTotal() - 1; i >= 0; i--) { ulong t = PositionGetTicket(i); if(PositionGetInteger(POSITION_MAGIC) == (long)InpMagic) trade.PositionClose(t); }
   Log("ЗАКРЫТО ВСЁ: " + why);
  }

void Guards()
  {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY), bal = AccountInfoDouble(ACCOUNT_BALANCE);
   string pd = NyDate(SrvToNy(TimeTradeServer()) + 7 * 3600);          // prop day resets at 17:00 New York
   if(pd != g_propDay) { g_propDay = pd; g_dayStartEq = MathMax(eq, bal); g_dayBlocked = false; g_dayTrades = 0; Log(StringFormat("новый торговый день, старт $%.2f", g_dayStartEq)); }
   if(!g_halted && eq <= InpStartBalance * (1 - InpTotalStopPct / 100))
     { g_halted = true; GlobalVariableSet("RBV_HALT_" + (string)AccountInfoInteger(ACCOUNT_LOGIN), 1); CloseAll(StringFormat("общая просадка %.1f%% — торговля остановлена до ручного сброса", InpTotalStopPct)); }
   if(g_dayBlocked || g_halted) return;
   double dayPnl = eq - g_dayStartEq;
   if(dayPnl <= -g_dayStartEq * InpDailyStopPct / 100) { g_dayBlocked = true; CloseAll(StringFormat("дневной стоп %.1f%%", InpDailyStopPct)); }
   else if(dayPnl >= g_dayStartEq * InpDailyCapPct / 100) { g_dayBlocked = true; CloseAll(StringFormat("дневной лимит прибыли %.1f%% (консистентность)", InpDailyCapPct)); }
  }

//+------------------------------------------------------------------+
//| lifecycle                                                         |
//+------------------------------------------------------------------+
datetime g_lastBar[];

int OnInit()
  {
   g_srvOff = (long)MathRound((double)(TimeTradeServer() - TimeGMT()) / 1800.0) * 1800;
   if(InpRiskPct > 1.0) { Alert("RBV: риск на сделку больше 1% запрещён правилами — поставь InpRiskPct ≤ 1"); return INIT_PARAMETERS_INCORRECT; }
   g_halted = GlobalVariableCheck("RBV_HALT_" + (string)AccountInfoInteger(ACCOUNT_LOGIN));
   if(g_halted) Log("торговля остановлена ранее (общая просадка). Сброс: удали глобальную переменную RBV_HALT_<login> (F3).");
   if(!MQLInfoInteger(MQL_TESTER) && InpAccountLogin != AccountInfoInteger(ACCOUNT_LOGIN))
      Log("InpAccountLogin не совпадает с этим счётом — советник только считает сигналы и пишет их в журнал, сделок не будет.");
   string parts[]; int n = StringSplit(InpSymbols, ',', parts);
   ArrayResize(g, 0); ArrayResize(g_lastBar, 0);
   for(int i = 0; i < n; i++)
     {
      string sname = parts[i]; StringTrimLeft(sname); StringTrimRight(sname);
      if(!SymbolSelect(sname, true)) { Log("нет символа " + sname + " — проверь InpSymbols"); continue; }
      CSym *s = new CSym; s.Init(sname);
      //--- warm-up: replay ~10 days of history through the engine (no trading)
      MqlRates m5[]; int got = CopyRates(sname, PERIOD_M5, 1, 2900, m5);
      if(got > 0)
        {
         MqlRates m15[]; int g15 = CopyRates(sname, PERIOD_M15, m5[0].time - 300 * 900, m5[0].time - 900, m15);
         for(int k = 0; k < g15; k++) OnM15(s, m15[k]);
         for(int k = 0; k < got; k++) OnM5(s, m5[k], false);
        }
      int k = ArraySize(g); ArrayResize(g, k + 1); g[k] = s; ArrayResize(g_lastBar, k + 1); g_lastBar[k] = got > 0 ? m5[got - 1].time : 0;
      Log(StringFormat("%s: история загружена (%d свечей M5), зон %d, пулов %d", sname, got, ArraySize(s.zCe), ArraySize(s.pPrice)));
     }
   trade.SetExpertMagicNumber(InpMagic); trade.SetDeviationInPoints(30);
   EventSetTimer(1);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) { EventKillTimer(); for(int i = 0; i < ArraySize(g); i++) delete g[i]; }

void Step()
  {
   Guards();
   for(int i = 0; i < ArraySize(g); i++)
     {
      CSym *s = g[i]; bool nb = false;
      datetime last = iTime(s.sym, PERIOD_M5, 1);                        // last CLOSED M5 bar
      if(last > g_lastBar[i])
        {
         MqlRates r[]; int got = CopyRates(s.sym, PERIOD_M5, g_lastBar[i] + 1, last, r);
         for(int k = 0; k < got; k++) OnM5(s, r[k], k == got - 1);      // only the newest bar may place orders
         g_lastBar[i] = last; nb = true;
        }
      Manage(s, nb);
     }
  }
void OnTimer() { Step(); }
void OnTick()  { if(MQLInfoInteger(MQL_TESTER)) Step(); }
//+------------------------------------------------------------------+
