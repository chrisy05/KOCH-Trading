# Current Work

## Erledigt (07.06.2026 — V6 Optimierung: 91.2% WR ERREICHT!)

### V6 Ergebnisse: 85 Konfigurationen ueber 80% WR, 9 ueber 85%!

150 Variationen getestet (50 Refinement + 100 Innovation), davon 94 auf volle 24 Coins expandiert.

### TOP 5 Konfigurationen V6 (balanciert: WR + PnL + DD + Trades):

| # | Config | WR | Trades | PnL | PF | DD | Liqs | Verified |
|---|--------|----|--------|-----|----|----|------|----------|
| 1 | **2x\|42%SL\|55%EM+MTF** | 85.0% | 745 | $3,641 | 1.79 | 33% | 2 | tbd |
| 2 | **2x40+MTF+Slope+Vol** | 84.1% | 857 | $4,110 | 2.09 | 15% | 2 | tbd |
| 3 | **2x40+MTF+50%EM** | 85.7% | 502 | $2,350 | 1.79 | 48% | 2 | 100% |
| 4 | **2x40+MTF+Score5+Max3** | 85.4% | 240 | $1,409 | 1.73 | 35% | 0 | tbd |
| 5 | **2x40+MTF+4Consec** | 84.1% | 668 | $2,601 | 1.84 | 22% | 0 | tbd |

### Zusaetzliche Highlights:
- **91.2% WR**: 2x40+MTF+30%EM — ABER: AvgWin nur $7.53, AvgLoss $-55 → fragil
- **89.4% WR**: 2x40+MTF+BTCstable12h — nur 47 Trades (zu wenig fuer Statistik)
- **85.0% WR + $3,641 PnL**: 2x|42%SL|55%EM+MTF — bester Kompromiss
- **84.1% WR + PF 2.09**: 2x40+MTF+Slope+Vol — bestes Risiko-Profil (DD nur 15%!)

### Schluessel-Erkenntnisse V6:
1. **Kleinere EM (TP) = hoeherer WR** — 30%EM→91%, 40%→87%, 50%→86%, 60%→84%
   - ABER: Zu klein (<40%) macht fragil (AvgWin zu klein vs AvgLoss)
2. **MTF (1H SMA20/50)** bleibt der staerkste Einzelfilter (+3-4pp WR)
3. **42%SL + 55%EM + MTF** ist der neue Sweet Spot: 85% WR, 745 Trades, $3,641 PnL
4. **Slope+Vol Filter** reduziert DD massiv (15%) bei stabilem WR (84%)
5. **Dynamic SL** (tighten over time) funktioniert, bringt ~1-2pp WR, reduziert DD
6. **Correlation Filter** hilft moderat (+0.5pp WR), verhindert Cluster-Verluste
7. **BTC Trend Persistence** (12h stable) maximiert WR (89%), aber zu wenig Trades
8. **Hybrid Leverage** (5x bei Score 7) bringt mehr PnL ohne WR-Verlust

### Dateien:
- Script: optimization_runner_v6.py
- Results: optimization_results_v6.json
- Protokoll: optimization_protocol.md (komplett inkl. V6)
- Alle in: /Trading/agents/signals/

## Fruehere Ergebnisse
- V5b BEST: 2x40+MultiTF = 83.7% WR, $3,910 PnL, PF 2.07
- V4 BEST: 5x|15%SL|100%EM|slope = 72.3% WR, $6,756 PnL

## Liquidation Hunter Backtest (07.06.2026)

### Ergebnis: Standalone NICHT profitabel
- 10 Coins, 30 Tage, 15m, real Binance Klines
- Mode A (Standalone): 107 Trades, 55.1% WR, -$178 PnL — Avg Loss > Avg Win
- Mode B (Kaskaden): 5 Trades, 100% WR, +$48 — zu wenig Trades
- Mode C (MTF): 32 Trades, 50% WR, -$6 — breakeven
- Bester Sweep: 5x|40%SL|3.0 Ratio → 59.7% WR, -$2.86 (fast breakeven)
- **Empfehlung**: Liqui-Ratio als 8. Score im bestehenden KODA-System testen

### Dateien:
- Script: /Trading/agents/signals/liqui_hunter_backtest.py
- Results: /Trading/agents/signals/liqui_hunter_backtest.json
- Protokoll: optimization_protocol.md (Abschnitt "Liquidation Hunter")

## Offen
- ENTSCHEIDUNG: Welchen Modus fuer Live-Bot?
  - **Option G (V6 BEST balanced)**: 2x, 42%SL, 55%EM, +MTF → 85.0% WR, PF 1.79, $3,641
  - **Option H (V6 BEST risk-adj)**: 2x, 40%SL, 60%EM, +MTF+Slope+Vol → 84.1% WR, PF 2.09, DD 15%
  - Option D (V5b): 2x, 40%SL, 60%EM, +MultiTF → 83.7% WR, PF 2.07, $3,910
- Bot neu starten wenn Chris bestaetigt
- Optional: Liqui-Ratio als Add-On Score in V6 Best Config testen
