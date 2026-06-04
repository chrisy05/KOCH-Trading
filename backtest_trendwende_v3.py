#!/usr/bin/env python3
"""
Backtest v3: Trendwende-Einschleifung mit echten ER1-Parametern.

Reversal-Erkennung auf 2min + 5min:
- SMA 10 Slope (Chris getestet, nahe an Signallinie)
- TMO Crossover (Length 14, EMA 3/5/3)
- Verschiedene Kombis testen

Für 15min Paper-Trades: wenn 2min+5min Reversal → 10% SL, sonst 40%.
"""

import json
import time
import requests
import numpy as np
from datetime import datetime, timedelta

BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
TIGHT_SL_PCT = 10
MAX_TRIES = 2


def fetch_klines(symbol, end_ms, interval="2m", limit=100):
    start_ms = end_ms - (limit * interval_to_ms(interval))
    params = {
        "symbol": f"{symbol}USDT", "interval": interval,
        "startTime": start_ms, "endTime": end_ms, "limit": limit
    }
    try:
        r = requests.get(BINANCE_URL, params=params, timeout=10)
        data = r.json()
        if isinstance(data, list):
            return data
    except:
        pass
    return []


def interval_to_ms(interval):
    if interval == "1m": return 60000
    if interval == "2m": return 120000
    if interval == "3m": return 180000
    if interval == "5m": return 300000
    if interval == "15m": return 900000
    return 60000


def fetch_trade_klines(symbol, start_ms, end_ms):
    all_klines = []
    current = start_ms
    while current < end_ms:
        params = {
            "symbol": f"{symbol}USDT", "interval": "1m",
            "startTime": current, "endTime": end_ms, "limit": 1500
        }
        try:
            r = requests.get(BINANCE_URL, params=params, timeout=10)
            data = r.json()
            if not data or not isinstance(data, list):
                break
            all_klines.extend(data)
            current = data[-1][0] + 60000
            if len(data) < 1500:
                break
            time.sleep(0.1)
        except:
            break
    return all_klines


def calc_sma(values, period):
    if len(values) < period:
        return None
    return np.mean(values[-period:])


def calc_ema(values, period):
    if len(values) < period:
        return None
    ema = np.mean(values[:period])
    mult = 2 / (period + 1)
    for v in values[period:]:
        ema = (v - ema) * mult + ema
    return ema


def calc_ema_series(values, period):
    if len(values) < period:
        return []
    emas = []
    ema = np.mean(values[:period])
    emas.append(ema)
    mult = 2 / (period + 1)
    for v in values[period:]:
        ema = (v - ema) * mult + ema
        emas.append(ema)
    return emas


def sma_slope(closes, period=10, bars=3):
    """SMA Slope: SMA now vs SMA N bars ago."""
    if len(closes) < period + bars:
        return 0
    sma_now = np.mean(closes[-period:])
    sma_prev = np.mean(closes[-(period + bars):-bars])
    if sma_prev == 0:
        return 0
    return (sma_now - sma_prev) / sma_prev * 100


def calc_tmo(opens, closes, length=14, calc_ema_p=3, smooth_p=5, signal_p=3):
    """TMO calculation matching ER1 Pine: close vs open[j], EMA 3/5/3."""
    if len(closes) < length + calc_ema_p + smooth_p + signal_p + 5:
        return None, None

    # Raw values: sum of (close > open[j] ? 1 : close < open[j] ? -1 : 0) for j=1..length
    raw_series = []
    for i in range(length, len(closes)):
        raw = 0
        for j in range(1, length):
            if i - j >= 0 and i - j < len(opens):
                if closes[i] > opens[i - j]:
                    raw += 1
                elif closes[i] < opens[i - j]:
                    raw -= 1
        raw_series.append(raw)

    if len(raw_series) < calc_ema_p + smooth_p + signal_p:
        return None, None

    # Triple EMA: EMA(calc) → EMA(smooth) → EMA(signal) = main
    ema1 = calc_ema_series(raw_series, calc_ema_p)
    if len(ema1) < smooth_p:
        return None, None
    ema2 = calc_ema_series(ema1, smooth_p)
    if len(ema2) < signal_p:
        return None, None
    main = calc_ema_series(ema2, signal_p)

    # Signal = EMA of main
    if len(main) < signal_p:
        return None, None
    signal = calc_ema_series(main, signal_p)

    if main and signal:
        return main[-1], signal[-1]
    return None, None


