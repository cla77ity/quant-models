//+------------------------------------------------------------------+
//| ModelE.mq5 — executor of research Model E (risk parity + SMA200) |
//| Reads Common\Files\ModelE\targets.csv written by model_e.js:     |
//|   asOf,YYYY-MM-DD / generated,ISO / KEY,SYMBOL,WEIGHT            |
//| WEIGHT = target notional as a fraction of account equity (long). |
//| Sizes lots from the broker's own specs and rebalances once per   |
//| new asOf, retrying symbols whose market is closed.               |
//| Safety: DEMO accounts only, stale files ignored, gross ≤ 2.5x,   |
//| 25% no-trade band, touches only its own positions (magic).       |
//+------------------------------------------------------------------+
#property copyright "research / Model E"
#property version   "1.00"
#property description "Model E executor. DEMO accounts only. Reads ModelE\\targets.csv from the common Files folder."

#include <Trade\Trade.mqh>

input string InpFile       = "ModelE\\targets.csv"; // targets file (common Files folder)
input string InpSuffix     = "";                     // symbol suffix, if HeroFX uses one (e.g. ".r")
input double InpBand       = 0.25;                   // skip resizes smaller than this share of the position
input double InpMaxGross   = 2.5;                    // hard cap: total notional / equity
input int    InpMaxAgeDays = 4;                      // ignore a targets file older than this (calendar days)
input ulong  InpMagic      = 20261111;               // marks this EA's positions
input long   InpDemoLogin  = 0;                      // a broker DEMO login that MT5 reports as REAL (HeroFX demo on its live server); 0 = off

CTrade   trade;
string   g_asOf    = "";   // asOf being executed now
string   g_pending = "";   // ";SYM;SYM;" still to do for g_asOf
string   g_noted   = "";   // ";SYM;" market-closed already logged (avoid spam)
int      g_fails[];        // failures per symbol index
string   g_syms[];
double   g_w[];
string   g_lastMsg = "";

const string GV_LAST = "ModelE_lastExec";

//+------------------------------------------------------------------+
void Log(const string msg)
  {
   string line = TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + ";" + msg;
   Print("ModelE: ", msg);
   int h = FileOpen("ModelE\\log.csv", FILE_COMMON | FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_SHARE_READ, '\t', CP_UTF8);
   if(h != INVALID_HANDLE)
     {
      FileSeek(h, 0, SEEK_END);
      FileWriteString(h, line + "\r\n");
      FileClose(h);
     }
  }
void LogOnce(const string msg) { if(msg != g_lastMsg) { Log(msg); g_lastMsg = msg; } }

// market closed / no prices right now: try again on the next timer tick
bool RetryLater(const uint rc) { return rc == TRADE_RETCODE_MARKET_CLOSED || rc == TRADE_RETCODE_PRICE_OFF || rc == TRADE_RETCODE_REQUOTE || rc == TRADE_RETCODE_PRICE_CHANGED || rc == TRADE_RETCODE_CONNECTION; }

double DateNum(const string d) { string s = d; StringReplace(s, "-", ""); return StringToDouble(s); }   // 2026-11-09 -> 20261109

//+------------------------------------------------------------------+
//| find the broker's name for a market: the configured name first,  |
//| then common aliases, allowing a short suffix (US500.r, GER40m)   |
//+------------------------------------------------------------------+
string Aliases(const string key)
  {
   if(key == "US500")  return "US500,SP500,SPX500,US500CASH,SPX,SNP500";
   if(key == "US100")  return "NAS100,US100,ND100,USTEC,NDX100,NQ100,USTECH,NASDAQ100,NAS";
   if(key == "US30")   return "US30,DJ30,WS30,DOW30,DJI30,USA30";
   if(key == "US2000") return "US2000,RUS2000,RTY2000,RUSSELL2000,US2K,RTY";
   if(key == "GER40")  return "GER40,DE40,DAX40,GER30,DE30,DAX,GERMANY40";
   if(key == "JP225")  return "JPN225,JP225,NI225,NIKKEI225,JPX225,JAP225,NIK225";
   if(key == "UK100")  return "UK100,FT100,FTSE100,GB100,FTSE";
   if(key == "XAU")    return "XAUUSD,GOLD";
   if(key == "XAG")    return "XAGUSD,SILVER";
   return key;
  }
