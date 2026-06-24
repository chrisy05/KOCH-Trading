#!/usr/bin/env python3
"""
EE2 Auto-Trader — Paper Mode
Reads ee2_signals.json, auto-trades Gruppe A signals.
3-stage TP: 33% at TP1 (+15%), 33% at TP2 (+22.5%), 34% at TP3 (+30%). SL from signal, moves up after each TP.
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

# Whitelist: 13 Coins mit ≥50% WR aus Backtest
COINS_A = [
    "BTC", "ETH", "BCH", "BNB", "DOGE", "HBAR", "LTC",
    "SUI", "SEI", "BAT", "THETA", "BNT", "DOT",
]

TZ = timezone(timedelta(hours=-4))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "paper_trades_confirm.json")
SIGNALS_FILE = os.path.join(SCRIPT_DIR, "ee2_signals.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "..", "ee2_trader.log")

# Telegram
TG_TOKEN = "8623243424:AAEqo7FlHPqZzZHrpLMQJFBxGnNY382YhW4"
CHRIS_ID = "351653518"
KODA_SE_TOKEN = "8716936978:AAGauC-r4RmpGvtSR9qS72TR-aJvRaVBPB8"
KODA_SE_CHANNEL = "-1003770314055"

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

def tg_koda(text):
    """Send message to KODA Signal Engine channel."""
    try:
        url = f"https://api.telegram.org/bot{KODA_SE_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": KODA_SE_CHANNEL, "text": text}).encode()
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "EE2Trader/1.0"})
        urllib.request.urlopen(req, context=ssl_ctx, timeout=15)
    except Exception as e:
        log.error(f"TG KODA send error: {e}")

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


COOLDOWN_HOURS = 8

def get_open_coins(data):
    """Get set of coins with open trades."""
    coins = set()
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            if t.get("status") == "open":
                coins.add(t["coin"])
    return coins


def check_coin_tf_cooldown(data, coin, tf):
    """Return True if cooldown (8h) since last trade for coin+tf has passed."""
    last_open = None
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            if t.get("coin") == coin and t.get("tf") == tf:
                ot = t.get("open_time")
                if ot and (last_open is None or ot > last_open):
                    last_open = ot
    if last_open is None:
        return True  # no previous trade
    try:
        last_dt = datetime.fromisoformat(last_open).replace(tzinfo=TZ)
        elapsed_h = (datetime.now(TZ) - last_dt).total_seconds() / 3600
        return elapsed_h >= COOLDOWN_HOURS
    except Exception:
        return True


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

        # Skip stale signals — max 1h old
        sig_time = sig.get("timestamp", "")
        if sig_time:
            try:
                from datetime import datetime as dt2, timezone as tz2
                sig_dt = dt2.fromisoformat(sig_time)
                age_min = (dt2.now(tz2.utc) - sig_dt).total_seconds() / 60
                if age_min > 60:
                    processed.add(sig_nr)
                    continue
            except:
                pass

        # Max 1 trade per coin
        if coin in open_coins:
            log.info(f"Skip signal #{sig_nr} {coin} — already has open trade")
            processed.add(sig_nr)  # mark as seen so we don't keep logging
            continue

        # 8h cooldown per coin+tf
        if not check_coin_tf_cooldown(data, coin, tf):
            log.info(f"Skip signal #{sig_nr} {coin} {tf} — 8h cooldown active")
            processed.add(sig_nr)
            continue

        direction = sig.get("direction", "LONG")
        entry = sig.get("entry")
        sl = sig.get("sl")
        leverage = sig.get("leverage", 12)
        tf = sig.get("tf", "1h")

        if entry is None or sl is None:
            log.warning(f"Skip signal #{sig_nr} — missing entry/sl")
            continue

        # Calculate TP1/TP2/TP3: 3-stufig (15/22.5/30% Margin)
        if direction == "LONG":
            tp1 = entry * (1 + 0.15 / leverage)
            tp2 = entry * (1 + 0.225 / leverage)
            tp3 = entry * (1 + 0.30 / leverage)
        else:
            tp1 = entry * (1 - 0.15 / leverage)
            tp2 = entry * (1 - 0.225 / leverage)
            tp3 = entry * (1 - 0.30 / leverage)

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
            "tp": round(tp1, 8),
            "tp1": round(tp1, 8),
            "tp2": round(tp2, 8),
            "tp3": round(tp3, 8),
            "sl": round(sl, 8),
            "sl_current": round(sl, 8),
            "size": round(size, 6),
            "size_remaining": round(size, 6),
            "margin": margin,
            "open_time": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S"),
            "close_time": None,
            "close_price": None,
            "close_reason": None,
            "pnl": 0,
            "pnl_realized": 0,
            "roi": 0,
            "status": "open",
            "phase": "open",
            "signal_nr": sig_nr,
            "group": "WL",
        }

        # All trades go into trades_30m (as specified)
        data["trades_30m"].append(trade)
        open_coins.add(coin)
        processed.add(sig_nr)

        sl_pct = abs(sl / entry - 1) * 100

        log.info(f"OPENED #{trade['id']} | Signal #{sig_nr} | {direction} {coin} {tf} | "
                 f"Entry: {fmt(entry)} | TP1: {fmt(tp1)} | TP2: {fmt(tp2)} | TP3: {fmt(tp3)} | "
                 f"SL: {fmt(sl)} (-{sl_pct:.2f}%) | Lev: {leverage}x")

        # Telegram notification
        arrow = "🟢" if direction == "LONG" else "🔴"
        msg = (
            f"{arrow} EE2 TRADE OPENED\n"
            f"Signal #{sig_nr} | {coin} {direction} | {tf}\n"
            f"Entry: {fmt(entry)}\n"
            f"TP1: {fmt(tp1)} (+15% Margin) → 33% close\n"
            f"TP2: {fmt(tp2)} (+22.5%) → 33% close\n"
            f"TP3: {fmt(tp3)} (+30%) → Rest close\n"
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
            sl_current = trade.get("sl_current", trade["sl"])
            phase = trade.get("phase", "open")
            size_remaining = trade.get("size_remaining", trade["size"])
            tp1 = trade.get("tp1", trade.get("tp"))
            tp2 = trade.get("tp2")
            tp3 = trade.get("tp3")
            leverage = trade.get("leverage", 12)

            # Check SL first (at current SL level)
            sl_hit = (direction == "LONG" and recent_low <= sl_current) or \
                     (direction == "SHORT" and recent_high >= sl_current)

            if sl_hit:
                # Close remaining position at current SL
                close_trade_3stage(trade, sl_current, "SL", data)
                continue

            # Check TP progression
            if phase == "open" and tp1:
                tp1_hit = (direction == "LONG" and recent_high >= tp1) or \
                          (direction == "SHORT" and recent_low <= tp1)
                if tp1_hit:
                    # Close 33.3%, move SL to entry + 2.5% margin
                    portion = trade["size"] * 0.333
                    if direction == "LONG":
                        realized = (tp1 - entry) * portion
                    else:
                        realized = (entry - tp1) * portion
                    fee = abs(entry * portion) * CONFIG["fee_rate"]
                    trade["pnl_realized"] = round(trade.get("pnl_realized", 0) + realized - fee, 2)
                    trade["size_remaining"] = round(trade["size"] * 0.667, 6)
                    trade["phase"] = "tp1"
                    # Move SL to entry + 2.5% margin
                    sl_move = entry * (0.025 / leverage)
                    trade["sl_current"] = round(entry + sl_move if direction == "LONG" else entry - sl_move, 8)
                    log.info(f"  TP1 HIT #{trade['id']} {trade['coin']} | 33% closed +${realized-fee:.2f} | SL → +2.5% Margin")
                    tg_send(f"🎯 TP1 HIT — {trade['coin']} {direction}\n33% geschlossen, +${realized-fee:.2f}\nSL → Break-Even +2.5% Margin\nRest laeuft weiter → TP2/TP3")
                    tg_koda(f"🎯 TP1 HIT — {trade['coin']} {direction} | {trade['tf']}\n33% geschlossen, +${realized-fee:.2f}\nSL → Break-Even +2.5% Margin\nRest laeuft weiter → TP2/TP3")

            if trade.get("phase") == "tp1" and tp2:
                tp2_hit = (direction == "LONG" and recent_high >= tp2) or \
                          (direction == "SHORT" and recent_low <= tp2)
                if tp2_hit:
                    portion = trade["size"] * 0.333
                    if direction == "LONG":
                        realized = (tp2 - entry) * portion
                    else:
                        realized = (entry - tp2) * portion
                    fee = abs(entry * portion) * CONFIG["fee_rate"]
                    trade["pnl_realized"] = round(trade.get("pnl_realized", 0) + realized - fee, 2)
                    trade["size_remaining"] = round(trade["size"] * 0.334, 6)
                    trade["phase"] = "tp2"
                    # Move SL to entry + 7.5% margin
                    sl_move = entry * (0.075 / leverage)
                    trade["sl_current"] = round(entry + sl_move if direction == "LONG" else entry - sl_move, 8)
                    log.info(f"  TP2 HIT #{trade['id']} {trade['coin']} | 33% closed +${realized-fee:.2f} | SL → +7.5% Margin")
                    tg_send(f"🎯🎯 TP2 HIT — {trade['coin']} {direction}\n66% geschlossen\nSL → +7.5% Margin\nRest laeuft → TP3")
                    tg_koda(f"🎯🎯 TP2 HIT — {trade['coin']} {direction} | {trade['tf']}\n66% geschlossen\nSL → +7.5% Margin\nRest laeuft → TP3")

            if trade.get("phase") == "tp2" and tp3:
                tp3_hit = (direction == "LONG" and recent_high >= tp3) or \
                          (direction == "SHORT" and recent_low <= tp3)
                if tp3_hit:
                    # Full close — all 3 TPs hit
                    close_trade_3stage(trade, tp3, "TP3_FULL", data)
                    continue

            # Update unrealized PnL (on remaining position)
            if direction == "LONG":
                raw_pnl = (current_price - entry) * trade.get("size_remaining", trade["size"])
            else:
                raw_pnl = (entry - current_price) * trade.get("size_remaining", trade["size"])
            fee = abs(entry * trade.get("size_remaining", trade["size"])) * CONFIG["fee_rate"]
            total_pnl = trade.get("pnl_realized", 0) + raw_pnl - fee
            trade["pnl"] = round(total_pnl, 2)
            trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)


def close_trade_3stage(trade, close_price, reason, data):
    """Close remaining position — handles partial close history."""
    direction = trade["direction"]
    entry = trade["entry"]
    size_remaining = trade.get("size_remaining", trade["size"])
    phase = trade.get("phase", "open")

    # PnL on remaining portion
    if direction == "LONG":
        raw_pnl = (close_price - entry) * size_remaining
    else:
        raw_pnl = (entry - close_price) * size_remaining

    fee = abs(entry * size_remaining) * CONFIG["fee_rate"]
    final_portion_pnl = raw_pnl - fee

    # Total PnL = previously realized + this final close
    total_pnl = trade.get("pnl_realized", 0) + final_portion_pnl
    roi = total_pnl / trade["margin"] * 100

    trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    trade["close_price"] = round(close_price, 8)
    trade["close_reason"] = reason
    trade["pnl"] = round(total_pnl, 2)
    trade["roi"] = round(roi, 2)
    trade["status"] = "closed"
    trade["phase"] = "closed"

    result = "WIN" if total_pnl > 0 else "LOSS"
    trade["result"] = result
    phase_info = f" (after {phase})" if phase != "open" else ""
    log.info(f"CLOSED #{trade['id']} | {direction} {trade['coin']} | {reason}{phase_info} | "
             f"PnL: ${total_pnl:.2f} ({roi:+.1f}%) | {result}")

    # Telegram notification
    emoji = "✅" if total_pnl > 0 else "❌"
    if reason == "TP3_FULL":
        emoji = "🎯🎯🎯"

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
        f"PnL: ${total_pnl:+.2f} ({roi:+.1f}%)\n"
        f"Duration: {duration}\n"
        f"─────────────────\n"
        f"Total: {stats.get('total', 0)} | W/L: {stats.get('wins', 0)}/{stats.get('losses', 0)} | "
        f"WR: {stats.get('winrate', 0):.0f}% | PnL: ${stats.get('total_pnl', 0):+.2f}"
    )
    tg_send(msg)
    tg_koda(msg)

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

    tg_send("🟢 EE2 Confirmation Bot gestartet (Paper)\n"
            f"Capital: ${CONFIG['capital']}/Trade | SL: 5% Price\n"
            f"TP: 3-stufig (15/22.5/30% Margin, je 33%)\n"
            f"Coins: {len(COINS_A)} Whitelist | TF: 30m+1h")

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
