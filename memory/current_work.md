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

## Offen
- Bot neu starten wenn Chris bestaetigt
- Paper Bot V2K1 laeuft parallel zum Vergleich
