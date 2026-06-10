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

## Offen
- Bot neu starten wenn Chris bestaetigt
- Paper Bot V2K1 laeuft parallel zum Vergleich
