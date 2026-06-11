# Current Work

## Erledigt (07.06.2026 — 50% Verification: 373 Trades gegen 1m Klines)

### 50% Verification: 2x|42%SL|55%EM+MTF — 373 von 745 Trades

**ERGEBNIS: CONFIRMED WITH HIGH CONFIDENCE**

1. **Re-Run reproduziert exakt**: 745 Trades, 85.0% WR, $3,640.70 PnL
2. **373 Trades gegen 1m Binance Klines verifiziert** (proportional nach Exit-Typ, gruppiert nach Coin):
   - 352 conclusive (21 EOD/inconclusive)
   - **327/352 Matches (92.9%)**
   - **0 optimistische Mismatches** (BT sagt Win, 1m sagt Loss = KEINE)
   - **25 konservative Mismatches** (BT sagt Loss, 1m sagt Win = BT ist pessimistisch)
   - Alle 25 konservativen: BT zeigt -$10.15 SL, aber 1m zeigt TP2/Trail
   - Grund: 15m Bars verpassen intra-bar TP1-Hits die 1m sieht
3. **Wichtige Erkenntnis**: 85 Trades exit als "SL" mit positivem PnL ($0.63-$10.14)
   - Das ist korrekt: TP1 getroffen (halbe Position geschlossen), dann Rest bei Breakeven-SL
   - Diese sind WINS trotz "SL" Label
4. **BT WR in Sample: 85.0% — 1m-adjusted WR: ~97%**
   - Backtest ist konservativ, nicht optimistisch

Reports:
- 50% Verification: `/Trading/agents/signals/best_config_verification_50pct.json`
- 20-Trade Verification: `/Trading/agents/signals/best_config_verification.json`
- Scripts: `verify_best_config.py`, `verify_best_config_50pct.py`

## Erledigt (07.06.2026 — Best Config VERIFIZIERT — 20 Trades)

### V6 Best Config Verification: 2x|42%SL|55%EM+MTF (Erstverifizierung)

Report: `/Trading/agents/signals/best_config_verification.json`
Script: `/Trading/agents/signals/verify_best_config.py`

## Erledigt (07.06.2026 — V7 Optimierung: Liqui-Ratio + Novel Methods)

### V7 Ergebnisse: 65 Konfigurationen getestet, 48 expanded auf 24 Coins

Zwei Aufgaben:
1. Liqui-Ratio (BigBeluga Pivot-Logik) als 8. Score in V6 Best Configs
2. Neue intelligente Methoden: Funding Rate, Candle Patterns, Cross-Coin Momentum, BB Squeeze, RSI Filter, Time-Exit, Partial Sizing

### V7 Kontrollen bestaetigt:
- BaseA (42%SL|55%EM+MTF): 85.0% WR, 745t, $3,641 PnL — identisch zu V6
- BaseB (40%SL+MTF+Slope+Vol): 84.1% WR, 857t, $4,110 PnL — identisch zu V6

### TASK 1: Liqui-Ratio als 8. Score

| Config | WR | Trades | PnL | PF | DD | Bewertung |
|--------|----|--------|-----|----|----|-----------|
| BaseA+LiquiReq(s>=6) | **86.5%** | 141 | $720 | 2.19 | 25% | **+1.5pp WR!** Aber wenig Trades |
| BaseA+LiquiMand(lb=200) | 85.3% | 190 | $-21 | 0.99 | 42% | +0.3pp aber PnL negativ |
| BaseA+LiquiMand(L<0.7,S>1.5) | 84.4% | 353 | $721 | 1.29 | 21% | Guter Trade-Count |
| BaseB+LiquiMand(L<0.7,S>1.5) | 78.9% | 147 | $-108 | 0.93 | 26% | Schadet BaseB |
| BaseB+LiquiMand(L<0.5,S>2.0) | 69.3% | 75 | $-688 | 0.49 | 48% | Deutlich schlechter |

**Erkenntnis**: Liqui-Ratio hilft NUR als selektiver Zusatzfilter bei BaseA (Score>=6 + Liqui confirm → 86.5% WR).
Bei BaseB (das bereits Slope+Vol hat) schadet es — zu restriktiv, filtert gute Trades raus.
"Bonus"-Modus hat NULL Effekt (Liqui bestaetigt fast nie die Schwellwerte bei trend-folgenden Setups).