bool Tradable(const string s) { return SymbolInfoInteger(s, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_DISABLED; }
string Resolve(const string key, const string configured)
  {
   if(SymbolSelect(configured + InpSuffix, true) && Tradable(configured + InpSuffix)) return configured + InpSuffix;
   string al[]; int na = StringSplit(Aliases(key), ',', al);
   int total = SymbolsTotal(false);
   for(int pass = 0; pass < 2; pass++)                       // pass 0: exact name, pass 1: alias + short suffix
      for(int a = 0; a < na; a++)
         for(int i = 0; i < total; i++)
           {
            string s = SymbolName(i, false), u = s;
            StringToUpper(u); StringReplace(u, "_", ""); StringReplace(u, "-", ""); StringReplace(u, ".", "");
            bool hit = pass == 0 ? u == al[a] : (StringFind(u, al[a]) == 0 && StringLen(u) - StringLen(al[a]) <= 4);
            if(hit && SymbolSelect(s, true) && Tradable(s)) return s;
           }
   return configured + InpSuffix;                             // not found: Rebalance() logs it and skips
  }

// broker specs of every target market, for the research side (contract size, lots, swaps): ModelE\symbols.csv
void DumpSpecs()
  {
   int h = FileOpen("ModelE\\symbols.csv", FILE_COMMON | FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, "symbol,contract_size,vol_min,vol_step,tick_value,tick_size,bid,profit_ccy,swap_mode,swap_long,swap_short,swap_3day,margin_initial_1lot,account_leverage\r\n");
   for(int i = 0; i < ArraySize(g_syms); i++)
     {
      string s = g_syms[i];
      if(!SymbolSelect(s, true)) { FileWriteString(h, s + ",NOT_FOUND\r\n"); continue; }
      double m = 0, bid = SymbolInfoDouble(s, SYMBOL_BID);
      if(!OrderCalcMargin(ORDER_TYPE_BUY, s, 1.0, SymbolInfoDouble(s, SYMBOL_ASK), m)) m = -1;
      FileWriteString(h, StringFormat("%s,%.2f,%.2f,%.2f,%.6f,%.6f,%.5f,%s,%d,%.5f,%.5f,%d,%.2f,%d\r\n", s,
         SymbolInfoDouble(s, SYMBOL_TRADE_CONTRACT_SIZE), SymbolInfoDouble(s, SYMBOL_VOLUME_MIN), SymbolInfoDouble(s, SYMBOL_VOLUME_STEP),
         SymbolInfoDouble(s, SYMBOL_TRADE_TICK_VALUE), SymbolInfoDouble(s, SYMBOL_TRADE_TICK_SIZE), bid, SymbolInfoString(s, SYMBOL_CURRENCY_PROFIT),
         (int)SymbolInfoInteger(s, SYMBOL_SWAP_MODE), SymbolInfoDouble(s, SYMBOL_SWAP_LONG), SymbolInfoDouble(s, SYMBOL_SWAP_SHORT),
         (int)SymbolInfoInteger(s, SYMBOL_SWAP_ROLLOVER3DAYS), m, (int)AccountInfoInteger(ACCOUNT_LEVERAGE)));
     }
   FileClose(h);
  }

//+------------------------------------------------------------------+
//| read targets; returns false if missing / malformed               |
//+------------------------------------------------------------------+
bool ReadTargets(string &asOf)
  {
   int h = FileOpen(InpFile, FILE_COMMON | FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return false;
   ArrayResize(g_syms, 0); ArrayResize(g_w, 0);
   asOf = "";
   while(!FileIsEnding(h))
     {
      string line = FileReadString(h);
      StringTrimLeft(line); StringTrimRight(line);
      if(line == "") continue;
      string p[];
      int n = StringSplit(line, ',', p);
      if(n >= 2 && p[0] == "asOf") { asOf = p[1]; continue; }
      if(n >= 2 && p[0] == "generated") continue;
      if(n >= 3)
        {
         double w = StringToDouble(p[2]);
         if(w < 0 || w > InpMaxGross) { FileClose(h); Log("ошибка в файле целей: вес " + p[2] + " у " + p[1]); return false; }
         int k = ArraySize(g_syms);
         ArrayResize(g_syms, k + 1); ArrayResize(g_w, k + 1);
         g_syms[k] = Resolve(p[0], p[1]); g_w[k] = w;
        }
     }
   FileClose(h);
   return asOf != "" && ArraySize(g_syms) > 0;
  }

//+------------------------------------------------------------------+
//| this EA's net lots in a symbol (buys − sells)                    |
//+------------------------------------------------------------------+
double MyLots(const string sym)
  {
   double v = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetString(POSITION_SYMBOL) != sym || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      double vol = PositionGetDouble(POSITION_VOLUME);
      v += PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? vol : -vol;
     }
   return v;
  }

double Norm(const string sym, double lots)
  {
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   if(step <= 0) step = 0.01;
   return NormalizeDouble(MathFloor(lots / step + 1e-9) * step, 2);
  }

// value of 1 lot in account currency (handles EUR / GBP / JPY quoted CFDs via tick value)
double LotValue(const string sym)
  {
   double px = SymbolInfoDouble(sym, SYMBOL_BID), tv = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE), ts = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   if(px <= 0 || tv <= 0 || ts <= 0) return 0;
   return px * tv / ts;
  }

