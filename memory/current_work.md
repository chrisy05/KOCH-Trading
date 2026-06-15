# Current Work

## Erledigt (12.06.2026 — Margin x Leverage Optimization Backtest)

### 10 Configs x 2 Cascade-Levels, 3 Monate, 22 Coins

**Script:** `/Trading/backtest_leverage.py`
**Results:** `/Trading/backtest_leverage_results.json`
**Cache:** `/Trading/backtest_3month_klines_cache.json` (293MB, wiederverwendet)

**Test-Matrix:** 3 Gruppen (A=$500, B=$250, C=$150 Position) x verschiedene Margin/Leverage-Splits

**ERGEBNIS:**

| Cascade | Leverage | Trades | WR% | Net PnL | Fazit |
|---------|----------|--------|-----|---------|-------|
| >=4 TP60% | 10x | 2220 | 62.8% | -$325 | NEGATIV |
| >=4 TP60% | 5x | 2064 | 64.8% | -$193 | NEGATIV |
| >=4 TP60% | 2x | 2052 | 65.2% | -$382 | NEGATIV |
| >=5 TP50% | 10x | 1124 | 64.6% | +$339 | PROFITABEL |
| >=5 TP50% | 5x | 1091 | 66.1% | -$666 | NEGATIV |
| >=5 TP50% | 2x | 1088 | 66.1% | -$1840 | NEGATIV |

**Key Findings:**
1. **Cascade>=4 ist ueber 3 Monate NICHT profitabel** — zu viele Trades, Fees fressen Gewinn
2. **Nur Cascade>=5 + 10x Leverage = profitabel** (+$339 bei $500 Position)
3. **10x ist BESSER als 5x/2x** trotz mehr SL-Hits: Verluste pro Trade sind kleiner (7% vs 14-35%)
4. **Hohere WR bei niedrigem Leverage hilft NICHT** — die wenigen Loser verlieren viel mehr
5. **Position Size skaliert linear** — B1/C1 sind proportional gleich wie A1

**ACHTUNG: Widerspruch zum vorherigen 14-Tage-Backtest!**
- 14 Tage: Cascade>=4 war profitabel ($263 net, 90% WR, 30 Trades)
- 3 Monate: Cascade>=4 ist NEGATIV ($-325, 62.8% WR, 2220 Trades)
- Grund: 14-Tage-Test war Dec 1-14 (Bullenphase), 3 Monate enthaelt Jan (baerisch)

**Empfehlung:**
- Paper: Cas>=5, $50x10x ($500 pos)
- Live Test: Cas>=5, $15x10x ($150 pos, +$102 in 3M)
- Live Scale: Cas>=5, $50x10x ($500 pos, +$339 in 3M)

## Erledigt (14.06.2026 — KODA SE Cascade-5 Signal Bot REWRITE)

### Kompletter Rewrite: paper_bot_koda_se.py

**Was:** KODA SE Bot komplett neu geschrieben als Cascade>=5 Signal Bot mit aktiver TG-Kanal-Posting.

**Config:** C6 aus combo backtest — 10x Leverage, 50% TP EM, 70% Margin SL, Prob>=60%

**Alle Fixes angewendet:**
1. SL ist MARGIN-basiert (70% margin / 10x = 7% price move)
2. Fees in PnL (0.11% RT auf Position Notional)
3. TP von Confirmation Entry (nicht Signal-Preis)
4. BE stop deckt Fees + 0.1% Buffer
5. check_open_trades auf trades_15m + trades_30m
6. 24h Force Close
7. Korrekte Budget-Verwaltung ueber alle TFs

**Signal Channel:** AKTIV — posted zu -1003770314055
**Bot Token:** 8623243424 (KODA Terminal)
**CASCADE_MIN:** 5 (alle 5 TFs muessen aligned sein)
**CONFIRMATION:** 0.3% in 8 Bars vor Entry
**Coins:** 24 Original-Coins
**Log-Name:** [KODA-SE-C5]
**Git push:** paper_trades_koda_se.json (korrekt!)
**RESET:** Signal counter startet bei 1, frische JSON bei Start

**Datei:** `/Trading/dashboard/paper_bot_koda_se.py`
**Status:** Syntax OK, NICHT gestartet. Chris muss Start bestaetigen.

## Erledigt (12.06.2026 — Independent Verification: 36/36 Trades = 100% Match)

### Unabhaengige Verifizierung aller Cascade-Trades gegen raw 1m Binance Klines

**2 Configs verifiziert, ALLE 36 Trades einzeln gegen frische 1m Klines geprueft:**

| Config | Total | Verified OK | Mismatches | Match Rate |
|--------|-------|-------------|------------|------------|
| Cascade>=4 + TP60% + 10x + 70%MSL | 30 | 30 | 0 | 100% |
| Cascade>=5 + TP50% + 10x + 70%MSL | 6 | 6 | 0 | 100% |
| **TOTAL** | **36** | **36** | **0** | **100%** |

