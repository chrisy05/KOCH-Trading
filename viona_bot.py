#!/usr/bin/env python3
"""
Viona Signal Trading Bot
========================
Listens to Telegram channel -1003766254546 for Viona signals,
then paper-trades (or live-trades) them using the defined strategy.

Strategy:
  MO  = 10% capital at market entry ($1,000 default)
  NK1 = 25% capital if price hits NK1 level (against direction)
  NK2 = 65% capital if price hits NK2 level (against direction)
  TP1 = +1% from avg entry -> close 50%, SL moves to entry
  TP2 = +2% from avg entry -> close remaining 50%
  SL  = Ausstiegslinie -> close everything

Usage:
  python3 viona_bot.py           # run bot
  python3 viona_bot.py --status  # show open trades
"""

import json, re, ssl, time, sys, os, traceback
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "viona_bot_config.json"
TRADES_FILE = BASE_DIR / "viona_trades.json"
LOG_FILE = BASE_DIR / "viona_bot.log"
TOKEN_FILE = Path("/Users/Chris/.claude/channels/telegram-2/.env")

SIGNAL_CHANNEL_ID = -1003766254546
NOTIFY_CHAT_ID = "351653518"

# ── SSL ────────────────────────────────────────────────────────────────
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# ── Default Config ─────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "entry_capital": 1000,      # MO size in $
    "leverage_override": 0,     # 0 = use signal leverage
    "tp1_pct": 1.0,             # TP1 at 1% price move
    "tp2_pct": 2.0,             # TP2 at 2% price move
    "tp_split": 50,             # % to close at TP1
    "use_signal_sl": True,      # Use SL from signal
    "enable_nk1": True,         # Enable Nachkauf 1
    "enable_nk2": True,         # Enable Nachkauf 2
    "entry_mode": "market",     # "market" or "limit"
    "mode": "paper",            # "paper" or "live"
}

DEFAULT_DATA = {
    "config": {},
    "paper_trades": [],
    "live_trades": [],
    "stats_paper": {},
    "stats_live": {},
    "last_update_id": 0,
}


# ══════════════════════════════════════════════════════════════════════
# Utilities
# ══════════════════════════════════════════════════════════════════════

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_token():
    with open(TOKEN_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("No TELEGRAM_BOT_TOKEN found in .env")


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # Merge with defaults for any missing keys
        for k, v in DEFAULT_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    else:
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def load_data():
    if TRADES_FILE.exists():
        with open(TRADES_FILE) as f:
            data = json.load(f)
        for k, v in DEFAULT_DATA.items():
            if k not in data:
                data[k] = v if not isinstance(v, (dict, list)) else type(v)()
        return data
    else:
        data = json.loads(json.dumps(DEFAULT_DATA))
        save_data(data)
        return data


def save_data(data):
    tmp = str(TRADES_FILE) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, TRADES_FILE)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def http_get(url, timeout=15):
    req = Request(url)
    resp = urlopen(req, context=ssl_ctx, timeout=timeout)
    return json.loads(resp.read())


def tg_send(token, chat_id, text):
    """Send a Telegram message."""
    try:
        import urllib.parse
        url = (
            f"https://api.telegram.org/bot{token}/sendMessage"
            f"?chat_id={chat_id}"
            f"&text={urllib.parse.quote(text)}"
            f"&parse_mode=HTML"
        )
        http_get(url)
    except Exception as e:
        log(f"TG send error: {e}", "WARN")


# ══════════════════════════════════════════════════════════════════════
# Signal Parser
# ══════════════════════════════════════════════════════════════════════

