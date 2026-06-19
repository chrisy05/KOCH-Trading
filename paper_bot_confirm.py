#!/usr/bin/env python3
"""
EE2 Auto-Trader — Paper Mode
Reads ee2_signals.json, auto-trades Gruppe A signals.
TP1 at +15% margin, SL from signal. No partial close, no trailing.
"""

import json
import ssl
import time
import os
import sys
import traceback
import logging
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CONFIG = {
    "capital": 50,           # per trade
    "fee_rate": 0.0011,      # 0.11% round trip
}

COINS_A = [
    "BTC", "ETH", "ADA", "AVAX", "BCH", "BNB", "DOGE",
    "HBAR", "LINK", "LTC", "SOL", "SUI", "TRX", "XMR", "XRP",
]

TZ = timezone(timedelta(hours=-4))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "paper_trades_confirm.json")
SIGNALS_FILE = os.path.join(SCRIPT_DIR, "ee2_signals.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "..", "ee2_trader.log")

# Telegram
TG_TOKEN = "8623243424:AAEqo7FlHPqZzZHrpLMQJFBxGnNY382YhW4"
CHRIS_ID = "351653518"

# SSL
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EE2-TRADE] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ee2_trader")

# ═══════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════

def tg_send(text):
    """Send message to Chris."""
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": CHRIS_ID, "text": text}).encode()
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "EE2Trader/1.0"})
        urllib.request.urlopen(req, context=ssl_ctx, timeout=15)
    except Exception as e:
        log.error(f"TG send error: {e}")

# ═══════════════════════════════════════════════════════════════
# BINANCE API
# ═══════════════════════════════════════════════════════════════

def api_get(url, timeout=10):
    """GET JSON from URL."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EE2Trader/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def get_current_price(symbol):
    """Get current price from Binance Futures."""
    data = api_get(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}")
    if data:
        return float(data["price"])
    return None


def get_recent_highlow(symbol, minutes=2):
    """Get high/low from last N 1m klines to catch wicks."""
    data = api_get(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&limit={minutes}")
    if not data:
        return None, None
    try:
        high = max(float(k[2]) for k in data)
        low = min(float(k[3]) for k in data)
        return high, low
    except Exception:
        return None, None

# ═══════════════════════════════════════════════════════════════
# DATA MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def load_data():
    """Load paper trades from JSON."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return new_data()


def new_data():
    """Create empty data structure."""
    return {
        "config": {
            "capital": CONFIG["capital"],
            "fee_rate": CONFIG["fee_rate"],
            "bot_name": "EE2 Auto-Trader",
            "start_date": datetime.now(TZ).strftime("%Y-%m-%d"),
        },
        "trades_15m": [],
        "trades_30m": [],
        "trades_1h": [],
        "trades_4h": [],
        "stats": {},
        "_heartbeat": "",
    }


