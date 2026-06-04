#!/usr/bin/env python3
"""
Backtest: Trendwende SL-Einschleifung
Simuliert was passiert wäre wenn SL bei 10% statt 40%/LIQ gewesen wäre.

Für jeden geschlossenen Trade:
1. Hole 1min Klines für die Trade-Dauer
2. Prüfe ob -10% SL ODER TP zuerst getroffen wurde
3. Vergleiche mit dem tatsächlichen Ergebnis
"""

import json
import time
import requests
from datetime import datetime, timedelta

BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"
SIM_SL_PCT = 10  # Simulated SL in percent

def fetch_klines(symbol, start_ms, end_ms, interval="1m"):
    """Fetch klines from Binance Futures."""
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
            current_start = data[-1][0] + 60000  # next minute
            if len(data) < 1500:
                break
            time.sleep(0.1)
        except Exception as e:
            print(f"  Error fetching {symbol}: {e}")
            break
    return all_klines

def simulate_trade(trade, klines, sl_pct):
    """Simulate a trade with tighter SL. Returns new outcome."""
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
            # Check SL first (conservative)
            if low <= sl_price:
                pnl = margin * (-sl_pct / 100) * leverage / leverage  # = -sl_pct% of margin
                pnl = -margin * sl_pct / 100
                return "SL_10", sl_price, pnl
            if high >= tp:
                # TP hit - calculate actual gain
                roi = ((tp - entry) / entry) * leverage * 100
                pnl = margin * roi / 100
                return "TP", tp, pnl
        else:  # SHORT
            if high >= sl_price:
                pnl = -margin * sl_pct / 100
                return "SL_10", sl_price, pnl
            if low <= tp:
                roi = ((entry - tp) / entry) * leverage * 100
                pnl = margin * roi / 100
                return "TP", tp, pnl

    # Neither hit during kline data
    return "OPEN", None, 0