//+------------------------------------------------------------------+
//| buy (delta>0) or reduce this EA's long (delta<0); true when done |
//| returns -1 = market closed / no price (retry later), 0 = failed, |
//| 1 = done                                                         |
//+------------------------------------------------------------------+
int Execute(const string sym, double delta)
  {
   trade.SetTypeFillingBySymbol(sym);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(vmax <= 0) vmax = 100;
   if(delta > 0)
     {
      while(delta > 1e-9)
        {
         double v = Norm(sym, MathMin(delta, vmax));
         if(v <= 0) break;
         if(!trade.Buy(v, sym, 0, 0, 0, "ModelE"))
           {
            uint rc = trade.ResultRetcode();
            if(RetryLater(rc)) return -1;
            Log(sym + ": ошибка покупки " + DoubleToString(v, 2) + " лота, код " + (string)rc + " " + trade.ResultRetcodeDescription());
            return 0;
           }
         Log(sym + ": КУПЛЕНО " + DoubleToString(v, 2) + " лота по " + DoubleToString(trade.ResultPrice(), (int)SymbolInfoInteger(sym, SYMBOL_DIGITS)));
         delta -= v;
        }
      return 1;
     }
   // reduce: netting accounts sell, hedging accounts close own buy positions (partially if needed)
   double need = -delta;
   bool netting = AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING;
   if(netting)
     {
      double v = Norm(sym, need);
      if(v <= 0) return 1;
      if(!trade.Sell(v, sym, 0, 0, 0, "ModelE"))
        {
         uint rc = trade.ResultRetcode();
         if(RetryLater(rc)) return -1;
         Log(sym + ": ошибка продажи, код " + (string)rc + " " + trade.ResultRetcodeDescription());
         return 0;
        }
      Log(sym + ": ПРОДАНО " + DoubleToString(v, 2) + " лота");
      return 1;
     }
   for(int i = PositionsTotal() - 1; i >= 0 && need > 1e-9; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || PositionGetString(POSITION_SYMBOL) != sym || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if(PositionGetInteger(POSITION_TYPE) != POSITION_TYPE_BUY) continue;
      double vol = PositionGetDouble(POSITION_VOLUME), v = Norm(sym, MathMin(vol, need));
      if(v <= 0) break;
      bool ok = v >= vol - 1e-9 ? trade.PositionClose(t) : trade.PositionClosePartial(t, v);
      if(!ok)
        {
         uint rc = trade.ResultRetcode();
         if(RetryLater(rc)) return -1;
         Log(sym + ": ошибка закрытия, код " + (string)rc + " " + trade.ResultRetcodeDescription());
         return 0;
        }
      Log(sym + ": ЗАКРЫТО " + DoubleToString(v, 2) + " лота");
      need -= v;
     }
   return 1;
  }

