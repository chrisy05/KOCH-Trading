#!/usr/bin/env python3
"""
Backtest v2: Trendwende SL-Einschleifung — NUR bei Reversal-Signalen.

Logik:
- Berechne EMA50 Slope (Trendscape) zum Zeitpunkt des Trade-Entry
- Slope GEGEN Trade-Richtung → Reversal Zone → 10% SL, max 2 Versuche/Coin
- Slope MIT Trade-Richtung → Normaler Trend → originaler SL bleibt
- Vergleiche PnL original vs. mit Einschleifung
"""

import json
import time
import requests
import numpy as np
from datetime import datetime, timedelta

BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
TIGHT_SL_PCT = 10
NORMAL_SL_PCT = 40
EMA_PERIOD = 50
SLOPE_BARS = 3
MAX_REVERSAL_TRIES_PER_COIN = 2


def fetch_klines(symbol, end_ms, interval="15m", limit=80):
    """Fetch klines ending at end_ms."""
    start_ms = end_ms - (limit * 15 * 60 * 1000)
    params = {
        "symbol": f"{symbol}USDT",
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit
    }
    try:
        r = requests.get(BINANCE_URL, params=params, timeout=10)
        data = r.json()
        if isinstance(data, list):
            return data
    except Exception as e:
        pass
    return []


def fetch_trade_klines(symbol, start_ms, end_ms, interval="1m"):
    """Fetch 1min klines for the trade duration."""
    all_klines = []
    current_start = start_ms
    while current_start < end_ms:
        params = {
            "symbol": f"{symbol}USDT",
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1500
        }
        try:
            r = requests.get(BINANCE_URL, params=params, timeout=10)
            data = r.json()
            if not data or not isinstance(data, list):
                break
            all_klines.extend(data)
            current_start = data[-1][0] + 60000
            if len(data) < 1500:
                break
            time.sleep(0.1)
        except:
            break
    return all_klines


def calc_ema(closes, period):
    """Calculate EMA."""
    if len(closes) < period:
        return None
    ema = np.mean(closes[:period])
    multiplier = 2 / (period + 1)
    for c in closes[period:]:
        ema = (c - ema) * multiplier + ema
    return ema


def calc_ema_slope(closes, period=50, slope_bars=3):
    """Calculate EMA slope over last slope_bars."""
    if len(closes) < period + slope_bars:
        return 0

    emas = []
    for i in range(slope_bars + 1):
        end_idx = len(closes) - slope_bars + i
        subset = closes[:end_idx]
        ema = np.mean(subset[:period])
        multiplier = 2 / (period + 1)
        for c in subset[period:]:
            ema = (c - ema) * multiplier + ema
        emas.append(ema)

    if len(emas) >= 2 and emas[-2] != 0:
        slope = (emas[-1] - emas[-2]) / emas[-2] * 100
        return slope
    return 0


def is_reversal_zone(slope, direction):
    """Check if EMA slope contradicts trade direction."""
    if direction == "LONG" and slope < -0.01:
        return True
    if direction == "SHORT" and slope > 0.01:
        return True
    return False


def simulate_trade(trade, klines, sl_pct):
    """Simulate a trade with given SL. Returns outcome."""
    entry = trade["entry"]
    tp = trade["tp"]
    direction = trade["direction"]
    leverage = 10
    margin = trade.get("margin", 100)

    sl_distance = entry * (sl_pct / 100) / leverage
    if direction == "LONG":
        sl_price = entry - sl_distance
    else:
        sl_price = entry + sl_distance

    for k in klines:
        high = float(k[2])
        low = float(k[3])

        if direction == "LONG":
            if low <= sl_price:
                pnl = -margin * sl_pct / 100
                return "SL", sl_price, pnl
            if high >= tp:
                roi = ((tp - entry) / entry) * leverage * 100
                pnl = margin * roi / 100
                return "TP", tp, pnl
        else:
            if high >= sl_price:
                pnl = -margin * sl_pct / 100
                return "SL", sl_price, pnl
            if low <= tp:
                roi = ((entry - tp) / entry) * leverage * 100
                pnl = margin * roi / 100
                return "TP", tp, pnl

    return "OPEN", None, 0


