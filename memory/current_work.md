# Current Work

## Erledigt (07.06.2026 — V5 Optimierung: 80%+ WR ERREICHT)

### V5/V5b Ergebnisse: 25 Konfigurationen ueber 80% WR!

10 neue Ideen getestet auf 24 Coins, 6 Monate (Dez 2025 - Jun 2026):
1. Multi-TF Confirmation (1H SMA20/50 muss mit 15m Signal uebereinstimmen)
2. Volume Confirmation (Bar-Volume > Durchschnitt)
3. Consecutive Candle Filter (2-3 Kerzen in Richtung)
4. Time-of-Day Filter (beste Stunden)
5. Dynamic Coin Exclusion (schlechte Coins automatisch entfernen)
6. Pullback Entry (Einstieg nach Ruecksetzer)
7. Triple Gate (Score + BTC + Volume + Slope)
8. Lower Leverage + Wider SL (2x mit 40% SL)
9. Max Positions Limit (3-5 gleichzeitig)
10. Trail-Only Exits (kein fester TP)

### TOP 5 Konfigurationen (alle 80%+, alle verifiziert):

| # | Config | WR | Trades | PnL | PF | DD | Liqs | Verified |
|---|--------|----|--------|-----|----|----|------|----------|
| 1 | **2x\|40%SL\|60%EM + Max5Pos** | 84.5% | 335 | $1,634 | 1.49 | 25% | 1 | 90% |
| 2 | **2x\|40%SL\|60%EM + Max3Pos** | 83.7% | 233 | $1,828 | 2.03 | 26% | 0 | 100% |
| 3 | **2x\|40%SL\|60%EM + MultiTF** | 83.7% | 663 | $3,910 | 2.07 | 30% | 2 | 100% |
| 4 | **2x\|40%SL\|60%EM + Score5+MTF** | 83.6% | 610 | $3,299 | 1.92 | 36% | 0 | 100% |
| 5 | **2x\|40%SL\|60%EM + Cooldown48** | 83.5% | 770 | $3,800 | 1.71 | 21% | 4 | 90% |

### Beste Kombination Trades+PnL+WR:
- **2x|40%SL|60%EM + MultiTF**: 83.7% WR, 663 Trades, $3,910 PnL, PF 2.07, DD 30%, 100% verified
  - Positiv in JEDEM Monat ausser Jan (-$59)
  - Nur 2 Liquidations in 6 Monaten

### Beste Kombination WR+DD (Risiko-adjustiert):
- **3x+MTF+Vol+Consec+Slope**: 79.0% WR, 1035 Trades, $4,222 PnL, PF 1.64, DD nur 10%!
  - Knapp unter 80%, aber exzellentes Risikoprofil
  - Combo+60%EM Variante: 82.5% WR, DD 14%, $2,823 PnL

### Schluessel-Erkenntnisse V5:
1. **2x Leverage + 40% SL** ist der Sweet Spot fuer hohe WR (80-85%)
   - Liq bei ~45%, SL bei 40% = sicherer Abstand
   - Position-Size $500 (2x * $250 margin) = aehnlich wie 5x/$100
2. **Multi-TF Confirmation** (1H SMA-Trend) ist der staerkste Einzelfilter (+3-4pp WR)
3. **60% EM** (kleinere TP) erhoet WR um 2-3pp vs 80% EM
4. **Max-Position-Limits** (3-5) erhoehen WR um 3-5pp durch Qualitaetsselektion
5. **Trail-Only**: NICHT funktioniert (30-35% WR — zu viele Whipsaws auf 15m)
6. **Time-of-Day**: Session-Filter allein bringt wenig, da Trades stark reduziert werden
7. **Consecutive Candles**: 2 Kerzen hilft (+0.5-2pp), 3 Kerzen etwas besser

### Dateien:
- Scripts: optimization_runner_v5.py, _v5b.py
- Results: optimization_results_v5.json, _v5b.json
- Protokoll: optimization_protocol.md (komplett inkl. V5/V5b)
- Alle in: /Trading/agents/signals/

## Fruehere Ergebnisse (11.06.2026 — V1-V4)
- V4 BEST: 5x|15%SL|100%EM|slope = 72.3% WR, $6,756 PnL
- V4 BEST WR: 3x|25%SL|80%EM = 77.1% WR, $4,029 PnL

## Offen
- ENTSCHEIDUNG: Welchen Modus fuer Live-Bot?
  - **Option D (NEU BEST)**: 2x, 40%SL, 60%EM, +MultiTF → 83.7% WR, PF 2.07, $3,910
  - **Option E (NEU BEST WR)**: 2x, 40%SL, 60%EM, +Max5 → 84.5% WR, PF 1.49, $1,634
  - **Option F (Risk-adj.)**: 3x+MTF+Vol+Consec+Slope, 60%EM → 82.5% WR, DD 14%, PF 1.51
  - Option A (alt): 5x, 15%SL, 100%EM, +slope → 72% WR, bestes PnL ($6,756)
  - Option B (alt): 3x, 25%SL, 80%EM → 77% WR, PF 1.46
- Bot neu starten wenn Chris bestaetigt
- Paper Bot V2K1 laeuft parallel zum Vergleich
