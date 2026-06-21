# Current Work

## Erledigt (12.06.2026 — EE2 Top 5 TP Strategy Comparison)

### 100% TP1 vs 50% TP1 + 1.5% Price Trailing — Trail VERLIERT

**Script:** `/Trading/ee2_top5_tp_comparison.py`
**Results:** `/Trading/ee2_top5_tp_comparison.json`

**Setup:** 90 Tage, 30m Signale, 1m Walk-Forward, $500 Margin, 15x Leverage, 0.11% Fees, BTC-confirmed only

**Strat A:** 100% close at TP1 (+15% margin = +1% price)
**Strat B:** 50% close at TP1, remaining 50% mit 1.5% Price Trail + BE Stop (entry+0.21%)

**ERGEBNIS: Strategy B ist -$1,295 SCHLECHTER als Strategy A**

| Coin | Strat A | Strat B | Diff | Trail | BE | SL |
|------|---------|---------|------|-------|----|----|
| DYDX | -$177 | -$116 | +$61 | 18 | 21 | 11 |
| WLD | +$107 | +$129 | +$22 | 15 | 10 | 8 |
| FIDA | -$203 | -$831 | -$628 | 12 | 19 | 12 |
| BAT | -$303 | -$808 | -$504 | 15 | 19 | 16 |
| KAS | -$49 | -$295 | -$246 | 21 | 19 | 20 |
| TOTAL | -$626 | -$1,921 | -$1,295 | 81 | 88 | 67 |

**Strategy B Breakdown (236 Trades):**
- Trail: 81 (34%) — Preis lief weiter, Trailing funktionierte
- BE: 88 (37%) — kam zurueck zu Entry nach TP1 → nur 50% Gewinn
- SL: 67 (28%) — gestoppt vor TP1 (identisch zu Strat A)

**Key Findings:**
1. **37% der Trades kommen nach TP1 zurueck zu BE** — das halbiert den TP1-Gewinn fuer diese Trades
2. **Trail funktioniert nur in 34% der Faelle** — Avg Trail Profit $79, Best $276 (DYDX)
3. **FIDA/BAT besonders schlecht** mit Trail (-$628/-$504 Differenz) — viele BE-Exits
4. **Nur DYDX/WLD marginal besser** mit Trail (+$61/+$22) — staerkere Trends
5. **1.5% Price Trail bei 15x = 22.5% Margin Retrace** — das ist VIEL Spielraum, trotzdem kommen 37% zurueck
6. **Grundproblem:** EE2 ist Mean-Reversion → 1% Moves sind oft das Maximum, danach Reversal

**EMPFEHLUNG:** 100% TP1 bleibt ueberlegen. Kein Trailing bei EE2 Signalen.

## Erledigt (20.06.2026 — EE2 Top 5 1-Minute Verification)

### 1m Klines bestaetigen 30m WR exakt — alle 5 Coins CONFIRMED

**Script:** `/Trading/ee2_top5_1m_verify.py`
**Results:** `/Trading/ee2_top5_1m_verification.json`

**Setup:** 90 Tage, 30m Signale, 1m TP/SL Verification, 15x Leverage, 48h Walk-Forward

**ERGEBNIS: 30m WR = 1m WR bei BTC-bestaetigten Signalen (identisch!)**

| Coin | 30m WR (BTC) | 1m WR (BTC) | 1m WR (no BTC) | Sigs | BTC Sigs | Confirmed |
|------|-------------|-------------|----------------|------|----------|-----------|
| DYDX | 78.0% | 78.0% | 67.6% | 296 | 50 | YES |
| WLD | 75.8% | 75.8% | 66.4% | 280 | 33 | YES |
| FIDA | 72.1% | 72.1% | 69.6% | 328 | 43 | YES |
| BAT | 68.0% | 68.0% | 59.2% | 293 | 50 | YES |
| KAS | 66.7% | 66.7% | 65.0% | 294 | 60 | YES |

