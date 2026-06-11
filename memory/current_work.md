# Current Work

## Erledigt (07.06.2026)
- Kaskaden-Ampel + TP1/TP2 Trailing in live_bot.py implementiert
  - `get_cascade_signal()` — BTC SMA10/20/50 auf 5 TFs, 5min Cache
  - Cascade Gate in `scan_and_trade`: 0-1 Lights = SKIP, 2 = TP 50% EM, 3-5 = normal 70%
  - TP1/TP2 Trailing in `check_open_trades`: TP1 = 50% close + SL->Entry, dann 3% Trail
  - Alle Closes gehen durch Bybit API (close_position_market)
  - Neue Trade-Felder: tp1_hit, tp1_pnl, peak_price, cascade_lights, cascade_code
  - tp_range_pct Default von 80% auf 70% geaendert (V2K1 Standard)
  - update_stats erkennt TP1+TRAIL und TP1+BE als Wins
  - Syntax verifiziert, Bot NICHT gestartet

## Erledigt (07.06.2026 — Backtest)
- Kaskaden-Ampel 30-Tage Backtest durchgefuehrt (May 10 - Jun 10, 2026)
  - 24 Coins + BTC Cascade, Binance Futures 15m Daten
  - CASCADE: 1011 Trades, 61.6% WR, $4373 PnL, PF 2.31
  - V2 (ohne Cascade): 1262 Trades, 73.3% WR, -$2111 PnL, PF 0.84
  - Cascade filtert 5192 von 6203 Signalen (84%), spart $6484 vs V2
  - Adaptive SL: 2890x getriggert, 1661 Saves
  - Ergebnis: /Trading/agents/signals/kaskaden_backtest.json

## Erledigt (07.06.2026 — Realistic Backtest)
- Kaskaden Backtest mit Budget-Constraints ($1000 Start, $50/Trade, 10x)
  - 766 von 1011 Trades genommen (245 wegen Slot-Limit uebersprungen, 0 wegen Budget)
  - 60.8% WR, Endbalance $2,301, Net PnL $1,301, ROI +130%
  - Peak $2,398, Max DD 50.9%, Max Concurrent 23
  - V2 haette mit gleicher Skalierung $-1,055 gemacht (Totalverlust)
  - Ergebnis: /Trading/agents/signals/kaskaden_backtest_realistic.json

## Erledigt (07.06.2026 — 6-Month Backtest V2)
- Kaskade vs V2 6-Monats-Backtest mit korrekten Timestamps und PnL-Bugfix
  - Timestamps verifiziert: Apr 1 = $68k (korrekt), BTC Dez-Jun Preise validiert
  - PnL Double-Counting Bug gefunden und gefixt (partial_pnl wurde doppelt gezaehlt)
  - KASKADE: 2250 Trades, 62.5% WR, PnL -$648, Final $352 (von $1000)
  - V2: 472 Trades, 54.9% WR, PnL -$963, Final $38 (quasi Totalverlust)
  - Kaskade besser als V2 (+$314 PnL, +7.6pp WR), aber beide negativ
  - Hauptproblem: April -$1581 (54% WR bei Kaskade), Dez -$511
  - Beste Monate: Jan +$795, Feb +$512, Maerz +$236
  - Max DD: Kaskade 95.7%, V2 96.9%
  - Ergebnis: /Trading/agents/signals/kaskaden_6month_backtest_v2.json

## Erledigt (07.06.2026 — Split TF Backtest 15m+4H)
- Kaskade Split-Timeframe Backtest: 15m Signal + 4H Execution
  - Signal auf 15m (stuendlich), TP/SL aus 4H ATR14 (EM = ATR * sqrt(6) * 0.5)
  - 509 Trades, 54.8% WR, PnL -$959, Final $41 (von $1000)
  - Avg Win $12.81 vs Avg Loss -$19.72 (4H ATR bringt groessere TP-Distanz)
  - Avg TP Distance 2.75% (vs ~1.5% bei 15m-only)
  - Problem: WR zu niedrig fuer das Risk/Reward bei 4% SL
  - Dez allein -$556 (241 Trades), danach kein Kapital mehr
  - Self-Verification: 10/10 passed (100%)
  - Vergleich: 15m+4H leicht besser als 15m-only (-$959 vs -$969)
  - 1H-only bleibt bester Run (-$648, 62.5% WR, 2250 Trades)
  - Ergebnis: /Trading/agents/signals/kaskaden_6month_15m_4h.json

## Offen
- Bot neu starten wenn Chris bestaetigt
- Paper Bot V2K1 laeuft parallel zum Vergleich
