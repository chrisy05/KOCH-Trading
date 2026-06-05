#!/usr/bin/env python3
"""
Backtest: Trendwende-Erkennung mit Quant-Daten (BTC Leitindikator).

Logik:
- Vor jedem Trade: BTC Taker Ratio, Funding Rate, OI Change prüfen
- Wenn 2/3 gegen Trade-Richtung → TREND_WARNING → halber Hebel (5x statt 10x)
- Wenn 0-1/3 → TREND_OK → normaler Hebel (10x)

Simulation auf Paper Bot Trades.
"""
import json
import ssl
import time
import urllib.request
from datetime import datetime, timedelta

BINANCE_FUTURES = "https://fapi.binance.com"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def api_get(path, params=None):
    url = BINANCE_FUTURES + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    try:
        with urllib.request.urlopen(url, timeout=10, context=ctx) as r:
            return json.loads(r.read())
    except:
        return None


def get_btc_quant_at_time(open_ms):
    """Get BTC quant data around trade open time."""
    # Taker Buy/Sell Ratio (15min periods)
    taker = api_get("/futures/data/takerlongshortRatio", {
        "symbol": "BTCUSDT", "period": "15m", "limit": "3",
        "startTime": str(open_ms - 3600000), "endTime": str(open_ms)
    })

    # Funding Rate (closest)
    funding = api_get("/fapi/v1/fundingRate", {
        "symbol": "BTCUSDT", "limit": "1",
        "endTime": str(open_ms)
    })

    # OI History (1h)
    oi = api_get("/futures/data/openInterestHist", {
        "symbol": "BTCUSDT", "period": "1h", "limit": "3",
        "endTime": str(open_ms)
    })

    result = {"taker_ratio": None, "funding": None, "oi_change": None}

    if taker and len(taker) >= 1:
        result["taker_ratio"] = float(taker[-1].get("buySellRatio", 1))

    if funding and len(funding) >= 1:
        result["funding"] = float(funding[-1].get("fundingRate", 0))

    if oi and len(oi) >= 2:
        oi_now = float(oi[-1].get("sumOpenInterestValue", 0))
        oi_prev = float(oi[-2].get("sumOpenInterestValue", 0))
        if oi_prev > 0:
            result["oi_change"] = (oi_now - oi_prev) / oi_prev * 100

    return result


def is_trend_warning(quant, direction):
    """Check if BTC quant data warns against trade direction."""
    warnings = 0
    details = []

    # Taker Ratio: < 1 = bearish, > 1 = bullish
    if quant["taker_ratio"] is not None:
        if direction == "LONG" and quant["taker_ratio"] < 0.95:
            warnings += 1
            details.append(f"Taker {quant['taker_ratio']:.3f} bearish")
        elif direction == "SHORT" and quant["taker_ratio"] > 1.05:
            warnings += 1
            details.append(f"Taker {quant['taker_ratio']:.3f} bullish")

    # Funding: positive = long-heavy, negative = short-heavy
    if quant["funding"] is not None:
        # Contrarian: too many longs = bearish signal
        if direction == "LONG" and quant["funding"] > 0.0003:
            warnings += 1
            details.append(f"Funding {quant['funding']*100:.4f}% long-heavy")
        elif direction == "SHORT" and quant["funding"] < -0.0003:
            warnings += 1
            details.append(f"Funding {quant['funding']*100:.4f}% short-heavy")

    # OI Change: falling OI = positions closing = trend weakening
    if quant["oi_change"] is not None:
        if direction == "LONG" and quant["oi_change"] < -2:
            warnings += 1
            details.append(f"OI {quant['oi_change']:+.1f}% falling")
        elif direction == "SHORT" and quant["oi_change"] > 2:
            warnings += 1
            details.append(f"OI {quant['oi_change']:+.1f}% rising")

    return warnings >= 2, warnings, details