class SignalParser:
    """Parses Viona signals from Telegram channel_post messages."""

    def __init__(self):
        # Buffer: store setup messages waiting for entry
        # Key: (coin, direction) -> setup_data
        self.pending_setups = {}

    def extract_text(self, message):
        """Extract plain text from a Telegram message."""
        text = message.get("text", "")
        if not text:
            # Try caption for media messages
            text = message.get("caption", "")
        return text if isinstance(text, str) else ""

    def parse_message(self, message):
        """
        Parse a channel_post message.
        Returns a complete signal dict if this is an Entry message and we have a matching setup,
        or None if it's a setup message (stored) or unrelated.
        """
        text = self.extract_text(message)
        if "VIONA SIGNAL" not in text:
            return None

        # Determine if this is a setup message or entry message
        if "Entry des Signals:" in text:
            return self._parse_entry(text, message)
        elif "Nachkauf" in text or "Limit Order bei" in text:
            self._parse_setup(text, message)
            return None
        return None

    def _parse_setup(self, text, message):
        """Parse and store a setup message."""
        coin_m = re.search(r"([A-Z0-9]+USDT)", text)
        dir_m = re.search(r"Richtung:\s*(Long|Short)", text)
        lev_m = re.search(r"Hebel:\s*(\d+)", text)
        nk1_m = re.search(r"1\.\s*Limit Order bei\s*([\d.]+)", text)
        nk2_m = re.search(r"2\.\s*Limit Order bei\s*([\d.]+)", text)
        sl_m = re.search(r"Ausstiegslinie:\s*([\d.]+)", text)

        if not all([coin_m, dir_m, lev_m, nk1_m, nk2_m, sl_m]):
            log(f"Setup message incomplete, skipping", "WARN")
            return

        coin = coin_m.group(1)
        direction = dir_m.group(1).upper()
        key = (coin, direction)

        setup = {
            "coin": coin,
            "direction": direction,
            "leverage": int(lev_m.group(1)),
            "nk1_price": float(nk1_m.group(1).rstrip(".")),
            "nk2_price": float(nk2_m.group(1).rstrip(".")),
            "sl_price": float(sl_m.group(1).rstrip(".")),
            "setup_time": now_iso(),
            "msg_id": message.get("message_id"),
        }
        self.pending_setups[key] = setup
        log(f"Setup stored: {coin} {direction} {setup['leverage']}x | NK1={setup['nk1_price']} NK2={setup['nk2_price']} SL={setup['sl_price']}")

    def _parse_entry(self, text, message):
        """Parse an entry message and match with stored setup."""
        coin_m = re.search(r"([A-Z0-9]+USDT)", text)
        dir_m = re.search(r"Richtung:\s*(Long|Short)", text)
        entry_m = re.search(r"Entry des Signals:\s*([\d.]+)", text)

        if not all([coin_m, dir_m, entry_m]):
            log(f"Entry message incomplete, skipping", "WARN")
            return None

        coin = coin_m.group(1)
        direction = dir_m.group(1).upper()
        entry_price = float(entry_m.group(1).rstrip("."))
        key = (coin, direction)

        setup = self.pending_setups.pop(key, None)
        if setup is None:
            log(f"Entry for {coin} {direction} but no setup found, skipping", "WARN")
            return None

        signal = {
            "coin": coin,
            "direction": direction,
            "leverage": setup["leverage"],
            "signal_entry": entry_price,
            "nk1_price": setup["nk1_price"],
            "nk2_price": setup["nk2_price"],
            "sl_price": setup["sl_price"],
            "setup_time": setup["setup_time"],
            "entry_time": now_iso(),
        }
        log(f"Signal complete: {coin} {direction} {signal['leverage']}x @ {entry_price}")
        return signal


# ══════════════════════════════════════════════════════════════════════
# Price Monitor (Bitget)
# ══════════════════════════════════════════════════════════════════════

_price_cache = {}
_price_cache_ts = {}
PRICE_CACHE_TTL = 5  # seconds

def get_price(coin):
    """Get current price from Bitget futures."""
    now = time.time()
    if coin in _price_cache and now - _price_cache_ts.get(coin, 0) < PRICE_CACHE_TTL:
        return _price_cache[coin]

    try:
        url = f"https://api.bitget.com/api/v2/mix/market/ticker?productType=USDT-FUTURES&symbol={coin}"
        data = http_get(url)
        if data.get("code") == "00000" and data.get("data"):
            price = float(data["data"][0]["lastPr"])
            _price_cache[coin] = price
            _price_cache_ts[coin] = now
            return price
    except Exception as e:
        log(f"Price fetch error for {coin}: {e}", "WARN")
    return _price_cache.get(coin)


# ══════════════════════════════════════════════════════════════════════
# Trade Manager
# ══════════════════════════════════════════════════════════════════════