//+------------------------------------------------------------------+
void Rebalance()
  {
   // no decisions on cached data: wait until the terminal is logged in and the account is loaded
   if(!TerminalInfoInteger(TERMINAL_CONNECTED) || AccountInfoDouble(ACCOUNT_EQUITY) <= 0) { LogOnce("нет подключения к серверу брокера (или баланс не загружен) — жду"); return; }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED)) { LogOnce("включи кнопку «Алготрейдинг» в MT5 и разрешение на торговлю в настройках советника"); return; }
   string asOf;
   if(!ReadTargets(asOf)) { LogOnce("нет файла целей " + InpFile + " — запусти model_e.js"); return; }
   double lastExec = GlobalVariableCheck(GV_LAST) ? GlobalVariableGet(GV_LAST) : 0;
   if(DateNum(asOf) <= lastExec) return;                                         // already executed
   string d = asOf; StringReplace(d, "-", ".");
   if(TimeCurrent() - StringToTime(d) > (datetime)InpMaxAgeDays * 86400) { LogOnce("файл целей устарел (" + asOf + ") — model_e.js не запускался, ничего не делаю"); return; }

   int n = ArraySize(g_syms);
   if(asOf != g_asOf)
     {
      g_asOf = asOf; g_pending = ";"; g_noted = ";";
      ArrayResize(g_fails, n); ArrayInitialize(g_fails, 0);
      for(int i = 0; i < n; i++) g_pending += g_syms[i] + ";";
      double sum = 0; for(int i = 0; i < n; i++) sum += g_w[i];
      string map = ""; for(int i = 0; i < n; i++) map += " " + g_syms[i];
      Log("новые цели на " + asOf + ", сумма весов " + DoubleToString(sum, 3) + ", баланс " + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 0) + "; символы:" + map);
      DumpSpecs();
     }
   double sum = 0; for(int i = 0; i < n; i++) sum += g_w[i];
   double scale = sum > InpMaxGross ? InpMaxGross / sum : 1.0;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   for(int i = 0; i < n; i++)
     {
      string sym = g_syms[i];
      if(StringFind(g_pending, ";" + sym + ";") < 0) continue;
      if(!SymbolSelect(sym, true)) { Log(sym + ": такого символа нет у брокера — пропускаю (проверь имя в herofx.json или суффикс)"); StringReplace(g_pending, ";" + sym + ";", ";"); continue; }
      if(SymbolInfoInteger(sym, SYMBOL_TRADE_MODE) == SYMBOL_TRADE_MODE_DISABLED) { Log(sym + ": торговля запрещена брокером — пропускаю"); StringReplace(g_pending, ";" + sym + ";", ";"); continue; }
      double lv = LotValue(sym);
      if(lv <= 0) continue;                                                     // no quotes yet: retry
      double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
      double target = Norm(sym, g_w[i] * scale * equity / lv);
      if(target < vmin) target = 0;
      double have = MyLots(sym), delta = NormalizeDouble(target - have, 2);
      bool small = MathAbs(delta) < SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP) / 2 || (have > 0 && target > 0 && MathAbs(delta) < InpBand * have);
      if(small) { StringReplace(g_pending, ";" + sym + ";", ";"); continue; }
      int r = Execute(sym, delta);
      if(r == 1) StringReplace(g_pending, ";" + sym + ";", ";");
      else if(r == -1) { if(StringFind(g_noted, ";" + sym + ";") < 0) { Log(sym + ": рынок закрыт, повторю, когда откроется (нужно " + DoubleToString(target, 2) + " лота, сейчас " + DoubleToString(have, 2) + ")"); g_noted += sym + ";"; } }
      else if(++g_fails[i] >= 5) { Log(sym + ": 5 ошибок подряд — пропускаю до следующих целей"); StringReplace(g_pending, ";" + sym + ";", ";"); }
     }
   if(g_pending == ";")
     {
      GlobalVariableSet(GV_LAST, DateNum(asOf));
      Log("цели на " + asOf + " исполнены");
     }
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   // demo and contest accounts hold virtual money; real accounts are refused. HeroFX runs demo accounts on its live server,
   // so MT5 reports them as REAL: only the one login the user confirmed as a HeroFX demo (InpDemoLogin) is let through
   long mode = AccountInfoInteger(ACCOUNT_TRADE_MODE);
   bool knownDemo = InpDemoLogin > 0 && AccountInfoInteger(ACCOUNT_LOGIN) == InpDemoLogin && StringFind(AccountInfoString(ACCOUNT_SERVER), "HeroFX") == 0;
   if(mode != ACCOUNT_TRADE_MODE_DEMO && mode != ACCOUNT_TRADE_MODE_CONTEST && !knownDemo)
     {
      Alert("ModelE: счёт " + (string)AccountInfoInteger(ACCOUNT_LOGIN) + " — РЕАЛЬНЫЙ (" + AccountInfoString(ACCOUNT_SERVER) + "). Советник работает только на демо или конкурсном счёте и отключён.");
      return INIT_FAILED;
     }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(100);
   EventSetTimer(60);
   Log(string(mode == ACCOUNT_TRADE_MODE_CONTEST ? "запущен на конкурсном счёте " : "запущен на демо-счёте ") + (string)AccountInfoInteger(ACCOUNT_LOGIN) + ", баланс " + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 0) + " " + AccountInfoString(ACCOUNT_CURRENCY) + ", плечо 1:" + (string)AccountInfoInteger(ACCOUNT_LEVERAGE));
   string a;
   if(TerminalInfoInteger(TERMINAL_CONNECTED) && AccountInfoDouble(ACCOUNT_EQUITY) > 0 && ReadTargets(a)) { DumpSpecs(); string map = ""; for(int i = 0; i < ArraySize(g_syms); i++) map += " " + g_syms[i]; Log("символы HeroFX:" + map + " (спецификации в ModelE\\symbols.csv)"); }
   Rebalance();
   return INIT_SUCCEEDED;
  }
void OnDeinit(const int reason) { EventKillTimer(); }
void OnTimer() { Rebalance(); }
//+------------------------------------------------------------------+