**Key Findings:**
1. **1m WR = 30m WR exakt** bei BTC-bestaetigten Signalen — 30m Backtest war bereits korrekt
2. **Ohne BTC Confirmation: WR sinkt 2-9pp** — DYDX 67.6%, WLD 66.4%, BAT 59.2%
3. **BTC Confirmation IST relevant fuer diese Top-Coins** (trotz Overall-Ergebnis von +0.4pp)
4. **FIDA ist am stabilsten**: 72.1% mit BTC, 69.6% ohne — nur -2.5pp Differenz
5. **BAT profitiert am meisten von BTC Filter**: 68.0% vs 59.2% (-8.8pp ohne)
6. **Alle Coins: 0 Timeouts** bei BTC-bestaetigten Signalen
7. **DYDX SHORT besonders stark**: 88.9% WR (8/9), aber kleine Sample Size
8. **WLD SHORT ohne BTC**: 75.5% WR (138 Trades) — staerkstes Einzel-Segment

**Fazit:** Die 30m Walk-Forward Methode ist zuverlaessig — 1m Verification bestaetigt identische Ergebnisse. BTC Confirmation filtert effektiv bei diesen Top-Coins (im Gegensatz zum Gesamt-Pool von 42 Coins).

## Erledigt (20.06.2026 — EE2 BTC-Altcoin Korrelations-Backtest)

### BTC EE2 Signal als Altcoin-Filter: KEIN signifikanter Vorteil

**Script:** `/Trading/backtest_ee2_btc_correlation.py`
**Results:** `/Trading/ee2_btc_correlation_results.json`

**Setup:** 90 Tage, 30m, 42 Coins (15 Group A Large Caps + 27 Group B Mid Caps)
- EE2 Signal: LL/HH + TMO Extreme (<=9.7 / >=+9.7) + TMO turning
- TP1: 15% Margin / Leverage, SL: Swing Low/High * 0.998/1.002
- Walk-Forward: 48h max, TP vs SL vs Timeout

**BTC eigene Performance:** 276 Signale, WR 45.7% (126W/150L), Avg PnL -3.03%

**ERGEBNIS: BTC Confirmation bringt NICHTS**

| Vergleich | Signals | WR% | Avg PnL |
|-----------|---------|-----|---------|
| MIT BTC Confirmation | 2,815 | 56.4% | -2.17% |
| OHNE BTC | 9,726 | 56.0% | +0.03% |
| **Improvement** | — | **+0.4pp** | **-2.20%** |

**Direction Breakdown:**
- LONG w/ BTC: 57.3% WR, -3.56% Avg PnL
- SHORT w/ BTC: 54.6% WR, +0.36% Avg PnL
- LONG w/o BTC: 52.3% WR, -0.85% Avg PnL
- SHORT w/o BTC: 59.4% WR, +0.85% Avg PnL

**Group A vs B:** Beide +1.2pp mit BTC (vernachlaessigbar)

**Top Coins mit BTC Confirmation:**
- DYDX: 78.0% WR (39W/11L), +2.59% Avg
- WLD: 75.8% WR (25W/8L), +3.95% Avg
- FIDA: 72.1% WR (31W/12L), +2.35% Avg
- BAT: 68.0% WR (34W/16L), +2.09% Avg

**Key Findings:**
1. BTC EE2 auf 30m ist selbst ein schwaches Signal (45.7% WR, negative PnL)
2. WR-Verbesserung durch BTC Confirmation: nur +0.4pp — statistisch irrelevant
3. Avg PnL wird SCHLECHTER mit BTC Confirmation (-2.17% vs +0.03%)
4. SHORT Signale generell besser als LONG (59.4% vs 52.3% ohne BTC)
5. EE2 ist ein Mean-Reversion Signal — auf 30m zu rauschig fuer zuverlaessige BTC-Korrelation
6. Einzelne Coins (DYDX, WLD, FIDA) zeigen gute WR mit BTC, aber Sample Size zu klein

**EMPFEHLUNG:** BTC EE2 NICHT als Altcoin-Filter verwenden. EE2 Signale einzeln pro Coin bewerten.

## Erledigt (12.06.2026 — EE2 Signal Scanner erstellt)

### ee2_scanner.py — Autonomer stuendlicher Scanner

**Datei:** `/Trading/ee2_scanner.py`

