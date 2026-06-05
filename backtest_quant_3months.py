#!/usr/bin/env python3
"""
3-Monats Backtest: BTC Quant-Trendwende Filter.

Simuliert SHORT/LONG Trades auf BTC über 3 Monate (März-Juni 2026).
Prüft ob der Quant-Filter (Taker, Funding, OI) Trendwenden erkennt
und ob halber Hebel bei WARNING die Performance verbessert.
"""
import json
import ssl
import time
import urllib.request
import math
from datetime import datetime, timedelta, timezone

BINANCE = "https://fapi.binance.com"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def api_get(url):
    try:
        with urllib.request.urlopen(url, timeout=15, context=ctx) as r:
            return json.loads(r.read())
    except Exception as e:
        return None


def fetch_all_klines(symbol, interval, start_ms, end_ms):
    """Fetch all klines between start and end."""
    all_bars = []
    current = start_ms
    while current < end_ms:
        url = f"{BINANCE}/fapi/v1/klines?symbol={symbol}&interval={interval}&startTime={current}&limit=1500"
        data = api_get(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            break
        all_bars.extend(data)
        current = data[-1][0] + 1
        time.sleep(0.1)
    return all_bars


def fetch_taker_ratio(start_ms, end_ms):
    """Fetch BTC taker buy/sell ratio (15min periods)."""
    all_data = []
    current = start_ms
    while current < end_ms:
        url = f"{BINANCE}/futures/data/takerlongshortRatio?symbol=BTCUSDT&period=15m&limit=500&startTime={current}"
        data = api_get(url)
        if not data or len(data) == 0:
            break
        all_data.extend(data)
        current = data[-1]["timestamp"] + 1
        time.sleep(0.2)
    return {d["timestamp"]: float(d["buySellRatio"]) for d in all_data}


def fetch_funding(start_ms, end_ms):
    """Fetch BTC funding rates."""
    all_data = []
    current = start_ms
    while current < end_ms:
        url = f"{BINANCE}/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1000&startTime={current}"
        data = api_get(url)
        if not data or len(data) == 0:
            break
        all_data.extend(data)
        current = data[-1]["fundingTime"] + 1
        time.sleep(0.2)
    return {d["fundingTime"]: float(d["fundingRate"]) for d in all_data}


def fetch_oi_hist(start_ms, end_ms):
    """Fetch BTC OI history (1h)."""
    all_data = []
    current = start_ms
    while current < end_ms:
        url = f"{BINANCE}/futures/data/openInterestHist?symbol=BTCUSDT&period=1h&limit=500&startTime={current}"
        data = api_get(url)
        if not data or len(data) == 0:
            break
        all_data.extend(data)
        current = data[-1]["timestamp"] + 1
        time.sleep(0.2)
    return {d["timestamp"]: float(d["sumOpenInterestValue"]) for d in all_data}


def calc_ema(values, period):
    if len(values) < period:
        return values[-1] if values else 0
    ema = sum(values[:period]) / period
    mult = 2 / (period + 1)
    for v in values[period:]:
        ema = (v - ema) * mult + ema
    return ema


def calc_tmo(closes, opens, length=14, smooth=5, sig=3):
    """TMO calculation."""
    if len(closes) < length + smooth + sig + 5:
        return 0, 0
    raw = []
    for i in range(len(closes)):
        r = 0
        for j in range(1, min(length, i + 1)):
            if closes[i] > opens[i - j]:
                r += 1
            elif closes[i] < opens[i - j]:
                r -= 1
        raw.append(r)

    def ema_series(vals, p):
        if len(vals) < p:
            return vals
        result = []
        e = sum(vals[:p]) / p
        result.append(e)
        m = 2 / (p + 1)
        for v in vals[p:]:
            e = (v - e) * m + e
            result.append(e)
        return result

    e1 = ema_series(raw, smooth)
    main_series = ema_series(e1, smooth)
    if not main_series:
        return 0, 0
    signal_series = ema_series(main_series, sig)
    if not signal_series:
        return main_series[-1], 0
    return main_series[-1], signal_series[-1]


def main():
    # 3 months: March 9 to June 5, 2026
    start = datetime(2026, 3, 9, tzinfo=timezone.utc)
    end = datetime(2026, 6, 5, tzinfo=timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    print(f"3-Monats Backtest: {start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}")
    print(f"{'='*65}")

    # 1. Fetch BTC 15min klines
    print("Lade BTC 15min Klines...")
    klines = fetch_all_klines("BTCUSDT", "15m", start_ms, end_ms)
    print(f"  {len(klines)} Bars geladen")

    # 2. Fetch Quant data
    print("Lade Taker Ratio...")
    taker_data = fetch_taker_ratio(start_ms, end_ms)
    print(f"  {len(taker_data)} Datenpunkte")

    print("Lade Funding Rate...")
    funding_data = fetch_funding(start_ms, end_ms)
    print(f"  {len(funding_data)} Datenpunkte")

    print("Lade OI History...")
    oi_data = fetch_oi_hist(start_ms, end_ms)
    print(f"  {len(oi_data)} Datenpunkte")

    # 3. Simulate trades every 15min candle
    # Simple strategy: TMO direction on 15min = trade direction
    print("\nSimuliere Trades...")

    closes = []
    opens = []
    results = {"ok_tp": 0, "ok_sl": 0, "warn_tp": 0, "warn_sl": 0}
    pnl_orig = 0
    pnl_sim = 0
    trades_total = 0

    tp_pct = 0.005  # 0.5% TP (like V2 bot ~20% of expected move)
    sl_pct = 0.01   # 1.0% SL (like bot ~40% of expected move)

    for i, bar in enumerate(klines):
        ts = bar[0]
        o = float(bar[1])
        h = float(bar[2])
        l = float(bar[3])
        c = float(bar[4])

        closes.append(c)
        opens.append(o)

        if len(closes) < 30:
            continue

        # TMO signal
        tmo_main, tmo_sig = calc_tmo(closes[-50:], opens[-50:])

        if abs(tmo_main - tmo_sig) < 0.5:
            continue  # No clear signal

        direction = "LONG" if tmo_main > tmo_sig else "SHORT"

        # Check next few bars for TP/SL
        if i + 10 >= len(klines):
            continue

        entry = c
        if direction == "LONG":
            tp_price = entry * (1 + tp_pct)
            sl_price = entry * (1 - sl_pct)
        else:
            tp_price = entry * (1 - tp_pct)
            sl_price = entry * (1 + sl_pct)

        # Simulate forward
        hit = None
        for j in range(1, min(20, len(klines) - i)):
            fh = float(klines[i + j][2])
            fl = float(klines[i + j][3])

            if direction == "LONG":
                if fl <= sl_price:
                    hit = "SL"
                    break
                if fh >= tp_price:
                    hit = "TP"
                    break
            else:
                if fh >= sl_price:
                    hit = "SL"
                    break
                if fl <= tp_price:
                    hit = "TP"
                    break

        if hit is None:
            continue

        trades_total += 1
        orig_pnl = tp_pct * 10 * 100 if hit == "TP" else -sl_pct * 10 * 100  # % return at 10x

        # Quant filter
        # Find closest taker ratio
        taker_ts = max((t for t in taker_data if t <= ts), default=None)
        taker_val = taker_data.get(taker_ts) if taker_ts else None

        # Find closest funding
        funding_ts = max((t for t in funding_data if t <= ts), default=None)
        funding_val = funding_data.get(funding_ts) if funding_ts else None

        # Find closest OI (and previous for change)
        oi_times = sorted(t for t in oi_data if t <= ts)
        oi_change = None
        if len(oi_times) >= 2:
            oi_now = oi_data[oi_times[-1]]
            oi_prev = oi_data[oi_times[-2]]
            if oi_prev > 0:
                oi_change = (oi_now - oi_prev) / oi_prev * 100

        # Count warnings (MITTEL thresholds)
        warnings = 0
        if taker_val is not None:
            if direction == "LONG" and taker_val < 0.98:
                warnings += 1
            elif direction == "SHORT" and taker_val > 1.02:
                warnings += 1
        if funding_val is not None:
            if direction == "LONG" and funding_val > 0.0002:
                warnings += 1
            elif direction == "SHORT" and funding_val < -0.0002:
                warnings += 1
        if oi_change is not None:
            if direction == "LONG" and oi_change < -1.5:
                warnings += 1
            elif direction == "SHORT" and oi_change > 1.5:
                warnings += 1

        pnl_orig += orig_pnl
        if warnings >= 2:
            sim_pnl = orig_pnl * 0.5
            if hit == "TP":
                results["warn_tp"] += 1
            else:
                results["warn_sl"] += 1
        else:
            sim_pnl = orig_pnl
            if hit == "TP":
                results["ok_tp"] += 1
            else:
                results["ok_sl"] += 1
        pnl_sim += sim_pnl

    # Results
    print(f"\n{'='*65}")
    print(f"ERGEBNIS — 3 Monate BTC TMO-Trades mit Quant-Filter")
    print(f"{'='*65}")

    ok_total = results["ok_tp"] + results["ok_sl"]
    warn_total = results["warn_tp"] + results["warn_sl"]
    ok_wr = results["ok_tp"] / ok_total * 100 if ok_total > 0 else 0
    warn_wr = results["warn_tp"] / warn_total * 100 if warn_total > 0 else 0
    total_wr = (results["ok_tp"] + results["warn_tp"]) / trades_total * 100 if trades_total > 0 else 0

    print(f"\nTotal: {trades_total} Trades | WR gesamt: {total_wr:.1f}%")
    print(f"\nTREND_OK (10x Hebel): {ok_total} Trades | WR: {ok_wr:.1f}%")
    print(f"  TP: {results['ok_tp']} | SL: {results['ok_sl']}")
    print(f"\nTREND_WARNING (5x Hebel): {warn_total} Trades | WR: {warn_wr:.1f}%")
    print(f"  TP: {results['warn_tp']} | SL: {results['warn_sl']}")
    print(f"\nPnL ohne Filter: ${pnl_orig:.0f}")
    print(f"PnL mit Filter:  ${pnl_sim:.0f}")
    print(f"Differenz:        ${pnl_sim - pnl_orig:+.0f}")
    print(f"Warn-Anteil:      {warn_total/trades_total*100:.1f}%")

    # Save
    with open("backtest_quant_3months_result.json", "w") as f:
        json.dump({
            "period": "2026-03-09 to 2026-06-05",
            "trades": trades_total,
            "results": results,
            "pnl_orig": round(pnl_orig, 2),
            "pnl_sim": round(pnl_sim, 2),
            "pnl_diff": round(pnl_sim - pnl_orig, 2),
        }, f, indent=2)


if __name__ == "__main__":
    main()