class TradeManager:
    def __init__(self, config, data, token):
        self.config = config
        self.data = data
        self.token = token

    def _trades_list(self):
        mode = self.config["mode"]
        key = f"{mode}_trades"
        return self.data[key]

    def _next_id(self):
        all_trades = self.data["paper_trades"] + self.data["live_trades"]
        if not all_trades:
            return 1
        return max(t["id"] for t in all_trades) + 1

    def _get_open_trades(self):
        return [t for t in self._trades_list() if t["status"] == "open"]

    def has_open_trade(self, coin, direction):
        """Check if there's already an open trade for this coin+direction."""
        for t in self._get_open_trades():
            if t["coin"] == coin and t["direction"] == direction:
                return True
        return False

    def open_trade(self, signal):
        """Open a new trade from a signal."""
        cfg = self.config
        coin = signal["coin"]
        direction = signal["direction"]

        if self.has_open_trade(coin, direction):
            log(f"Already have open {direction} trade for {coin}, skipping")
            return None

        leverage = cfg["leverage_override"] if cfg["leverage_override"] > 0 else signal["leverage"]
        entry_price = signal["signal_entry"]
        is_long = direction == "LONG"

        # Get actual market price for paper mode
        actual_entry = entry_price
        if cfg["mode"] == "paper":
            mp = get_price(coin)
            if mp:
                actual_entry = mp
                log(f"Paper entry: signal={entry_price}, market={mp}")

        # Capital allocation
        mo_capital = cfg["entry_capital"]
        nk1_capital = mo_capital * 2.5   # 25% of 10k = $2,500
        nk2_capital = mo_capital * 6.5   # 65% of 10k = $6,500
        total_reserved = mo_capital + nk1_capital + nk2_capital  # $10,000

        # Position size (in coin units)
        size = mo_capital * leverage / actual_entry

        # TP levels
        tp1_price = actual_entry * (1 + cfg["tp1_pct"] / 100) if is_long else actual_entry * (1 - cfg["tp1_pct"] / 100)
        tp2_price = actual_entry * (1 + cfg["tp2_pct"] / 100) if is_long else actual_entry * (1 - cfg["tp2_pct"] / 100)

        trade = {
            "id": self._next_id(),
            "coin": coin,
            "direction": direction,
            "leverage": leverage,
            "signal_entry": entry_price,
            "actual_entry": actual_entry,
            "nk1_price": signal["nk1_price"],
            "nk2_price": signal["nk2_price"],
            "sl_price": signal["sl_price"],
            "tp1_price": round(tp1_price, 8),
            "tp2_price": round(tp2_price, 8),
            "capital_mo": mo_capital,
            "capital_nk1": nk1_capital,
            "capital_nk2": nk2_capital,
            "capital_reserved": total_reserved,
            "capital_used": mo_capital,
            "size": round(size, 4),
            "avg_entry": actual_entry,
            "tp1_hit": False,
            "nk1_hit": False,
            "nk2_hit": False,
            "sl_hit": False,
            "sl_moved_to_entry": False,
            "current_sl": signal["sl_price"],
            "open_time": now_iso(),
            "close_time": None,
            "pnl_tp1": 0,
            "pnl_rest": 0,
            "pnl_total": 0,
            "status": "open",
            "mode": cfg["mode"],
            "events": [
                {
                    "time": now_iso(),
                    "event": "ENTRY",
                    "price": actual_entry,
                    "detail": f"MO ${mo_capital} | {size:.4f} {coin} @ {actual_entry}"
                }
            ],
        }

        self._trades_list().append(trade)
        log(f"TRADE OPENED: #{trade['id']} {coin} {direction} {leverage}x @ {actual_entry} | Size: {size:.4f} | SL: {signal['sl_price']}")

        # Notify Chris
        emoji = "🟢" if is_long else "🔴"
        msg = (
            f"{emoji} <b>VIONA {cfg['mode'].upper()} Trade #{trade['id']}</b>\n\n"
            f"<b>{coin}</b> {direction} {leverage}x\n"
            f"Entry: {actual_entry}\n"
            f"TP1: {trade['tp1_price']} (+{cfg['tp1_pct']}%)\n"
            f"TP2: {trade['tp2_price']} (+{cfg['tp2_pct']}%)\n"
            f"SL: {signal['sl_price']}\n"
            f"NK1: {signal['nk1_price']} | NK2: {signal['nk2_price']}\n"
            f"Capital: ${mo_capital} (reserved: ${total_reserved})"
        )
        tg_send(self.token, NOTIFY_CHAT_ID, msg)
        return trade

    def check_trade(self, trade):
        """Check a single open trade for TP/NK/SL hits."""
        coin = trade["coin"]
        price = get_price(coin)
        if price is None:
            return

        direction = trade["direction"]
        is_long = direction == "LONG"
        cfg = self.config
        changed = False

        # ── NK1 Check ──────────────────────────────────────────────
        if (cfg["enable_nk1"] and not trade["nk1_hit"]
                and not trade["tp1_hit"] and not trade["sl_hit"]):
            nk1_triggered = (is_long and price <= trade["nk1_price"]) or \
                            (not is_long and price >= trade["nk1_price"])
            if nk1_triggered:
                trade["nk1_hit"] = True
                nk1_size = trade["capital_nk1"] * trade["leverage"] / trade["nk1_price"]
                old_size = trade["size"]
                trade["size"] += nk1_size
                trade["capital_used"] += trade["capital_nk1"]
                # Recalculate avg entry
                total_cost = old_size * trade["avg_entry"] + nk1_size * trade["nk1_price"]
                trade["avg_entry"] = round(total_cost / trade["size"], 8)
                # Recalculate TP levels from new avg entry
                trade["tp1_price"] = round(
                    trade["avg_entry"] * (1 + cfg["tp1_pct"] / 100) if is_long
                    else trade["avg_entry"] * (1 - cfg["tp1_pct"] / 100), 8)
                trade["tp2_price"] = round(
                    trade["avg_entry"] * (1 + cfg["tp2_pct"] / 100) if is_long
                    else trade["avg_entry"] * (1 - cfg["tp2_pct"] / 100), 8)
                trade["events"].append({
                    "time": now_iso(), "event": "NK1", "price": trade["nk1_price"],
                    "detail": f"+${trade['capital_nk1']} | +{nk1_size:.4f} | AvgEntry={trade['avg_entry']}"
                })
                log(f"NK1 HIT: #{trade['id']} {coin} @ {trade['nk1_price']} | New avg: {trade['avg_entry']}")
                tg_send(self.token, NOTIFY_CHAT_ID,
                    f"🔁 <b>NK1 #{trade['id']} {coin}</b>\n"
                    f"Price: {trade['nk1_price']} | +${trade['capital_nk1']}\n"
                    f"New avg entry: {trade['avg_entry']}\n"
                    f"New TP1: {trade['tp1_price']} | TP2: {trade['tp2_price']}")
                changed = True

        # ── NK2 Check ──────────────────────────────────────────────
        if (cfg["enable_nk2"] and trade["nk1_hit"] and not trade["nk2_hit"]
                and not trade["tp1_hit"] and not trade["sl_hit"]):
            nk2_triggered = (is_long and price <= trade["nk2_price"]) or \
                            (not is_long and price >= trade["nk2_price"])
            if nk2_triggered:
                trade["nk2_hit"] = True
                nk2_size = trade["capital_nk2"] * trade["leverage"] / trade["nk2_price"]
                old_size = trade["size"]
                trade["size"] += nk2_size
                trade["capital_used"] += trade["capital_nk2"]
                total_cost = old_size * trade["avg_entry"] + nk2_size * trade["nk2_price"]
                trade["avg_entry"] = round(total_cost / trade["size"], 8)
                trade["tp1_price"] = round(
                    trade["avg_entry"] * (1 + cfg["tp1_pct"] / 100) if is_long
                    else trade["avg_entry"] * (1 - cfg["tp1_pct"] / 100), 8)
                trade["tp2_price"] = round(
                    trade["avg_entry"] * (1 + cfg["tp2_pct"] / 100) if is_long
                    else trade["avg_entry"] * (1 - cfg["tp2_pct"] / 100), 8)
                trade["events"].append({
                    "time": now_iso(), "event": "NK2", "price": trade["nk2_price"],
                    "detail": f"+${trade['capital_nk2']} | +{nk2_size:.4f} | AvgEntry={trade['avg_entry']}"
                })
                log(f"NK2 HIT: #{trade['id']} {coin} @ {trade['nk2_price']} | New avg: {trade['avg_entry']}")
                tg_send(self.token, NOTIFY_CHAT_ID,
                    f"🔁 <b>NK2 #{trade['id']} {coin}</b>\n"
                    f"Price: {trade['nk2_price']} | +${trade['capital_nk2']}\n"
                    f"New avg entry: {trade['avg_entry']}\n"
                    f"New TP1: {trade['tp1_price']} | TP2: {trade['tp2_price']}")
                changed = True

        # ── TP1 Check ──────────────────────────────────────────────
        if not trade["tp1_hit"] and not trade["sl_hit"]:
            tp1_triggered = (is_long and price >= trade["tp1_price"]) or \
                            (not is_long and price <= trade["tp1_price"])
            if tp1_triggered:
                trade["tp1_hit"] = True
                close_pct = cfg["tp_split"] / 100
                close_size = trade["size"] * close_pct
                if is_long:
                    pnl = (trade["tp1_price"] - trade["avg_entry"]) * close_size
                else:
                    pnl = (trade["avg_entry"] - trade["tp1_price"]) * close_size
                trade["pnl_tp1"] = round(pnl, 2)
                trade["size"] = round(trade["size"] - close_size, 4)
                # Move SL to entry (breakeven)
                trade["current_sl"] = trade["avg_entry"]
                trade["sl_moved_to_entry"] = True
                trade["events"].append({
                    "time": now_iso(), "event": "TP1", "price": trade["tp1_price"],
                    "detail": f"{int(close_pct*100)}% closed | PnL: ${pnl:.2f} | SL->entry"
                })
                log(f"TP1 HIT: #{trade['id']} {coin} @ {trade['tp1_price']} | PnL: ${pnl:.2f} | SL->entry")
                tg_send(self.token, NOTIFY_CHAT_ID,
                    f"🎯 <b>TP1 #{trade['id']} {coin}</b>\n"
                    f"Price: {trade['tp1_price']}\n"
                    f"Closed {int(close_pct*100)}% | PnL: ${pnl:.2f}\n"
                    f"SL moved to entry ({trade['avg_entry']})")
                changed = True

        # ── TP2 Check ──────────────────────────────────────────────
        if trade["tp1_hit"] and not trade["sl_hit"] and trade["status"] == "open":
            tp2_triggered = (is_long and price >= trade["tp2_price"]) or \
                            (not is_long and price <= trade["tp2_price"])
            if tp2_triggered:
                if is_long:
                    pnl_rest = (trade["tp2_price"] - trade["avg_entry"]) * trade["size"]
                else:
                    pnl_rest = (trade["avg_entry"] - trade["tp2_price"]) * trade["size"]
                trade["pnl_rest"] = round(pnl_rest, 2)
                trade["pnl_total"] = round(trade["pnl_tp1"] + pnl_rest, 2)
                trade["status"] = "closed"
                trade["close_time"] = now_iso()
                trade["size"] = 0
                trade["events"].append({
                    "time": now_iso(), "event": "TP2", "price": trade["tp2_price"],
                    "detail": f"100% closed | PnL rest: ${pnl_rest:.2f} | Total: ${trade['pnl_total']:.2f}"
                })
                log(f"TP2 HIT: #{trade['id']} {coin} @ {trade['tp2_price']} | Total PnL: ${trade['pnl_total']:.2f}")
                tg_send(self.token, NOTIFY_CHAT_ID,
                    f"🏆 <b>TP2 #{trade['id']} {coin}</b>\n"
                    f"Price: {trade['tp2_price']}\n"
                    f"Total PnL: <b>${trade['pnl_total']:.2f}</b>\n"
                    f"Capital: ${trade['capital_used']}")
                changed = True

        # ── SL Check ───────────────────────────────────────────────
        if not trade.get("sl_hit") and trade["status"] == "open":
            sl = trade["current_sl"]
            sl_triggered = (is_long and price <= sl) or (not is_long and price >= sl)
            if sl_triggered:
                trade["sl_hit"] = True
                if is_long:
                    pnl_rest = (sl - trade["avg_entry"]) * trade["size"]
                else:
                    pnl_rest = (trade["avg_entry"] - sl) * trade["size"]
                trade["pnl_rest"] = round(pnl_rest, 2)
                trade["pnl_total"] = round(trade["pnl_tp1"] + pnl_rest, 2)
                trade["status"] = "closed"
                trade["close_time"] = now_iso()
                trade["size"] = 0

                if trade["sl_moved_to_entry"]:
                    event_name = "SL_BE"
                    detail = f"SL at entry (breakeven) | PnL rest: ${pnl_rest:.2f} | Total: ${trade['pnl_total']:.2f}"
                    emoji = "🟡"
                else:
                    event_name = "SL"
                    detail = f"SL hit | PnL rest: ${pnl_rest:.2f} | Total: ${trade['pnl_total']:.2f}"
                    emoji = "🛑"

                trade["events"].append({
                    "time": now_iso(), "event": event_name, "price": sl, "detail": detail
                })
                log(f"{event_name}: #{trade['id']} {coin} @ {sl} | Total PnL: ${trade['pnl_total']:.2f}")
                tg_send(self.token, NOTIFY_CHAT_ID,
                    f"{emoji} <b>{event_name} #{trade['id']} {coin}</b>\n"
                    f"Price: {sl}\n"
                    f"Total PnL: <b>${trade['pnl_total']:.2f}</b>\n"
                    f"Capital used: ${trade['capital_used']}")
                changed = True

        return changed

    def update_stats(self):
        """Update statistics for both paper and live trades."""
        for mode in ["paper", "live"]:
            trades = self.data[f"{mode}_trades"]
            closed = [t for t in trades if t["status"] == "closed"]
            open_trades = [t for t in trades if t["status"] == "open"]

            if not closed and not open_trades:
                self.data[f"stats_{mode}"] = {}
                continue

            wins = [t for t in closed if t["pnl_total"] > 0]
            losses = [t for t in closed if t["pnl_total"] < 0]
            breakeven = [t for t in closed if t["pnl_total"] == 0]
            total_pnl = sum(t["pnl_total"] for t in closed)
            nk1_count = sum(1 for t in closed if t["nk1_hit"])
            nk2_count = sum(1 for t in closed if t["nk2_hit"])

            self.data[f"stats_{mode}"] = {
                "total_trades": len(trades),
                "open_trades": len(open_trades),
                "closed_trades": len(closed),
                "wins": len(wins),
                "losses": len(losses),
                "breakeven": len(breakeven),
                "winrate": round(len(wins) / len(closed) * 100, 1) if closed else 0,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(total_pnl / len(closed), 2) if closed else 0,
                "best_trade": round(max((t["pnl_total"] for t in closed), default=0), 2),
                "worst_trade": round(min((t["pnl_total"] for t in closed), default=0), 2),
                "nk1_trades": nk1_count,
                "nk2_trades": nk2_count,
                "capital_in_use": sum(t["capital_used"] for t in open_trades),
                "last_updated": now_iso(),
            }