**Was:** Autonomer EE2 Signal Scanner der stuendlich laeuft:
- 15 Coins (BTC, ETH, ADA, AVAX, BCH, BNB, DOGE, HBAR, LINK, LTC, SOL, SUI, TRX, XMR, XRP)
- 3 Timeframes (30m, 1h, 2h)
- TMO 14/5/3/3 EMA Berechnung
- Variante A: LL/HH + TMO Extreme (>=9.7 / <=-9.7) + Momentum Shift
- Variante B: Preis-Divergenz (LL/HH auf Preis, gegenlaeufig auf TMO)
- Swing Point Detection (2 Candles lookback each side)
- TP: 15%/22.5%/30% MARGIN (geteilt durch Leverage fuer Price%)
- SL: Low/High der letzten 2 Candles vor Signal
- SL Skip wenn >25% Margin
- Trading Hours Check (CEST, kein Weekend, kein US Open)
- 8h Cooldown pro Coin+TF
- Telegram: Channel -1003770314055 + Chris direkt
- Leverage-Tabelle: 30m BTC/ETH/BNB=22x, rest=15x; 1h/2h BTC/ETH/BNB=17x, rest=12x
- Signals gespeichert in dashboard/ee2_signals.json
- Log: ee2_scanner.log

**Status:** py_compile OK. Chris muss Start bestaetigen.

## Erledigt (12.06.2026 — C8 Strategy in alle 3 Bots implementiert)

### C8 = C4+Phase + Time Filter + Slope + BTC Momentum Gate

**3 Dateien aktualisiert:**
- `paper_bot_cascade4.py` — Time Filter + BTC Momentum Gate hinzugefuegt
- `live_bot_cascade4.py` — Time Filter + BTC Momentum Gate hinzugefuegt
- `paper_bot_koda_se.py` — Time Filter + BTC Momentum Gate hinzugefuegt

**C8 Filter (alle 3 Bots):**
1. **Time Filter (UTC):** Nur Stunden {0,3,5,6,7,8,9,11,14,20,21,22} — Skip: 1,2,4,10,12,13,15,16,17,18,19,23
2. **Slope Filter < 1.0%** — war bereits vorhanden (paper_bot_cascade4 + koda_se)
3. **BTC Momentum Gate 0.2%:** BTC muss >= 0.2% in Trade-Richtung in letzter 1h bewegt haben

**Reihenfolge in scan_and_trade():**
1. Time Filter (am Anfang, vor allem)
2. Cascade >= 3 (CASCADE_MIN)
3. Phase Detection (Score >= 4.0, keine Phase D)
4. Slope Filter < 1.0%
5. BTC Momentum Gate 0.2%
6. Confirmation Queue (0.3% in 8 bars)

**Logging:**
- `[CASCADE4] TIME SKIP: UTC hour 15 not in trading hours`
- `[CASCADE4] BTC MOMENTUM SKIP: SHORT but BTC 1h change +0.15% (need -0.20%)`

**Status:** py_compile OK auf allen 3 Dateien. Chris muss Neustart bestaetigen.

## Erledigt (12.06.2026 — NK/DCA Strategy Backtest: NK REDUZIERT PnL massiv)

### 6 Configs: Viona-style NK + Relaxed BTC Cascade, 3 Monate, 23 Coins

**Script:** `/Trading/backtest_nk_strategy.py`
**Results:** `/Trading/backtest_nk_strategy_results.json`

**KONZEPT:** Statt Single Entry, Viona-style 3-Step DCA:
- MO: 10% Capital ($5) bei Signal
- NK1: 25% ($12.50) wenn Preis 1.8% gegen Trade laeuft
- NK2: 65% ($32.50) wenn Preis 3.8% gegen Trade laeuft
- SL: 5.6% vom ORIGINAL Entry
- TP: 60% EM vom aktuellen Durchschnittspreis

**ERGEBNIS: NK FUNKTIONIERT NICHT — Single Entry bleibt klar besser**

| # | Config | Trades | WR% | Net PnL | MaxDD | PnL/DD | NK1 | NK2 | FullSL |
|---|--------|--------|-----|---------|-------|--------|-----|-----|--------|
| 1 | Baseline C4+Phase (single) | 908 | 52.4% | **$2,741** | $500 | **5.48** | - | - | - |
| 2 | C4+Phase+NK | 914 | 53.8% | $258 | $139 | 1.86 | 267 | 29 | 25 |
| 3 | Relaxed BTC+Phase (single) | 1028 | 52.4% | **$2,934** | $490 | **5.99** | - | - | - |
| 4 | Relaxed BTC+Phase+NK | 1035 | 53.8% | $320 | $124 | 2.58 | 285 | 29 | 24 |
| 5 | C3+Phase+NK | 994 | 54.3% | $329 | $122 | 2.69 | 275 | 29 | 24 |
| 6 | Relaxed BTC C3+Phase+NK | 1035 | 53.9% | $323 | $124 | 2.61 | 285 | 29 | 24 |