**Verifikations-Checks pro Trade:**
- Entry-Preis innerhalb der 1m-Candle (high/low) zum Entry-Zeitpunkt
- TP korrekt erreicht (High fuer LONG, Low fuer SHORT)
- SL korrekt erreicht (Low fuer LONG, High fuer SHORT)
- Reihenfolge TP/SL Events korrekt (welches zuerst)
- Close-Preis stimmt ueberein (<0.05% Toleranz)

**Ergebnis:** Backtest-Engine ist korrekt. Kein einziger Trade weicht ab.

**Config A Details:** 30 Trades, 27 Wins, WR 90.0%, Net $263.24
**Config B Details:** 6 Trades, 6 Wins, WR 100%, Net $49.25

**Dateien:**
- Verification Script: `/Trading/verify_cascade_trades.py`
- Results JSON: `/Trading/verification_cascade_results.json`

## Erledigt (12.06.2026 — Combo-Backtest: 8 Kombinationen + 5x Leverage)

### Combination Filter Backtest: Cascade + TP% + Probability

**8 Kombis getestet (10x Lev) + alle >75% WR auch mit 5x Lev:**

| # | Config | Trades | WR% | Net PnL | MaxDD | PnL/DD |
|---|--------|--------|-----|---------|-------|--------|
| C1 | Cas≥4+TP50%+Prob60% | 30 | 90.0% | $242 | $38 | 6.31 |
| C2 | Cas≥4+TP60%+Prob60% | 30 | 90.0% | $263 | $38 | 6.85 |
| C3 | Cas≥4+TP50%+Prob65% | 30 | 90.0% | $242 | $38 | 6.31 |
| C4 | Cas≥3+TP50%+Prob60% | 70 | 81.4% | $194 | $53 | 3.68 |
| C5 | Cas≥4+TP70%+Prob60% | 29 | 86.2% | $260 | $38 | 6.76 |
| C6 | Cas≥5+TP50%+Prob60% | 6 | 100% | $49 | $0 | inf |
| C7 | Cas≥4+TP50%+Prob70% | 30 | 90.0% | $242 | $38 | 6.31 |
| C8 | Cas≥3+TP50%+Prob70% | 70 | 81.4% | $194 | $53 | 3.68 |

**Key Findings:**
- Cascade≥4 ist der dominante Filter → 90% WR bei ALLEN TP/Prob-Varianten
- TP 60% EM bringt +$21 mehr PnL als TP 50% bei gleicher WR → BEST
- Probability 60/65/70% macht NULL Unterschied wenn Cascade≥4
- 5x Leverage: Gleiche WR, halber PnL, halber DD → PnL/DD Ratio identisch, kein Vorteil
- Cascade≥3 lockert auf 70 Trades/81.4% WR — guter Fallback fuer mehr Trades

**EMPFEHLUNG:** Cas≥4 + TP 60% EM + Prob≥60% (10x) → 90% WR, $263/14d, PnL/DD 6.85

**Dateien:**
- Script: `/Trading/backtest_combo.py`
- Results: `/Trading/backtest_combo_results.json`

## Erledigt (14.06.2026 — paper_bot_cascade4.py erstellt)

### KODA Cascade 4 Paper Bot — Verified Config C2

**Bot:** `paper_bot_cascade4.py` | **Daten:** `paper_trades_cascade4.json` | **Log:** `paper_bot_cascade4.log`

**Config:** 10x Lev, $50/Trade, $1000 Budget, 60% TP EM, 70% Margin SL, Cascade>=4
**Dashboard Name:** "KODA Cascade 4"

**Alle 4 kritischen Fixes aus backtest_confirmation_fixed.py:**
1. SL margin-basiert (70%/10x = 7% Price Move)
2. Fees 0.11% RT in PnL eingerechnet
3. TP von Confirmation-Entry-Preis berechnet (nicht Signal-Preis)
4. BE-Stop deckt Fees + 0.1% Buffer (entry * 1.0021)

**Zusaetzliche Features:**
5. Cascade>=4 Filter (nicht >=2)
6. 24h Force Close ohne TP1
7. check_open_trades iteriert trades_15m + trades_30m
8. Collective Profit Exit (ROI>30% single + sum>=100%)
9. TG Channel DISABLED, nur Drawdown-Alerts an Chris

**Status:** Erstellt, kompiliert OK. Chris muss Start bestaetigen.

## Offen
- Bot starten wenn Chris bestaetigt
- Dashboard Tab "Cascade 4" in paperbot.html hinzufuegen

## Erledigt (14.06.2026 — Fixed Confirmation Backtest: 4 Bugs behoben)

### Backtest mit KORREKTER Mathe: Margin-SL, Fees, TP-Recalc, BE+Fees