def main():
    with open("paper_trades.json") as f:
        d = json.load(f)

    all_trades = []
    for tf in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        all_trades.extend(d.get(tf, []))

    closed = [t for t in all_trades
              if t.get("status") == "closed"
              and t.get("close_reason") in ("TP", "SL", "LIQ")]

    print(f"Backtest v2: Trendwende-Einschleifung")
    print(f"  {len(closed)} Trades | Tight SL: {TIGHT_SL_PCT}% | Normal SL: {NORMAL_SL_PCT}%")
    print(f"  Max {MAX_REVERSAL_TRIES_PER_COIN} Versuche pro Coin in Reversal Zone")
    print(f"{'='*70}")

    by_coin = {}
    for t in closed:
        coin = t["coin"]
        if coin not in by_coin:
            by_coin[coin] = []
        by_coin[coin].append(t)

    pnl_original = 0
    pnl_simulated = 0
    stats = {
        "trend_tp": 0, "trend_sl": 0,
        "reversal_tp": 0, "reversal_sl_tight": 0, "reversal_skipped": 0,
        "errors": 0
    }
    reversal_coin_tries = {}

    for coin, trades in sorted(by_coin.items()):
        print(f"\n{coin}: {len(trades)} trades")
        reversal_coin_tries[coin] = {}

        for t in sorted(trades, key=lambda x: x.get("open_time", "")):
            orig_reason = t["close_reason"]
            orig_pnl = t.get("pnl", 0) or 0
            pnl_original += orig_pnl
            direction = t["direction"]

            try:
                open_dt = datetime.fromisoformat(t["open_time"])
                close_dt = datetime.fromisoformat(t["close_time"]) if t.get("close_time") else open_dt + timedelta(hours=24)
            except:
                stats["errors"] += 1
                pnl_simulated += orig_pnl
                continue

            open_ms = int(open_dt.timestamp() * 1000)
            close_ms = int(close_dt.timestamp() * 1000) + 60000

            # 1. Fetch pre-trade klines for EMA50 slope
            pre_klines = fetch_klines(coin, open_ms, interval="15m", limit=80)
            if len(pre_klines) < EMA_PERIOD + SLOPE_BARS:
                stats["errors"] += 1
                pnl_simulated += orig_pnl
                continue

            closes = [float(k[4]) for k in pre_klines]
            slope = calc_ema_slope(closes, EMA_PERIOD, SLOPE_BARS)
            reversal = is_reversal_zone(slope, direction)

            if reversal:
                # Check max tries per coin per direction
                dir_key = direction
                tries = reversal_coin_tries[coin].get(dir_key, 0)
                if tries >= MAX_REVERSAL_TRIES_PER_COIN:
                    # Skip this trade (cooldown)
                    stats["reversal_skipped"] += 1
                    pnl_simulated += 0  # no trade taken
                    print(f"  SKIP {direction} #{t['id']}: Reversal, max tries reached")
                    continue

                reversal_coin_tries[coin][dir_key] = tries + 1

                # Reversal zone → tight SL
                trade_klines = fetch_trade_klines(coin, open_ms, close_ms)
                if not trade_klines:
                    stats["errors"] += 1
                    pnl_simulated += orig_pnl
                    continue

                new_reason, _, new_pnl = simulate_trade(t, trade_klines, TIGHT_SL_PCT)

                if new_reason == "TP":
                    stats["reversal_tp"] += 1
                    pnl_simulated += new_pnl
                    # Reset tries on success
                    reversal_coin_tries[coin][dir_key] = 0
                    print(f"  ✓ REVERSAL {direction} #{t['id']}: TP ${new_pnl:.2f} (orig {orig_reason} ${orig_pnl:.2f}) slope={slope:.3f}%")
                else:
                    stats["reversal_sl_tight"] += 1
                    pnl_simulated += new_pnl
                    print(f"  ✗ REVERSAL {direction} #{t['id']}: SL_10 ${new_pnl:.2f} (orig {orig_reason} ${orig_pnl:.2f}) slope={slope:.3f}%")
            else:
                # Normal trend → keep original outcome
                if orig_reason == "TP":
                    stats["trend_tp"] += 1
                else:
                    stats["trend_sl"] += 1
                pnl_simulated += orig_pnl
                # Reset reversal tries on trend-aligned trade
                reversal_coin_tries[coin][direction] = 0

            time.sleep(0.05)

    print(f"\n{'='*70}")
    print(f"ERGEBNIS: Trendwende-Einschleifung Backtest")
    print(f"{'='*70}")

    total_orig = stats["trend_tp"] + stats["trend_sl"] + stats["reversal_tp"] + stats["reversal_sl_tight"] + stats["reversal_skipped"]

    print(f"\nOriginal: {len(closed)} Trades, PnL ${pnl_original:.2f}")
    print(f"\nMit Einschleifung:")
    print(f"  Trend-Zone (normal SL):  {stats['trend_tp']} TP + {stats['trend_sl']} SL")
    print(f"  Reversal-Zone (10% SL):  {stats['reversal_tp']} TP + {stats['reversal_sl_tight']} SL")
    print(f"  Reversal übersprungen:   {stats['reversal_skipped']} (max {MAX_REVERSAL_TRIES_PER_COIN}/Coin)")
    print(f"  Errors/Skipped:          {stats['errors']}")
    print(f"  Simulated PnL:           ${pnl_simulated:.2f}")
    print(f"  Differenz:               ${pnl_simulated - pnl_original:+.2f}")

    wr_trend = stats["trend_tp"] / max(1, stats["trend_tp"] + stats["trend_sl"]) * 100
    wr_reversal = stats["reversal_tp"] / max(1, stats["reversal_tp"] + stats["reversal_sl_tight"]) * 100
    print(f"\n  WR Trend-Zone:     {wr_trend:.1f}%")
    print(f"  WR Reversal-Zone:  {wr_reversal:.1f}%")

    result = {
        "tight_sl": TIGHT_SL_PCT,
        "normal_sl": NORMAL_SL_PCT,
        "max_tries": MAX_REVERSAL_TRIES_PER_COIN,
        "total_trades": len(closed),
        "stats": stats,
        "pnl_original": round(pnl_original, 2),
        "pnl_simulated": round(pnl_simulated, 2),
        "pnl_diff": round(pnl_simulated - pnl_original, 2),
    }
    with open("backtest_trendwende_v2_result.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nErgebnisse gespeichert in backtest_trendwende_v2_result.json")


if __name__ == "__main__":
    main()