def main():
    with open("paper_trades.json") as f:
        d = json.load(f)

    all_trades = []
    for tf in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        all_trades.extend(d.get(tf, []))

    closed = [t for t in all_trades
              if t.get("status") == "closed"
              and t.get("close_reason") in ("TP", "SL", "LIQ")]

    # Sort by time
    closed.sort(key=lambda x: x.get("open_time", ""))

    print(f"Backtest: Quant-Trendwende (BTC Leitindikator)")
    print(f"  {len(closed)} Trades | Bei WARNING: 5x statt 10x Hebel")
    print(f"{'='*65}")

    pnl_original = 0
    pnl_simulated = 0
    stats = {
        "ok_tp": 0, "ok_sl": 0,
        "warn_tp": 0, "warn_sl": 0,
        "errors": 0
    }

    # Cache quant data per 15min window to avoid redundant API calls
    quant_cache = {}

    for i, t in enumerate(closed):
        orig_pnl = t.get("pnl", 0) or 0
        pnl_original += orig_pnl
        direction = t["direction"]
        reason = t.get("close_reason", "")

        try:
            open_dt = datetime.fromisoformat(t["open_time"])
        except:
            stats["errors"] += 1
            pnl_simulated += orig_pnl
            continue

        open_ms = int(open_dt.timestamp() * 1000)

        # Cache key: 15min window
        cache_key = open_ms // (15 * 60 * 1000)

        if cache_key not in quant_cache:
            quant = get_btc_quant_at_time(open_ms)
            quant_cache[cache_key] = quant
            time.sleep(0.15)
        else:
            quant = quant_cache[cache_key]

        warning, warn_count, details = is_trend_warning(quant, direction)

        if warning:
            # Half leverage: PnL is halved
            sim_pnl = orig_pnl * 0.5
            if reason == "TP":
                stats["warn_tp"] += 1
            else:
                stats["warn_sl"] += 1
        else:
            # Normal
            sim_pnl = orig_pnl
            if reason == "TP":
                stats["ok_tp"] += 1
            else:
                stats["ok_sl"] += 1

        pnl_simulated += sim_pnl

        if (i + 1) % 100 == 0:
            print(f"  ... {i+1}/{len(closed)} Trades verarbeitet")

    print(f"\n{'='*65}")
    print(f"ERGEBNIS")
    print(f"{'='*65}")

    ok_total = stats["ok_tp"] + stats["ok_sl"]
    warn_total = stats["warn_tp"] + stats["warn_sl"]

    ok_wr = stats["ok_tp"] / ok_total * 100 if ok_total > 0 else 0
    warn_wr = stats["warn_tp"] / warn_total * 100 if warn_total > 0 else 0

    print(f"\nTREND_OK (normaler Hebel 10x): {ok_total} Trades")
    print(f"  TP: {stats['ok_tp']} | SL: {stats['ok_sl']} | WR: {ok_wr:.1f}%")

    print(f"\nTREND_WARNING (halber Hebel 5x): {warn_total} Trades")
    print(f"  TP: {stats['warn_tp']} | SL: {stats['warn_sl']} | WR: {warn_wr:.1f}%")

    print(f"\nErrors: {stats['errors']}")
    print(f"\nPnL Original (alles 10x):  ${pnl_original:.2f}")
    print(f"PnL Simulated (5x bei Warning): ${pnl_simulated:.2f}")
    print(f"Differenz: ${pnl_simulated - pnl_original:+.2f}")

    # Key insight
    if warn_total > 0:
        warn_avg_pnl = (pnl_simulated - pnl_original) / warn_total * 2
        print(f"\nDurchschnittlicher Effekt pro Warning-Trade: ${warn_avg_pnl:+.2f}")

    saved_on_losses = 0
    lost_on_wins = 0
    for t in closed:
        orig_pnl = t.get("pnl", 0) or 0
        try:
            open_dt = datetime.fromisoformat(t["open_time"])
            open_ms = int(open_dt.timestamp() * 1000)
            cache_key = open_ms // (15 * 60 * 1000)
            quant = quant_cache.get(cache_key, {})
            warning, _, _ = is_trend_warning(quant, t["direction"])
            if warning:
                if orig_pnl < 0:
                    saved_on_losses += abs(orig_pnl) * 0.5
                else:
                    lost_on_wins += orig_pnl * 0.5
        except:
            pass

    print(f"\nGespart bei Verlusten: +${saved_on_losses:.2f}")
    print(f"Entgangen bei Gewinnen: -${lost_on_wins:.2f}")
    print(f"Netto: ${saved_on_losses - lost_on_wins:+.2f}")

    result = {
        "stats": stats,
        "pnl_original": round(pnl_original, 2),
        "pnl_simulated": round(pnl_simulated, 2),
        "pnl_diff": round(pnl_simulated - pnl_original, 2),
        "saved_on_losses": round(saved_on_losses, 2),
        "lost_on_wins": round(lost_on_wins, 2),
    }
    with open("backtest_quant_trendwende_result.json", "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nErgebnisse gespeichert.")


if __name__ == "__main__":
    main()