### TASK 2: Neue Methoden — Ergebnisse

**Methoden die WR BEIBEHALTEN (>=84%):**
| Methode | Config | WR | Trades | PnL | Ergebnis |
|---------|--------|----|--------|-----|----------|
| Funding Confirm | BaseB+FundConfirm | 84.2% | 671 | $2,482 | +0.1pp, weniger Trades |
| Candle Required | BaseA+CandleReq | 84.4% | 481 | $1,615 | Filtert ~35% Trades |
| Partial Sizing | BaseA+Partial | 84.7% | 452 | $1,670 | WR gleich, PnL sinkt |
| Candle Required | BaseB+CandleReq | 83.1% | 527 | $2,417 | PF 2.06, DD 18% |

**Methoden die SCHADEN:**
- **Funding Contrarian**: Zu wenig Trades (Funding selten extrem genug)
- **RSI Contrarian**: NULL Trades (RSI Extreme + Trend-Scores widersprechen sich)
- **Time-based Exit**: WR sinkt auf 58-70% (schliesst profitable Trades zu frueh)
- **BB Squeeze Required**: WR sinkt auf 77-84% bei wenigen Trades
- **Kitchen Sink (alles zusammen)**: 70.8% WR — jeder Filter allein ok, zusammen zu restriktiv

**Methoden die NEUTRAL sind:**
- BB Squeeze Bonus: 0 Effekt (Squeeze trifft selten gleichzeitig mit Signal)
- Candle Bonus: 0 Effekt (Pattern triggert nicht haeufig genug fuer Score-Erhoehung)
- Liqui Bonus: 0 Effekt (Liqui-Ratio selten unter Schwellwert bei Trend-Setups)

**Cross-Coin Momentum: Vielversprechend!**
- BaseB+CrossCoin(t=5): 84.0% WR, 406t, $1,540 — weniger Trades aber stabil
- Idee: Nur traden wenn >=5 Coins gleiche Richtung = Marktbreite bestaetigt

### V7 Schluessel-Erkenntnisse:
1. **V6 Configs sind bereits nahe am Optimum** — keine Methode bringt >1.5pp WR
2. **Liqui-Ratio + Score>=6 bester Kandidat** (+1.5pp auf 86.5%), aber nur 141 Trades
3. **Funding Rate confirming** minimal positiv (+0.1pp), reduziert aber Trade-Count
4. **Time-based Exit SCHADET** — schneidet profitable Trailing-Trades ab
5. **Candle Patterns als Required-Filter** reduziert Trades bei stabiler WR
6. **"Bonus"-Modi haben fast NULL Effekt** — Schwellwerte werden zu selten erreicht
7. **BaseB (Slope+Vol) ist bereits so selektiv** dass weitere Filter schaden

### Dateien:
- Script: optimization_runner_v7.py
- Results: optimization_results_v7.json
- Protokoll: optimization_protocol.md (V7 Abschnitt)
- Alle in: /Trading/agents/signals/

## V6 Ergebnisse (Referenz)
- TOP: 2x|42%SL|55%EM+MTF = 85.0% WR, 745t, $3,641 PnL, PF 1.79
- TOP: 2x40+MTF+Slope+Vol = 84.1% WR, 857t, $4,110 PnL, PF 2.09
- 85 von 150 Configs erreichten 80%+ WR

## Fruehere Ergebnisse
- V5b BEST: 2x40+MultiTF = 83.7% WR, $3,910 PnL, PF 2.07
- V4 BEST: 5x|15%SL|100%EM|slope = 72.3% WR, $6,756 PnL

## Offen
- ENTSCHEIDUNG: Welchen Modus fuer Live-Bot?
  - **Option G (V6 BEST balanced)**: 2x, 42%SL, 55%EM, +MTF → 85.0% WR, PF 1.79, $3,641
  - **Option H (V6 BEST risk-adj)**: 2x, 40%SL, 60%EM, +MTF+Slope+Vol → 84.1% WR, PF 2.09, DD 15%
  - **Option I (V7 selective)**: BaseA + Liqui-Score>=6 → 86.5% WR, PF 2.19, aber nur 141 Trades
- Bot neu starten wenn Chris bestaetigt