**Key Findings:**
1. **NK reduziert Net PnL um ~90%**: $2,741 -> $258 (C4) bzw. $2,934 -> $320 (Relaxed)
2. **NK senkt MaxDD deutlich**: $500 -> $139 (-72%), ABER PnL/DD sinkt trotzdem (5.48 -> 1.86)
3. **WR steigt nur minimal**: 52.4% -> 53.8% (+1.4pp) — NK verbessert WR kaum
4. **NK1 wird in ~28% der Trades gefuellt** — 3.2% erreichen NK2
5. **Full SL (alle NKs + SL) tritt bei 2.3-2.7% der Trades auf** — selten aber teuer
6. **Relaxed BTC Cascade (SMA10>20 only) ist BESSER**: +13% mehr Trades, $2,934 vs $2,741, PnL/DD 5.99 vs 5.48
7. **C3 vs C4 bei NK: fast identisch** — Cascade-Threshold macht bei NK kaum Unterschied

**Warum NK schadet:**
- MO Entry ist nur 10% Capital ($5) = viel kleinere Position bei den 70%+ Trades die direkt laufen
- NK1/NK2 werden nur bei Trades gefuellt die gegen dich laufen = genau die schlechten Trades bekommen mehr Kapital
- Phase Detection PHASE_EXIT (58% aller Trades) schliesst frueh = NK hat keine Zeit zu wirken
- SL bei NK ist enger (5.6% vs 7%) = mehr SL-Hits
- **Grundproblem: Bei Trend-Following Entries (Phase C + Cascade) laufen Trades meist sofort in die richtige Richtung — DCA fuer Entries die zurueckkommen widerspricht dem Trend-Ansatz**

**Positive Erkenntnisse:**
- **Relaxed BTC Cascade ist eine echte Verbesserung** fuer Single-Entry-Strategie
- Config 3 (Relaxed + Single) ist BESSER als Config 1 (Standard + Single): +$193 mehr PnL, besserer PnL/DD
- Relaxed produziert +13% mehr Trades bei gleicher WR

**EMPFEHLUNG:**
- NK/DCA NICHT implementieren — Single Entry bleibt optimal
- Relaxed BTC Cascade (SMA10>20 only) als Verbesserung in Betracht ziehen
- Phase FULL + Single Entry + Relaxed Cascade = bestes Ergebnis ($2,934, PnL/DD 5.99)

## Erledigt (12.06.2026 — Trend+Pullback+Momentum Backtest: NICHT profitabel)

### 10 Configs (7x 15m + 3x 30m) + 6 Extra ohne 7-Score, 3 Monate, 23 Coins

**Script:** `/Trading/backtest_pullback_momentum.py`
**Results:** `/Trading/backtest_pullback_momentum_results.json`

**KONZEPT:** Statt alles aligned (Cascade >=4 auf ALLEN TFs), neuer Ansatz:
- Higher TFs (30m/1h/4h): Trend bestaetigt (SMA10>20>50)
- Signal TF (15m): Pullback aktiv (SMA10<SMA20 oder Price<SMA20)
- Signal TF: TMO dreht aus Extremzone = Momentum-Reversal
- = Entry am Pullback-Boden, MIT dem Trend

**ERGEBNIS: Pullback+Momentum FUNKTIONIERT NICHT**

| # | Config | Trades | WR% | Net PnL | PnL/DD |
|---|--------|--------|-----|---------|--------|
| 1 | Strict 3/3+cross ±1bar | 6 | 33.3% | $5 | 0.43 |
| 2 | Loose 2/3+cross ±2bar | 69 | 56.5% | $41 | 1.28 |
| 3 | Price PB+rising TMO | 3 | 33.3% | $2 | 0.43 |
| 4 | Relaxed (no extreme) | 76 | 52.6% | -$27 | -0.32 |
| 7 | **Baseline C4+Phase** | **910** | **52.3%** | **$2,720** | **5.22** |