def detect_reversal(coin, open_ms, direction, klines_2m, klines_5m):
    """
    Detect reversal using 2min + 5min data.
    Returns: (is_reversal, details_string)
    """
    if not klines_2m or not klines_5m:
        return False, "no data"

    closes_2m = [float(k[4]) for k in klines_2m]
    opens_2m = [float(k[1]) for k in klines_2m]
    closes_5m = [float(k[4]) for k in klines_5m]
    opens_5m = [float(k[1]) for k in klines_5m]

    # SMA 10 Slope on 2min and 5min
    slope_2m = sma_slope(closes_2m, period=10, bars=3)
    slope_5m = sma_slope(closes_5m, period=10, bars=3)

    # TMO on 2min and 5min
    tmo_main_2m, tmo_sig_2m = calc_tmo(opens_2m, closes_2m, 14, 3, 5, 3)
    tmo_main_5m, tmo_sig_5m = calc_tmo(opens_5m, closes_5m, 14, 3, 5, 3)

    # Reversal criteria
    reversal_factors = 0

    # SMA Slope against direction
    if direction == "LONG":
        if slope_2m < -0.01:
            reversal_factors += 1
        if slope_5m < -0.01:
            reversal_factors += 1
    else:  # SHORT
        if slope_2m > 0.01:
            reversal_factors += 1
        if slope_5m > 0.01:
            reversal_factors += 1

    # TMO against direction
    if tmo_main_2m is not None and tmo_sig_2m is not None:
        if direction == "LONG" and tmo_main_2m < tmo_sig_2m:
            reversal_factors += 1
        elif direction == "SHORT" and tmo_main_2m > tmo_sig_2m:
            reversal_factors += 1

    if tmo_main_5m is not None and tmo_sig_5m is not None:
        if direction == "LONG" and tmo_main_5m < tmo_sig_5m:
            reversal_factors += 1
        elif direction == "SHORT" and tmo_main_5m > tmo_sig_5m:
            reversal_factors += 1

    details = f"slope_2m={slope_2m:.3f}% slope_5m={slope_5m:.3f}% tmo_2m={'bear' if (tmo_main_2m or 0)<(tmo_sig_2m or 0) else 'bull'} tmo_5m={'bear' if (tmo_main_5m or 0)<(tmo_sig_5m or 0) else 'bull'} factors={reversal_factors}/4"

    return reversal_factors, details


def simulate_trade(trade, klines, sl_pct):
    entry = trade["entry"]
    tp = trade["tp"]
    direction = trade["direction"]
    margin = trade.get("margin", 100)
    leverage = 10
    sl_distance = entry * (sl_pct / 100) / leverage
    sl_price = entry - sl_distance if direction == "LONG" else entry + sl_distance

    for k in klines:
        high, low = float(k[2]), float(k[3])
        if direction == "LONG":
            if low <= sl_price:
                return "SL", -margin * sl_pct / 100
            if high >= tp:
                return "TP", margin * ((tp - entry) / entry) * leverage
        else:
            if high >= sl_price:
                return "SL", -margin * sl_pct / 100
            if low <= tp:
                return "TP", margin * ((entry - tp) / entry) * leverage
    return "OPEN", 0


