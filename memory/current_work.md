# Current Work

## Erledigt (11.06.2026 — Systematische Strategie-Optimierung)
- 4-Phasen Optimierung durchgefuehrt: V1 Einzeltests, V2 Innovation, V3 Sweep, V4 Realistisch
- KRITISCHER FUND: SL > 9% bei 10x Leverage = Liquidation vor SL!
  - V3 Ergebnisse mit 12-25% SL bei 10x waren UNGUELTIG
  - Maximaler realistischer SL bei 10x: ~8% (80% Margin-Verlust)
  - Fuer breiteren SL: niedrigerer Leverage noetig

### V4 REALISTISCHE Ergebnisse (mit Liquidation-Check):
- **10x Leverage** (max SL ~8%):
  - Bestes: 7%SL + 100%EM + slope = 63.1% WR, $778 PnL, PF 1.03, DD 78%
  - 10x ist generell negativ oder knapp break-even — zu viele Liquidations (43)
  
- **5x Leverage** (max SL ~15%):
  - **BEST OVERALL: 15%SL + 100%EM + slope = 72.3% WR, $6,756 PnL, PF 1.34, DD 37%**
  - Verified 10/10 (100%)
  - Score5 + 15%SL + 100%EM = 71.9% WR, $5,936, PF 1.33
  - 12%SL + 100%EM = 68.5% WR, $2,814, PF 1.15

- **3x Leverage** (max SL ~25%):
  - 25%SL + 80%EM = 77.1% WR, $4,029, PF 1.46 (hoechster PF)
  - 25%SL + 100%EM = 71.2% WR, $3,092, PF 1.35
  - 20%SL + 100%EM = 69.4% WR, $1,934, PF 1.27, 0 Liquidations

### Wichtigste Erkenntnisse:
1. Wider SL = hoehere WR, aber braucht niedrigeren Leverage
2. 100% EM (statt 70%) = hoehere PnL bei jeder SL-Groesse
3. Score >= 4 (7-Score System) filtert schlechte Trades effektiv
4. SMA20 Slope-Filter (Trend-Staerke) verbessert WR um ~2-4pp
5. Bei 5x Leverage mit 15% SL: 72% WR erreichbar, positiver PnL ueber ALLE 6 Monate
6. 80-85% WR ist nur bei 3x Leverage mit 25% SL realistisch, aber Position-Size klein

### Dateien:
- Scripts: optimization_runner.py, _v2.py, _v3.py, _v4.py
- Results: optimization_results.json, _v2.json, _v3.json, _v4.json
- Protokoll: optimization_protocol.md (komplett)
- Alle in: /Trading/agents/signals/

## Erledigt (07.06.2026)
- Kaskaden-Ampel + TP1/TP2 Trailing in live_bot.py implementiert
- Diverse Backtests (30d, 6-Monat, Split-TF) durchgefuehrt

## Offen
- ENTSCHEIDUNG: Welchen Leverage/SL Modus fuer Live-Bot?
  - Option A: 5x, 15%SL, 100%EM, +slope → 72% WR, bestes PnL
  - Option B: 3x, 25%SL, 80%EM → 77% WR, bester PF, kleinere Positionen
  - Option C: 10x bleiben, 7%SL, 100%EM, +slope → 63% WR, marginaler Profit
- Bot neu starten wenn Chris bestaetigt
- Paper Bot V2K1 laeuft parallel zum Vergleich