30m Signals: 0-1 Trades — komplett unbrauchbar.

**Ohne 7-Score Filter (Extra-Test):**
| Config | Trades | WR% | Net PnL |
|--------|--------|-----|---------|
| B. Loose+no7score | 450 | 47.1% | -$105 |
| C. Relaxed+no7score | 474 | 46.0% | -$216 |
| F. 30m Relaxed+no7score | 8 | 62.5% | +$47 |

**Key Findings:**
1. **KEIN Config erreicht >70% WR** — bester ist Loose 15m mit 56.5%
2. **Kernproblem: Pullback widerspricht 7-Score** — Pullback auf Signal-TF = SMA nicht aligned = niedrigerer Score. Prob>=60% filtert fast alles raus
3. **Ohne 7-Score: WR sinkt unter 50%** — TMO-Reversal am Pullback-Boden ist kein zuverlaessiges Signal
4. **Strict Config (3/3 TF aligned + TMO cross) = zu selektiv** — nur 6 Trades in 3 Monaten
5. **Relaxed Config (TMO<0 und steigend) = unzuverlaessig** — 52.6% WR, negatives PnL
6. **C4+Phase FULL bleibt 66x besser** ($2,720 vs $41 best Pullback)

**Fazit:** Die Theorie "Entry am Pullback-Boden mit Trend" klingt gut, funktioniert aber nicht:
- TMO Extreme auf 15m = zu selten wenn Higher TFs im Trend
- Pullback-SMA + Momentum-Reversal = entweder zu wenig Trades ODER zu schlechte WR
- Cascade-basierter Ansatz (alles aligned) bleibt klar ueberlegen

## Erledigt (12.06.2026 — Momentum Crossover Entry Backtest: TMO+RSI = NICHT brauchbar)

### 7 Configs, 3 Monate, 23 Coins — TMO/RSI Crossover als Entry-Filter

**Script:** `/Trading/backtest_momentum_entry.py`
**Results:** `/Trading/backtest_momentum_entry_results.json`

**ERGEBNIS: Momentum Crossover Filter KILLT Trade-Anzahl**

| # | Config | Trades | /Day | WR% | Net PnL | PnL/DD |
|---|--------|--------|------|-----|---------|--------|
| 1 | Baseline (C4+Phase) | 910 | 10.1 | 52.3% | $2,720 | 5.22 |
| 2 | +TMO strict (±1 bar) | 18 | 0.2 | 55.6% | $99 | 2.71 |
| 3 | +RSI (30/70 cross) | 0 | 0.0 | — | $0 | — |
| 4 | +TMO loose (±2 bar, zone 0.5x) | 39 | 0.4 | 59.0% | $159 | 3.81 |
| 5 | +TMO+tightTP (50%) | 18 | 0.2 | 55.6% | $94 | 2.48 |
| 6 | +TMO+wideTP (70%) | 18 | 0.2 | 50.0% | $71 | 1.99 |
| 7 | +TMO OR RSI | 18 | 0.2 | 55.6% | $99 | 2.71 |

**Key Findings:**
1. RSI Crossover: ZERO Trades — RSI Extreme (OS/OB) widerspricht Phase C + Cascade >=4 fundamental
2. TMO Crossover: Nur 18-39 Trades (2-4% der Baseline) — viel zu selektiv
3. WR steigt leicht (+3-7pp) aber viel zu wenig Trades fuer PnL
4. PnL/DD sinkt bei ALLEN Configs vs Baseline (5.22 -> 1.99-3.81)
5. Kernproblem: Momentum-Extreme = Mean Reversion Signal, Phase C + Cascade = Trend-Following Signal — gegensaetzlich

**Fazit:** Momentum Crossover aus Extremzonen ist NICHT kompatibel mit Trend-Following Entries (Phase Detection + Cascade). Diese Filter widersprechen sich logisch.

## Erledigt (12.06.2026 — KODA Cascade 4 LIVE Bot erstellt)

### live_bot_cascade4.py — Merged Paper Bot Logic + Bybit API

**Datei:** `/Trading/dashboard/live_bot_cascade4.py`