# ══════════════════════════════════════════════════════════════════════
# Telegram Poller
# ══════════════════════════════════════════════════════════════════════

class TelegramPoller:
    def __init__(self, token, data):
        self.token = token
        self.last_update_id = data.get("last_update_id", 0)

    def poll(self):
        """Poll for new channel_post updates. Returns list of messages."""
        try:
            url = (
                f"https://api.telegram.org/bot{self.token}/getUpdates"
                f"?offset={self.last_update_id + 1}"
                f"&timeout=5"
                f"&allowed_updates=[\"channel_post\"]"
            )
            result = http_get(url, timeout=15)
            if not result.get("ok"):
                return []

            messages = []
            for update in result.get("result", []):
                uid = update["update_id"]
                if uid > self.last_update_id:
                    self.last_update_id = uid

                post = update.get("channel_post")
                if not post:
                    continue

                chat = post.get("chat", {})
                chat_id = chat.get("id")
                if chat_id != SIGNAL_CHANNEL_ID:
                    continue

                messages.append(post)

            return messages

        except Exception as e:
            log(f"Telegram poll error: {e}", "WARN")
            return []


# ══════════════════════════════════════════════════════════════════════
# Main Bot
# ══════════════════════════════════════════════════════════════════════

def print_status(data, config):
    """Print current status of open trades."""
    mode = config["mode"]
    trades = data[f"{mode}_trades"]
    open_trades = [t for t in trades if t["status"] == "open"]
    stats = data.get(f"stats_{mode}", {})

    print(f"\n{'='*60}")
    print(f"  VIONA Bot Status — Mode: {mode.upper()}")
    print(f"{'='*60}")

    if stats:
        print(f"  Total: {stats.get('total_trades', 0)} | "
              f"Open: {stats.get('open_trades', 0)} | "
              f"Closed: {stats.get('closed_trades', 0)}")
        print(f"  W/L/BE: {stats.get('wins', 0)}/{stats.get('losses', 0)}/{stats.get('breakeven', 0)} | "
              f"WR: {stats.get('winrate', 0)}%")
        print(f"  Total PnL: ${stats.get('total_pnl', 0):.2f} | "
              f"Avg: ${stats.get('avg_pnl', 0):.2f}")
        print(f"  Capital in use: ${stats.get('capital_in_use', 0):.0f}")

    if open_trades:
        print(f"\n  Open Trades:")
        for t in open_trades:
            price = get_price(t["coin"])
            if price:
                is_long = t["direction"] == "LONG"
                upnl = (price - t["avg_entry"]) * t["size"] if is_long else (t["avg_entry"] - price) * t["size"]
                pnl_str = f"uPnL: ${upnl:.2f}"
            else:
                pnl_str = "price N/A"
            flags = []
            if t["nk1_hit"]: flags.append("NK1")
            if t["nk2_hit"]: flags.append("NK2")
            if t["tp1_hit"]: flags.append("TP1")
            if t["sl_moved_to_entry"]: flags.append("SL->BE")
            flag_str = f" [{','.join(flags)}]" if flags else ""
            print(f"    #{t['id']} {t['coin']} {t['direction']} {t['leverage']}x "
                  f"@ {t['avg_entry']} | {pnl_str} | ${t['capital_used']}{flag_str}")
    else:
        print(f"\n  No open trades.")
    print(f"{'='*60}\n")