def save_data(data):
    """Save paper trades to JSON."""
    data["_heartbeat"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    # Update stats
    update_stats(data)
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error(f"Save error: {e}")


def next_trade_id(data):
    """Get next global trade ID."""
    all_ids = []
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            all_ids.append(t.get("id", 0))
    return (max(all_ids) + 1) if all_ids else 1


def update_stats(data):
    """Compute overall stats."""
    all_closed = []
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        all_closed.extend([t for t in data.get(key, []) if t.get("status") == "closed"])
    total = len(all_closed)
    wins = len([t for t in all_closed if (t.get("pnl") or 0) > 0])
    losses = total - wins
    total_pnl = sum(t.get("pnl", 0) for t in all_closed)
    wr = (wins / total * 100) if total > 0 else 0
    data["stats"] = {
        "total": total,
        "wins": wins,
        "losses": losses,
        "winrate": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
    }

# ═══════════════════════════════════════════════════════════════
# PRICE FORMATTING
# ═══════════════════════════════════════════════════════════════

def fmt(price):
    """Format price for display."""
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"

# ═══════════════════════════════════════════════════════════════
# SIGNAL PROCESSING
# ═══════════════════════════════════════════════════════════════

def load_signals():
    """Load EE2 signals from JSON."""
    if not os.path.exists(SIGNALS_FILE):
        return []
    try:
        with open(SIGNALS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def get_processed_signal_numbers(data):
    """Get set of already-processed signal numbers."""
    processed = set()
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            nr = t.get("signal_nr")
            if nr is not None:
                processed.add(nr)
    return processed


def get_open_coins(data):
    """Get set of coins with open trades."""
    coins = set()
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            if t.get("status") == "open":
                coins.add(t["coin"])
    return coins


def check_new_signals(data):
    """Read ee2_signals.json, open trades for new Gruppe A signals."""
    signals = load_signals()
    if not signals:
        return

    processed = get_processed_signal_numbers(data)
    open_coins = get_open_coins(data)

    for sig in signals:
        sig_nr = sig.get("number")
        if sig_nr is None or sig_nr in processed:
            continue

        coin = sig.get("coin", "")
        if coin not in COINS_A:
            continue

        # Max 1 trade per coin
        if coin in open_coins:
            log.info(f"Skip signal #{sig_nr} {coin} — already has open trade")
            processed.add(sig_nr)  # mark as seen so we don't keep logging
            continue

        direction = sig.get("direction", "LONG")
        entry = sig.get("entry")
        sl = sig.get("sl")
        leverage = sig.get("leverage", 12)
        tf = sig.get("tf", "1h")

        if entry is None or sl is None:
            log.warning(f"Skip signal #{sig_nr} — missing entry/sl")
            continue

        # Calculate TP1: +15% margin gain
        if direction == "LONG":
            tp = entry * (1 + 0.15 / leverage)
        else:
            tp = entry * (1 - 0.15 / leverage)

        # Position sizing
        capital = CONFIG["capital"]
        size = capital * leverage / entry
        margin = capital

        trade = {
            "id": next_trade_id(data),
            "coin": coin,
            "tf": tf,
            "direction": direction,
            "leverage": leverage,
            "entry": round(entry, 8),
            "tp": round(tp, 8),
            "sl": round(sl, 8),
            "size": round(size, 6),
            "margin": margin,
            "open_time": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S"),
            "close_time": None,
            "close_price": None,
            "close_reason": None,
            "pnl": 0,
            "roi": 0,
            "status": "open",
            "signal_nr": sig_nr,
            "group": "A",
        }

        # All trades go into trades_30m (as specified)
        data["trades_30m"].append(trade)
        open_coins.add(coin)
        processed.add(sig_nr)

        tp_pct = abs(tp / entry - 1) * 100
        sl_pct = abs(sl / entry - 1) * 100

        log.info(f"OPENED #{trade['id']} | Signal #{sig_nr} | {direction} {coin} {tf} | "
                 f"Entry: {fmt(entry)} | TP: {fmt(tp)} (+{tp_pct:.2f}%) | "
                 f"SL: {fmt(sl)} (-{sl_pct:.2f}%) | Lev: {leverage}x")

        # Telegram notification
        arrow = "🟢" if direction == "LONG" else "🔴"
        msg = (
            f"{arrow} EE2 TRADE OPENED\n"
            f"Signal #{sig_nr} | {coin} {direction} | {tf}\n"
            f"Entry: {fmt(entry)}\n"
            f"TP1: {fmt(tp)} (+{tp_pct:.1f}%)\n"
            f"SL: {fmt(sl)} (-{sl_pct:.1f}%)\n"
            f"Leverage: {leverage}x | Margin: ${margin}"
        )
        tg_send(msg)

# ═══════════════════════════════════════════════════════════════
# TRADE MONITORING
# ═══════════════════════════════════════════════════════════════

def check_open_trades(data):
    """Monitor open trades for TP/SL hits."""
    for tf_key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        open_trades = [t for t in data[tf_key] if t.get("status") == "open"]
        if not open_trades:
            continue

        # Group by coin to minimize API calls
        coins_needed = set(t["coin"] for t in open_trades)
        price_data = {}

        for coin in coins_needed:
            sym = f"{coin}USDT"
            p = get_current_price(sym)
            h, l = get_recent_highlow(sym, 2)
            if p is not None:
                price_data[coin] = (p, h or p, l or p)
            time.sleep(0.05)

        for trade in open_trades:
            coin = trade["coin"]
            if coin not in price_data:
                continue

            current_price, recent_high, recent_low = price_data[coin]

            # Update live price for dashboard
            trade["current_price"] = round(current_price, 8)

            direction = trade["direction"]
            entry = trade["entry"]
            tp = trade["tp"]
            sl = trade["sl"]
            size = trade["size"]

            # Check TP/SL using recent high/low (catches wicks)
            if direction == "LONG":
                # TP hit?
                if recent_high >= tp:
                    close_trade(trade, tp, "TP1", data)
                    continue
                # SL hit?
                if recent_low <= sl:
                    close_trade(trade, sl, "SL", data)
                    continue
            else:  # SHORT
                # TP hit?
                if recent_low <= tp:
                    close_trade(trade, tp, "TP1", data)
                    continue
                # SL hit?
                if recent_high >= sl:
                    close_trade(trade, sl, "SL", data)
                    continue

            # Update unrealized PnL
            if direction == "LONG":
                raw_pnl = (current_price - entry) * size
            else:
                raw_pnl = (entry - current_price) * size
            fee = abs(entry * size) * CONFIG["fee_rate"]
            trade["pnl"] = round(raw_pnl - fee, 2)
            trade["roi"] = round(trade["pnl"] / trade["margin"] * 100, 2)


def close_trade(trade, close_price, reason, data):
    """Close a trade and calculate final PnL."""
    direction = trade["direction"]
    entry = trade["entry"]
    size = trade["size"]

    if direction == "LONG":
        raw_pnl = (close_price - entry) * size
    else:
        raw_pnl = (entry - close_price) * size

    fee = abs(entry * size) * CONFIG["fee_rate"]
    pnl = raw_pnl - fee
    roi = pnl / trade["margin"] * 100

    trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    trade["close_price"] = round(close_price, 8)
    trade["close_reason"] = reason
    trade["pnl"] = round(pnl, 2)
    trade["roi"] = round(roi, 2)
    trade["status"] = "closed"

    result = "WIN" if pnl > 0 else "LOSS"
    log.info(f"CLOSED #{trade['id']} | {direction} {trade['coin']} | {reason} | "
             f"PnL: ${pnl:.2f} ({roi:+.1f}%) | {result}")

    # Telegram notification
    emoji = "✅" if pnl > 0 else "❌"

    # Duration
    duration = ""
    try:
        t1 = datetime.fromisoformat(trade["open_time"])
        t2 = datetime.fromisoformat(trade["close_time"])
        mins = int((t2 - t1).total_seconds() / 60)
        if mins < 60:
            duration = f"{mins}m"
        elif mins < 1440:
            duration = f"{mins // 60}h {mins % 60}m"
        else:
            duration = f"{mins // 1440}d {(mins % 1440) // 60}h"
    except Exception:
        pass

    # Overall stats for footer
    update_stats(data)
    stats = data.get("stats", {})

    msg = (
        f"{emoji} EE2 TRADE CLOSED — {reason}\n"
        f"{trade['coin']} {direction} | {trade['tf']}\n"
        f"Entry: {fmt(entry)} → Exit: {fmt(close_price)}\n"
        f"PnL: ${pnl:+.2f} ({roi:+.1f}%)\n"
        f"Duration: {duration}\n"
        f"─────────────────\n"
        f"Total: {stats.get('total', 0)} | W/L: {stats.get('wins', 0)}/{stats.get('losses', 0)} | "
        f"WR: {stats.get('winrate', 0):.0f}% | PnL: ${stats.get('total_pnl', 0):+.2f}"
    )
    tg_send(msg)

# ═══════════════════════════════════════════════════════════════
# GIT PUSH
# ═══════════════════════════════════════════════════════════════

_last_push = 0

def git_push():
    """Push paper_trades_confirm.json every 5 min."""
    global _last_push
    now = time.time()
    if now - _last_push < 300:
        return
    _last_push = now
    try:
        cwd = os.path.dirname(DATA_FILE)
        subprocess.run(["git", "add", "paper_trades_confirm.json"],
                       cwd=cwd, capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", "EE2 paper trader data update"],
                       cwd=cwd, capture_output=True, timeout=10)
        subprocess.run(["git", "push"],
                       cwd=cwd, capture_output=True, timeout=30)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def print_status(data):
    """Print current status."""
    all_open = []
    all_closed = []
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            if t.get("status") == "open":
                all_open.append(t)
            elif t.get("status") == "closed":
                all_closed.append(t)

    total_pnl = sum(t.get("pnl", 0) for t in all_closed)
    wins = len([t for t in all_closed if (t.get("pnl") or 0) > 0])
    losses = len(all_closed) - wins
    wr = (wins / len(all_closed) * 100) if all_closed else 0

    log.info(f"Open: {len(all_open)} | Closed: {len(all_closed)} | "
             f"W/L: {wins}/{losses} ({wr:.0f}%) | PnL: ${total_pnl:.2f}")

    for t in all_open:
        log.info(f"  #{t['id']} {t['direction']} {t['coin']} {t['tf']} | "
                 f"Entry: {fmt(t['entry'])} | PnL: ${t.get('pnl', 0):.2f}")


def main():
    log.info("=" * 60)
    log.info("EE2 Auto-Trader starting (Paper Mode)")
    log.info(f"Capital: ${CONFIG['capital']} | Fee: {CONFIG['fee_rate'] * 100:.2f}%")
    log.info(f"TP: +15% margin | SL: from signal")
    log.info(f"Coins A: {len(COINS_A)} | Signals: {SIGNALS_FILE}")
    log.info(f"Data: {DATA_FILE}")
    log.info("=" * 60)

    data = load_data()

    # Reset config to EE2
    data["config"] = {
        "capital": CONFIG["capital"],
        "fee_rate": CONFIG["fee_rate"],
        "bot_name": "EE2 Auto-Trader",
        "start_date": data.get("config", {}).get("start_date", datetime.now(TZ).strftime("%Y-%m-%d")),
    }

    tg_send("🟢 EE2 Auto-Trader gestartet (Paper Mode)\n"
            f"Capital: ${CONFIG['capital']}/Trade | TP: +15% Margin | SL: Signal\n"
            f"Coins: {len(COINS_A)} Gruppe A")

    cycle = 0
    while True:
        try:
            cycle += 1
            check_new_signals(data)
            check_open_trades(data)
            save_data(data)
            git_push()

            # Status every 10 cycles (5 min)
            if cycle % 10 == 0:
                print_status(data)

            time.sleep(30)

        except KeyboardInterrupt:
            log.info("Shutting down...")
            save_data(data)
            log.info("Data saved. Goodbye.")
            sys.exit(0)
        except Exception as e:
            log.error(f"Main loop error: {e}")
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()