def main():
    with open("paper_trades.json") as f:
        d = json.load(f)

    all_trades = []
    for tf in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        all_trades.extend(d.get(tf, []))

    closed = [t for t in all_trades if t.get("status") == "closed" and t.get("close_reason") in ("TP", "SL", "LIQ")]

    print(f"Backtest: {len(closed)} geschlossene Trades mit {SIM_SL_PCT}% SL simulieren")
    print(f"{'='*70}")

    # Group by coin to minimize API calls
    by_coin = {}
    for t in closed:
        coin = t["coin"]
        if coin not in by_coin:
            by_coin[coin] = []
        by_coin[coin].append(t)

    results = {"TP_stayed_TP": 0, "TP_became_SL": 0, "SL_stayed_SL": 0, "LIQ_became_SL": 0, "errors": 0}
    pnl_original = 0
    pnl_simulated = 0

    tp_became_sl_trades = []

    for coin, trades in sorted(by_coin.items()):
        print(f"\n{coin}: {len(trades)} trades")

        for t in sorted(trades, key=lambda x: x.get("open_time", "")):
            orig_reason = t["close_reason"]
            orig_pnl = t.get("pnl", 0) or 0
            pnl_original += orig_pnl

            # Parse times
            try:
                open_dt = datetime.fromisoformat(t["open_time"])
                if t.get("close_time"):
                    close_dt = datetime.fromisoformat(t["close_time"])
                else:
                    close_dt = open_dt + timedelta(hours=24)
            except:
                results["errors"] += 1
                pnl_simulated += orig_pnl
                continue

            start_ms = int(open_dt.timestamp() * 1000)
            end_ms = int(close_dt.timestamp() * 1000) + 60000

            # Fetch klines
            klines = fetch_klines(coin, start_ms, end_ms)
            if not klines:
                results["errors"] += 1
                pnl_simulated += orig_pnl
                continue

            # Simulate
            new_reason, new_price, new_pnl = simulate_trade(t, klines, SIM_SL_PCT)

            if orig_reason == "TP":
                if new_reason == "TP":
                    results["TP_stayed_TP"] += 1
                    pnl_simulated += new_pnl
                else:
                    results["TP_became_SL"] += 1
                    pnl_simulated += new_pnl
                    tp_became_sl_trades.append({
                        "coin": coin, "dir": t["direction"],
                        "orig_pnl": f"${orig_pnl:.2f}", "new_pnl": f"${new_pnl:.2f}",
                        "open": t["open_time"][:16]
                    })
                    print(f"  ⚠ {t['direction']} #{t['id']}: TP→SL_10 (orig ${orig_pnl:.2f} → ${new_pnl:.2f})")
            elif orig_reason in ("SL", "LIQ"):
                if new_reason in ("SL_10", "SL"):
                    if orig_reason == "LIQ":
                        results["LIQ_became_SL"] += 1
                    else:
                        results["SL_stayed_SL"] += 1
                    pnl_simulated += new_pnl
                    saved = orig_pnl - new_pnl
                    if abs(saved) > 5:
                        print(f"  ✓ {t['direction']} #{t['id']}: {orig_reason}→SL_10 (${orig_pnl:.2f} → ${new_pnl:.2f}, gespart ${saved:.2f})")
                elif new_reason == "TP":
                    # Tight SL stopped it, but it would have recovered
                    # This shouldn't happen often since orig was SL/LIQ
                    results["SL_stayed_SL"] += 1
                    pnl_simulated += new_pnl
                else:
                    results["errors"] += 1
                    pnl_simulated += orig_pnl

            time.sleep(0.05)  # Rate limit

    print(f"\n{'='*70}")
    print(f"ERGEBNIS: Backtest mit {SIM_SL_PCT}% SL")
    print(f"{'='*70}")
    print(f"")
    print(f"Original:")
    print(f"  TP: {results['TP_stayed_TP'] + results['TP_became_SL']} | SL/LIQ: {results['SL_stayed_SL'] + results['LIQ_became_SL']}")
    print(f"  WR: {(results['TP_stayed_TP'] + results['TP_became_SL']) / max(1, len(closed)) * 100:.1f}%")
    print(f"  PnL: ${pnl_original:.2f}")
    print(f"")
    print(f"Mit {SIM_SL_PCT}% SL:")
    print(f"  TP (überlebt): {results['TP_stayed_TP']} | TP→SL: {results['TP_became_SL']} | SL (kleiner): {results['SL_stayed_SL']} | LIQ→SL: {results['LIQ_became_SL']}")
    print(f"  Neue WR: {results['TP_stayed_TP'] / max(1, results['TP_stayed_TP'] + results['TP_became_SL'] + results['SL_stayed_SL'] + results['LIQ_became_SL']) * 100:.1f}%")
    print(f"  PnL: ${pnl_simulated:.2f}")
    print(f"  Differenz: ${pnl_simulated - pnl_original:.2f}")
    print(f"  Errors/Skipped: {results['errors']}")

    if tp_became_sl_trades:
        print(f"\n--- Trades die TP waren aber mit 10% SL gestoppt worden wären ({len(tp_became_sl_trades)}): ---")
        for t in tp_became_sl_trades[:20]:
            print(f"  {t['coin']} {t['dir']} {t['open']}: {t['orig_pnl']} → {t['new_pnl']}")

    # Save results
    result_data = {
        "sl_pct": SIM_SL_PCT,
        "total_trades": len(closed),
        "results": results,
        "pnl_original": round(pnl_original, 2),
        "pnl_simulated": round(pnl_simulated, 2),
        "pnl_diff": round(pnl_simulated - pnl_original, 2),
        "tp_became_sl": tp_became_sl_trades
    }
    with open("backtest_trendwende_result.json", "w") as f:
        json.dump(result_data, f, indent=2)
    print(f"\nErgebnisse gespeichert in backtest_trendwende_result.json")

if __name__ == "__main__":
    main()