def main():
    log("=" * 50)
    log("VIONA Signal Bot starting...")

    # Load
    token = load_token()
    config = load_config()
    data = load_data()

    log(f"Mode: {config['mode'].upper()} | Entry: ${config['entry_capital']} | TP1: {config['tp1_pct']}% | TP2: {config['tp2_pct']}%")

    # Components
    parser = SignalParser()
    poller = TelegramPoller(token, data)
    manager = TradeManager(config, data, token)

    # Restore last_update_id
    poller.last_update_id = data.get("last_update_id", 0)

    # Notify startup
    tg_send(token, NOTIFY_CHAT_ID,
        f"🤖 <b>VIONA Bot gestartet</b>\n"
        f"Mode: {config['mode'].upper()}\n"
        f"Entry: ${config['entry_capital']} | TP1: {config['tp1_pct']}% | TP2: {config['tp2_pct']}%")

    print_status(data, config)

    last_save = time.time()
    last_status_log = time.time()
    cycle = 0

    while True:
        try:
            cycle += 1

            # ── 1. Poll Telegram ───────────────────────────────────
            messages = poller.poll()
            for msg in messages:
                signal = parser.parse_message(msg)
                if signal:
                    trade = manager.open_trade(signal)
                    if trade:
                        log(f"New trade opened: #{trade['id']}")

            # Save last_update_id
            data["last_update_id"] = poller.last_update_id

            # ── 2. Check open trades ───────────────────────────────
            mode = config["mode"]
            open_trades = [t for t in data[f"{mode}_trades"] if t["status"] == "open"]
            for trade in open_trades:
                try:
                    manager.check_trade(trade)
                except Exception as e:
                    log(f"Error checking trade #{trade['id']}: {e}", "ERROR")

            # ── 3. Update stats ────────────────────────────────────
            manager.update_stats()

            # ── 4. Save periodically (every 30s or on change) ──────
            now = time.time()
            if now - last_save >= 30:
                save_data(data)
                last_save = now

            # ── 5. Status log every 5 minutes ─────────────────────
            if now - last_status_log >= 300:
                n_open = len(open_trades)
                if n_open > 0:
                    coins = ", ".join(t["coin"] for t in open_trades)
                    log(f"Status: {n_open} open trades ({coins})")
                last_status_log = now

            # ── 6. Reload config if changed ────────────────────────
            if cycle % 30 == 0:  # every ~5 min
                try:
                    new_cfg = load_config()
                    if new_cfg != config:
                        config = new_cfg
                        manager.config = config
                        log("Config reloaded (changed)")
                except Exception:
                    pass

            time.sleep(10)

        except KeyboardInterrupt:
            log("Shutting down...")
            save_data(data)
            break
        except Exception as e:
            log(f"Main loop error: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")
            save_data(data)
            time.sleep(10)

    log("Bot stopped.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        config = load_config()
        data = load_data()
        print_status(data, config)
    else:
        main()