def main():
    with open("paper_trades.json") as f:
        d = json.load(f)

    all_trades = []
    for tf in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        all_trades.extend(d.get(tf, []))
    closed = [t for t in all_trades if t.get("status") == "closed" and t.get("close_reason") in ("TP", "SL", "LIQ")]

    # Test multiple threshold levels
    for min_factors in [2, 3, 4]:
        print(f"\n{'='*70}")
        print(f"TEST: Reversal bei >= {min_factors}/4 Faktoren gegen Trade-Richtung")
        print(f"  SMA 10 Slope (2min + 5min) + TMO 14/3/5/3 (2min + 5min)")
        print(f"  Tight SL: {TIGHT_SL_PCT}% | Max {MAX_TRIES} Versuche/Coin")
        print(f"{'='*70}")

        pnl_orig = 0
        pnl_sim = 0
        stats = {"trend_tp": 0, "trend_sl": 0, "rev_tp": 0, "rev_sl": 0, "rev_skip": 0, "err": 0}
        coin_tries = {}

        for t in sorted(closed, key=lambda x: x.get("open_time", "")):
            coin = t["coin"]
            direction = t["direction"]
            orig_pnl = t.get("pnl", 0) or 0
            pnl_orig += orig_pnl

            try:
                open_dt = datetime.fromisoformat(t["open_time"])
                close_dt = datetime.fromisoformat(t["close_time"]) if t.get("close_time") else open_dt + timedelta(hours=24)
            except:
                stats["err"] += 1
                pnl_sim += orig_pnl
                continue

            open_ms = int(open_dt.timestamp() * 1000)
            close_ms = int(close_dt.timestamp() * 1000) + 60000

            # Fetch 2min and 5min klines before trade entry
            klines_2m = fetch_klines(coin, open_ms, "3m", 100)  # 3m (Binance has no 2m)
            klines_5m = fetch_klines(coin, open_ms, "5m", 100)

            rev_count, details = detect_reversal(coin, open_ms, direction, klines_2m, klines_5m)

            if rev_count >= min_factors:
                # Reversal detected
                if coin not in coin_tries:
                    coin_tries[coin] = {}
                tries = coin_tries[coin].get(direction, 0)
                if tries >= MAX_TRIES:
                    stats["rev_skip"] += 1
                    pnl_sim += 0
                    continue

                coin_tries[coin][direction] = tries + 1

                trade_klines = fetch_trade_klines(coin, open_ms, close_ms)
                if not trade_klines:
                    stats["err"] += 1
                    pnl_sim += orig_pnl
                    continue

                reason, pnl = simulate_trade(t, trade_klines, TIGHT_SL_PCT)
                if reason == "TP":
                    stats["rev_tp"] += 1
                    coin_tries[coin][direction] = 0
                else:
                    stats["rev_sl"] += 1
                pnl_sim += pnl
            else:
                # Normal trend
                if t.get("close_reason") == "TP":
                    stats["trend_tp"] += 1
                else:
                    stats["trend_sl"] += 1
                pnl_sim += orig_pnl
                if coin in coin_tries:
                    coin_tries[coin][direction] = 0

            time.sleep(0.03)

        total = stats["trend_tp"] + stats["trend_sl"] + stats["rev_tp"] + stats["rev_sl"]
        wr_trend = stats["trend_tp"] / max(1, stats["trend_tp"] + stats["trend_sl"]) * 100
        wr_rev = stats["rev_tp"] / max(1, stats["rev_tp"] + stats["rev_sl"]) * 100

        print(f"\n  Trend:    {stats['trend_tp']} TP + {stats['trend_sl']} SL = {wr_trend:.1f}% WR")
        print(f"  Reversal: {stats['rev_tp']} TP + {stats['rev_sl']} SL = {wr_rev:.1f}% WR")
        print(f"  Skipped:  {stats['rev_skip']} | Errors: {stats['err']}")
        print(f"  PnL orig: ${pnl_orig:.2f} → sim: ${pnl_sim:.2f} ({pnl_sim-pnl_orig:+.2f})")


if __name__ == "__main__":
    main()