**4 kritische Bugs im paper_bot_confirm.py identifiziert und gefixt:**
1. SL war PRICE-basiert (30% Price = nie gefeuert bei 5x). Jetzt MARGIN-basiert.
2. Fees fehlten komplett. Jetzt 0.11% RT eingerechnet.
3. TP wurde von Signal-Preis berechnet, nicht von Confirmation-Entry. Gefixt.
4. BE-Stop deckte Fees nicht. Jetzt entry * (1 + 0.0021).

**Backtest: 24 Coins, 14 Tage, 483k 1m-Candles, 15 Konfigurationen**

| Lev | Margin SL% | Trades | WR% | Net PnL | Fees | Max DD |
|-----|-----------|--------|-----|---------|------|--------|
| 3x  | 40%       | 422    | 62.3% | $279  | $70  | $68    |
| 5x  | 30%       | 473    | 61.5% | $430  | $130 | $123   |
| 5x  | 40%       | 447    | 62.4% | $484  | $123 | $142   |
| 10x | 60%       | 473    | 61.5% | $860  | $260 | $247   |
| 10x | 70%       | 456    | 62.5% | $1106 | $251 | $214   |
| 10x | 30%       | 683    | 49.0% | -$64  | $376 | $769   |

**Erkenntnisse:**
- WR mit Fees: ~60-63% (realistisch vs 87% ohne Fees/korrekte SL)
- 10x/30% Margin SL = 3% Price Move = zu eng, 49% WR, NEGATIV
- 10x/70% = bester absolute PnL ($1106), aber 7% Price SL immer noch vor Liq (9.5%)
- Bester Risk/Reward: 5x/40% ($484 PnL bei nur $142 DD, PnL/DD = 3.4x)
- Fees machen 15-35% des Gross PnL aus — MASSIV unterschaetzt bisher

**Dateien:**
- Script: `/Trading/backtest_confirmation_fixed.py`
- Results: `/Trading/backtest_confirmation_fixed_results.json`

## Offen
- ENTSCHEIDUNG: Welche Config fuer Live-Bot nach diesen realistischen Zahlen?
  - **5x/40% Margin SL**: Sicherste Wahl (PnL/DD = 3.4x, WR 62.4%)
  - **10x/70% Margin SL**: Hoechster PnL aber mehr Risiko (DD $214)
  - paper_bot_confirm.py muss mit korrekter SL/Fee-Logik aktualisiert werden

## Erledigt (13.06.2026 — 1-Minute SL-Backtest: SL% fast irrelevant)

### 1m Klines SL-Backtest: 20 Coins, 14 Tage, 4 SL-Varianten

**Ergebnis:** SL-Prozentsatz macht bei KODA Confirmation praktisch KEINEN Unterschied.

| SL% | Trades | WR% | PnL | Max DD |
|-----|--------|-----|-----|--------|
| 20% | 5348 | 87.9% | $1,310 | -$69 |
| 30% | 5348 | 87.9% | $1,412 | -$28 |
| 42% | 5348 | 87.9% | $1,412 | -$28 |
| 50% | 5348 | 87.9% | $1,412 | -$28 |

**Grund:** Bei 1% TP1 + 2% Trail + BE werden Trades durch TP1/Trail/BE beendet, nicht durch SL. Der SL wird fast nie getroffen. Nur bei 20% gibt es minimale Unterschiede (1 Trade weniger, $100 weniger PnL).

**Empfehlung:** 30% SL reicht (gleiche Performance wie 42/50%, weniger Exposure).

**Dateien:**
- Script: `/Trading/backtest_sl_1min.py`
- Results: `/Trading/backtest_sl_1min_results.json`

## Erledigt (07.06.2026 — KODA Optimal Paper Bot erstellt)

### KODA Optimal Paper Bot — V6 Best Config als Paper Bot

**Bot:** `paper_bot_optimal.py` | **Daten:** `paper_trades_optimal.json`

**Strategy:**
- 2x Leverage, $50/Trade, $1000 Budget, max 20 simultaneous
- SL: 42% PRICE move (LONG: entry * 0.58, SHORT: entry * 1.42) = 84% margin loss
- TP: 55% Expected Move (ATR14 * sqrt(bars_per_day) * 0.5 * 0.55)
- MTF Gate: BTC 1H SMA20 > SMA50 for LONG, SMA20 < SMA50 for SHORT
- Score Gate: >= 4/7 scores aligned
- Min Probability: 60%
- TP1/TP2: 50% close at TP1, SL moves to entry, 3% trail from peak
- Adaptive SL: EMA ribbon width (8/13/21/34) — tighten when narrows
- Cascade Ampel: >= 2 lights required
- Timeframes: 15m (50%) + 30m (50%)
- 24 Coins: GLM, AVAX, KAS, MINA, XRP, FLOW, AXL, CELR, CYS, IOST, CAKE, KAITO, TRX, SUN, GRT, DUSK, BAT, SYN, TON, HBAR, DOT, LTC, LINK, SOL

**Dashboard:** Tab "Optimal" in paperbot.html hinzugefuegt

**Status:** Erstellt, NICHT gestartet. Chris muss Start bestaetigen.

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