**Was:** KODA Cascade 4 LIVE Bot erstellt durch Merge von:
- `paper_bot_cascade4.py` (komplette Signal/Trading-Logik inkl. Phase Detection)
- `live_bot_confirm.py` (Bybit V5 API Infrastruktur)

**Config:**
- $15/Trade, 10x Leverage, $1000 Budget
- SL: 70% MARGIN (= 7% Price bei 10x) — MARGIN-basiert, nicht Price-basiert
- TP: 60% Expected Move, berechnet vom Confirmation Entry
- Fees: 0.11% RT in PnL eingerechnet
- BE Stop: entry * (1 + 0.0021) fuer LONG

**Alle Features:**
1. 7-Score Analysis + Cascade >= 4 Filter
2. Phase Detection Entry Gate (Score >= 6, keine Phase D)
3. Phase Detection SL Management (5m D->5%, 15m D->3%, 30m D->close)
4. Confirmation Stage (0.3% in 8 bars)
5. TP1/TP2 Trailing (50% close, SL->BE fee-covered, 2% trail)
6. 24h Force Close ohne TP1
7. Collective Profit Exit (ROI>30% + sum>=100%)
8. Drawdown Brake (5 SLs -> pause + TG alert)
9. K3 Fix: Budget recheck vor jedem Trade Open
10. Bybit API: Market Orders, set_tp_sl, Position Monitoring, Cross-Reference

**Dual Mode:**
- `python3 live_bot_cascade4.py` — dry-run
- `python3 live_bot_cascade4.py --live` — REAL ORDERS

**Telegram:** Token 8623243424, Chat 351653518, Channel DISABLED
**Data:** `live_trades_cascade4.json` (git push alle 5 min)
**Log:** `live_bot_cascade4.log`
**24 Coins:** GLM, AVAX, KAS, MINA, XRP, FLOW, AXL, CELR, CYS, IOST, CAKE, KAITO, TRX, SUN, GRT, DUSK, BAT, SYN, TON, HBAR, DOT, LTC, LINK, SOL

**BYBIT_QTY_DECIMALS:** Alle Coins aus live_bot_confirm.py uebernommen + neue Cascade4-Coins hinzugefuegt (XRPUSDT:1, FLOWUSDT:1, MINAUSDT:1, CAKEUSDT:1, etc.)

**Status:** py_compile OK. Chris muss Start bestaetigen.

## Offen
- Bot starten wenn Chris bestaetigt
- Erst dry-run testen, dann --live

## Erledigt (12.06.2026 — Trade Verification Report: 44 Trades gegen Telegram Chat Export)

### extra_trades.json (#77-#120) vs Telegram Chat Export mit Screenshots

**Report:** `/Trading/trade_verification_report.json`

