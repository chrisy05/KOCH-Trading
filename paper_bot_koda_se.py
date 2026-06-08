#!/usr/bin/env python3
"""
KODA SE-Analyzer Paper Trading Bot
Reads Grade A signals from se_analyzer_scanner.py and trades them on paper.

- Capital: $1,000 per trade (all-in, one trade at a time)
- Max 1 trade simultaneously
- Leverage: dynamic based on probability (70-75=5x, 75-80=7x, 80+=10x)
- Only Grade A signals (score >= 80)
- TP/SL from signal
- Active 24/7
"""

import json
import ssl
import time
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CAPITAL = 1000
MIN_SCORE = 80
CANDIDATES_FILE = "/tmp/se_analyzer_candidates.json"
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trades_koda_se.json")
CHECK_INTERVAL = 30  # seconds

TZ = timezone(timedelta(hours=-4))  # Santo Domingo

# SSL context for Binance API
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

import urllib.request

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

def log(msg):
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    print(f"[{ts}] [KODA-SE] {msg}", flush=True)

# ═══════════════════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════════════════

def api(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "KODA-SE-PaperBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def get_current_price(symbol):
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
    data = api(url)
    if data:
        return float(data["price"])
    return None


def get_recent_highlow(symbol, minutes=3):
    """Get high/low from recent 1m klines to catch wicks."""
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&limit={minutes}"
        req = urllib.request.Request(url, headers={"User-Agent": "KODA-SE-PaperBot/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            klines = json.loads(resp.read().decode())
        if not klines:
            return None, None
        high = max(float(k[2]) for k in klines)
        low = min(float(k[3]) for k in klines)
        return high, low
    except Exception:
        return None, None

# ═══════════════════════════════════════════════════════════════
# LEVERAGE MAPPING
# ═══════════════════════════════════════════════════════════════

def get_leverage(probability):
    """Map probability to leverage: 70-75=5x, 75-80=7x, 80+=10x"""
    if probability >= 80:
        return 10
    elif probability >= 75:
        return 7
    else:
        return 5

# ═══════════════════════════════════════════════════════════════
# DATA MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "config": {
            "capital": CAPITAL,
            "leverage": "dynamic",
            "min_score": MIN_SCORE,
            "start_date": datetime.now(TZ).strftime("%Y-%m-%d"),
            "total_budget": CAPITAL,
            "description": "KODA SE-Analyzer Bot | $1k/Trade | 1 Trade max | Grade A only"
        },
        "trades": [],
        "stats": {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0, "total_pnl": 0.0}
    }


def save_data(data):
    data["_heartbeat"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"ERROR saving data: {e}")


def next_trade_id(data):
    trades = data.get("trades", [])
    if not trades:
        return 1
    return max(t.get("id", 0) for t in trades) + 1

# ═══════════════════════════════════════════════════════════════
# TRADE LOGIC
# ═══════════════════════════════════════════════════════════════

def get_open_trade(data):
    """Return the currently open trade, or None."""
    for t in data.get("trades", []):
        if t["status"] == "open":
            return t
    return None


def open_trade(data, candidate):
    """Open a new paper trade from a candidate signal."""
    coin = candidate["name"]
    direction = candidate["direction"]
    entry = candidate["entry"]
    tp = candidate["tp"]
    sl = candidate["sl"]
    probability = candidate["probability"]
    score = candidate["score"]
    leverage = get_leverage(probability)
    size = CAPITAL * leverage / entry

    trade = {
        "id": next_trade_id(data),
        "coin": coin,
        "direction": direction,
        "entry": round(entry, 8),
        "tp": round(tp, 8),
        "sl": round(sl, 8),
        "leverage": leverage,
        "margin": CAPITAL,
        "size": round(size, 4),
        "probability": probability,
        "score": score,
        "open_time": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S"),
        "close_time": None,
        "close_price": None,
        "close_reason": None,
        "pnl": None,
        "roi": None,
        "status": "open",
    }

    data["trades"].append(trade)
    log(f"OPENED {direction} {coin} @ ${entry:.6f} | TP: ${tp:.6f} | SL: ${sl:.6f} | Lev: {leverage}x | Prob: {probability}% | Score: {score}")
    return trade


def close_trade(trade, close_price, reason):
    """Close a paper trade."""
    direction = trade["direction"]
    entry = trade["entry"]
    size = trade["size"]

    if direction == "LONG":
        pnl = (close_price - entry) * size
    else:
        pnl = (entry - close_price) * size

    roi = pnl / trade["margin"] * 100

    trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    trade["close_price"] = round(close_price, 8)
    trade["close_reason"] = reason
    trade["pnl"] = round(pnl, 2)
    trade["roi"] = round(roi, 2)
    trade["status"] = "closed"

    result = "WIN" if pnl > 0 else "LOSS"
    log(f"CLOSED {direction} {trade['coin']} @ ${close_price:.6f} | {reason} | PnL: ${pnl:.2f} ({roi:.1f}%) | {result}")
    return trade


def check_open_trade(data):
    """Check if open trade hit TP or SL."""
    trade = get_open_trade(data)
    if not trade:
        return

    sym = f"{trade['coin']}USDT"
    current = get_current_price(sym)
    if current is None:
        return

    high, low = get_recent_highlow(sym, 3)
    if high is None:
        high = current
    if low is None:
        low = current

    if trade["direction"] == "LONG":
        # TP hit? (wick up counts)
        if high >= trade["tp"]:
            close_trade(trade, trade["tp"], "TP")
        # SL hit? (wick down counts)
        elif low <= trade["sl"]:
            close_trade(trade, trade["sl"], "SL")
    else:  # SHORT
        # TP hit? (wick down counts)
        if low <= trade["tp"]:
            close_trade(trade, trade["tp"], "TP")
        # SL hit? (wick up counts)
        elif high >= trade["sl"]:
            close_trade(trade, trade["sl"], "SL")


def update_stats(data):
    """Recalculate stats from closed trades."""
    closed = [t for t in data["trades"] if t["status"] == "closed"]
    if not closed:
        data["stats"] = {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0, "total_pnl": 0.0}
        return

    wins = [t for t in closed if t.get("pnl", 0) and t["pnl"] > 0]
    total_pnl = sum(t["pnl"] for t in closed if t["pnl"])

    data["stats"] = {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "winrate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
        "total_pnl": round(total_pnl, 2),
    }

# ═══════════════════════════════════════════════════════════════
# SIGNAL READING
# ═══════════════════════════════════════════════════════════════

_last_scan_time = None

def read_candidates():
    """Read candidates from SE-Analyzer output file.
    Returns list of Grade A candidates, or empty list.
    Only returns NEW candidates (scan_time changed since last check).
    """
    global _last_scan_time

    if not os.path.exists(CANDIDATES_FILE):
        return []

    try:
        with open(CANDIDATES_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return []

    scan_time = data.get("scan_time")
    if not scan_time:
        return []

    # Skip if we already processed this scan
    if scan_time == _last_scan_time:
        return []

    _last_scan_time = scan_time
    candidates = data.get("candidates", [])

    # Filter: only Grade A (score >= 80)
    grade_a = [c for c in candidates if c.get("score", 0) >= MIN_SCORE]

    if grade_a:
        log(f"New scan detected ({scan_time}) — {len(candidates)} candidates, {len(grade_a)} Grade A")

    return grade_a

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def print_status(data):
    open_trade = get_open_trade(data)
    s = data["stats"]
    status = f"Open: {open_trade['coin'] + ' ' + open_trade['direction'] if open_trade else 'none'}"
    log(f"STATUS | {status} | Closed: {s['total']} | WR: {s['winrate']}% | PnL: ${s['total_pnl']:.2f}")


def main():
    log("KODA SE-Analyzer Paper Bot starting...")
    log(f"Capital: ${CAPITAL} | Min Score: {MIN_SCORE} | Max trades: 1")
    log(f"Leverage: dynamic (5x/7x/10x by probability)")
    log(f"Candidates file: {CANDIDATES_FILE}")
    log(f"Data file: {DATA_FILE}")
    log("")

    data = load_data()
    print_status(data)

    cycle = 0
    while True:
        try:
            cycle += 1

            # 1. Check open trade for TP/SL
            check_open_trade(data)

            # 2. If no trade open, check for new Grade A signals
            if get_open_trade(data) is None:
                candidates = read_candidates()
                if candidates:
                    # Take the best candidate (highest score)
                    best = max(candidates, key=lambda c: c.get("score", 0))
                    open_trade(data, best)

            # 3. Update stats and save
            update_stats(data)
            save_data(data)

            # 4. Status every 5 minutes (10 cycles of 30s)
            if cycle % 10 == 0:
                print_status(data)

            # 5. Auto-push to GitHub every 5 minutes
            if cycle % 10 == 0:
                try:
                    import subprocess
                    subprocess.run(["git", "add", "paper_trades_koda_se.json"],
                                   cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
                    subprocess.run(["git", "commit", "-m", "KODA-SE paper bot data update"],
                                   cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
                    subprocess.run(["git", "push"],
                                   cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=30)
                except Exception:
                    pass

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log("Shutting down...")
            update_stats(data)
            save_data(data)
            log("Data saved. Goodbye.")
            sys.exit(0)
        except Exception as e:
            log(f"ERROR in main loop: {e}")
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()