**Ergebnis:**
- 25 Trades: SCREENSHOT MATCH (alle Werte exakt bestaetigt via Bitget/BingX/Phemex Screenshots)
- 3 Trades: PARTIAL MATCH (Screenshot vorhanden, kleine Abweichungen erklaerbar)
- 15 Trades: TEXT MATCH (Chat-Nachrichten bestaetigen Daten, kein Screenshot)
- 1 Trade: NO EVIDENCE (#80 BNT LONG $2.14 — kleiner Trade ohne Screenshot/Chat)
- 0 MISMATCHES

**Verifizierte Screenshots (key trades):**
- #77 BTC SHORT: PnL -56.29 exakt, Entry 80871.4, Exit 80811.7 (Bitget)
- #81 NEO LONG: PnL +1574.47, ROI 42.92%, 12x (Bitget)
- #82-85 Close All Unfall: Alle 4 Trades exakt bestaetigt (IMX/JASMY/SUSHI/ETH)
- #89 FIDA SHORT: PnL -146.28, ROI -29.26% (Bitget)
- #90 SOL LONG 70x: PnL +44.46 (BingX)
- #94 IP SHORT: PnL +202.66 (Bitget)
- #97 FIL SHORT: PnL -157.67 (Bitget)
- #98 JASMY LONG 5x: PnL -85.66 (Bitget)
- #99 SOL SHORT 65x: PnL +324.25 (BingX)
- #101 SOL SHORT 70x: PnL +317.76 (BingX)
- #107 XLM LONG: PnL +931.62, 37 Tage (Phemex)
- #113 THETA SHORT 9x: Bei +$462 Peak, SL bei +$25.54 (Bitget)
- #118 WLD SHORT: PnL +228.66 net (Bitget)
- #119 KAS SHORT 9x: PnL +32.63 net (Bitget)
- #120 CRV SHORT 5x: PnL +222.27 (Bitget)

## Erledigt (12.06.2026 — Phase Detection in paper_bot_koda_se.py integriert)

### Phase Detection FULL (Entry + SL) in KODA SE C5 Signal Bot

**Datei:** `/Trading/dashboard/paper_bot_koda_se.py`

**Identische Phase Detection wie paper_bot_cascade4.py hinzugefuegt:**
1. **Phase Detection Funktionen** — `_detect_phase_direction()`, `get_phase()`, `fetch_sma_data_for_tf()`, `get_coin_phases()`, `calculate_phase_score()`, `check_phase_sl()`
2. **Phase Entry Gate** (in `scan_and_trade()`, nach Cascade>=5 Gate): Score >= 6, keine Phase D, konsistente Richtung
3. **Phase SL Management** (in `check_open_trades()`, vor TP1/TP2 Logik): Progressive SL-Verschaerfung
4. **Phase Cache** — `_phase_cache` dict, 120s Cache
5. **Phase Tracking** — `phase_score`, `phase_details`, `phase_sl_level` in Trade-Dict

**Unveraenderte KODA-SE-C5 Besonderheiten:**
- CASCADE_MIN = 5 (nicht 4)
- TP = 50% EM (nicht 60%)
- Signal Channel AKTIV (posted zu TG Channel)
- Log prefix [KODA-SE-C5]
- Fees separat berechnet (calc_fee Funktion)

**Status:** Syntax OK (py_compile). Chris muss Neustart bestaetigen.

## Erledigt (12.06.2026 — Phase Detection in paper_bot_cascade4.py integriert)

### Phase Detection FULL (Entry + SL) in Cascade 4 Paper Bot

**Datei:** `/Trading/dashboard/paper_bot_cascade4.py` (1840 Zeilen, von 1516)

**Was wurde hinzugefuegt:**
1. **Phase Detection Funktionen** — `get_phase()`, `_detect_phase_direction()` (exakt aus backtest_phase_detection_c4.py)
2. **Phase Entry Gate** — `calculate_phase_score()`: Score >= 6, keine Phase D, konsistente Richtung
3. **Phase SL Management** — `check_phase_sl()`: Progressive SL-Verschaerfung bei Degradation
4. **SMA-Daten pro TF** — `fetch_sma_data_for_tf()` holt SMA10/20/50 + 4-Punkt-History
5. **Caching** — Phase-Daten 120s gecacht um API-Calls zu minimieren

**Phase Entry (in scan_and_trade, NACH Cascade-Gate):**
- Score berechnet aus 5 TFs (5m/15m/30m/1h/4h): C=2, B=1.5, A=1, D=-1, X=0
- Entry nur wenn Score >= 6.0 UND keine Phase D UND keine Gegenrichtung
- Graceful Fallback: Wenn Phase-Daten nicht verfuegbar, Trade wird trotzdem erlaubt

**Phase SL (in check_open_trades, VOR TP1/TP2 Logik):**
- 5m Phase D → SL auf 50% Margin (5% Price bei 10x) verschaerft
- 15m Phase D → SL auf 30% Margin (3% Price) verschaerft
- 30m Phase D → Trade sofort geschlossen (PHASE_EXIT)
- SL wird NUR verschaerft, nie gelockert (nur in Richtung Tightening)

**Bestehende Funktionalitaet UNVERAENDERT:**
- Cascade >=4 Filter, Confirmation, TP/SL, Trailing, Fees, BE Stop, 24h Timeout
- Drawdown Brake, Collective Profit Exit, Budget Management
- Alle 4 kritischen Math-Fixes

**Status:** Syntax OK, Tests bestanden. Chris muss Start bestaetigen.

## Erledigt (12.06.2026 — Phase Detection vs Cascade >=4 Backtest)

### 4 Configs, 3 Monate, 23 Coins, Cascade >=4 + TP 60% EM

**Script:** `/Trading/backtest_phase_detection_c4.py`
**Results:** `/Trading/backtest_phase_detection_c4_results.json`

**ERGEBNIS — Phase Detection turns C4 from NEGATIVE to POSITIVE:**

| Config | Trades | WR% | Net PnL | MaxDD | PnL/DD | Jan PnL |
|--------|--------|-----|---------|-------|--------|---------|
| C4 baseline (fixed SL, 60%EM) | 2,220 | 62.8% | -$325 | $3,655 | -0.09 | -$1,147 |
| C4 + Phase Entry + Fixed SL | 781 | 67.7% | +$2,413 | $1,340 | 1.80 | +$501 |
| C4 + Phase SL | 6,756 | 43.4% | +$2,302 | $1,547 | 1.49 | -$3 |
| C4 + Phase FULL | 908 | 52.4% | +$2,741 | $500 | 5.48 | +$654 |

**Monthly Breakdown (C4 + Phase FULL):**
- Dec: $660 | Jan: +$654 | Feb: +$1,427

**Key Findings:**
1. **Phase Detection rettet C4** — von -$325 net auf +$2,741 (Phase FULL)
2. **Phase Entry alleine** bringt C4 auf +$2,413 (WR 67.7%, 781 Trades)
3. **Phase SL alleine** explodiert auf 6,756 Trades (fruehe Exits = schneller Re-Entry), trotzdem +$2,302
4. **Phase FULL hat besten Risk/Reward** — PnL/DD = 5.48, nur $500 MaxDD
5. **Januar wird profitabel**: -$1,147 wird zu +$654 (Phase FULL)
6. **C5 Phase FULL bleibt besser**: $3,487 net, PnL/DD 6.67 vs C4: $2,741, PnL/DD 5.48
7. **Phase SL erzeugt massiv PHASE_EXIT** (5,856 von 6,756 Trades) — sehr aggressiv

**Vergleich C5 vs C4 mit Phase FULL:**
- C5: 1,074t | 55.1% WR | +$3,487 | $523 DD | PnL/DD 6.67
- C4: 908t | 52.4% WR | +$2,741 | $500 DD | PnL/DD 5.48
- C5 gewinnt in allen Metriken

## Erledigt (12.06.2026 — Phase Detection vs Cascade Backtest)

### 4 Configs, 3 Monate, 23 Coins, 1m Klines

**Script:** `/Trading/backtest_phase_detection.py`
**Results:** `/Trading/backtest_phase_detection_results.json`

**ERGEBNIS:**

| Config | Trades | WR% | Net PnL | MaxDD | PnL/DD | Jan PnL |
|--------|--------|-----|---------|-------|--------|---------|
| Cascade >=5 (baseline) | 1124 | 64.6% | +$339 | $2,778 | 0.12 | -$1,149 |
| Phase Entry + Fixed SL | 934 | 74.1% | +$3,873 | $1,137 | 3.41 | +$598 |
| Cascade Entry + Phase SL | 2289 | 46.0% | +$2,053 | $848 | 2.42 | -$27 |
| Phase Entry + Phase SL (FULL) | 1074 | 55.1% | +$3,487 | $523 | 6.67 | +$639 |

**Key Findings:**
1. **Phase Entry ist der groesste Hebel** — WR springt von 64.6% auf 74.1%, Januar wird profitabel
2. **Phase SL alleine verdoppelt Trades** (2289 vs 1124) weil frueherer Exit = schnellerer Re-Entry
3. **Phase FULL hat besten Risk/Reward** — PnL/DD = 6.67x, nur $523 MaxDD
4. **Januar: -$1,149 wird zu +$639** — Phase Detection erkennt Baeren-Phasen und vermeidet falsche Entries
5. **Phase SL ONLY senkt WR auf 46%** aber viele kleine Exits statt wenige grosse Verluste

**Phase Detection Modell:**
- 5 TFs (5m/15m/30m/1h/4h), SMA10/20/50
- Phase A (Fresh cross), B (Building), C (Confirmed), D (Weakening), X (No trend)
- Entry: Score >=6, keine Phase D, konsistente Richtung
- SL: Progressive Tightening bei Phase D (5m=caution, 15m=warning, 30m=exit)

**EMPFEHLUNG:** Phase Entry + Phase SL (FULL) als neuer Bot-Standard

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
