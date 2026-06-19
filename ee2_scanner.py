#!/usr/bin/env python3
"""
EE2 Signal Scanner — Autonomous hourly scanner
Detects: TMO Extreme + Lower Low / Higher High + Divergence
Posts signals to Telegram SE Channel + Chris direct.

Settings: TMO 14/5/3/3 EMA
Coins: 15 Large Caps
Timeframes: 30m, 1h, 2h
"""

import json
import logging
import os
import ssl
import time
import urllib.request
import urllib.parse
import datetime

# ── Config ──────────────────────────────────────────────────────
SCANNER_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(SCANNER_DIR, "dashboard")
LOG_FILE = os.path.join(SCANNER_DIR, "ee2_scanner.log")
SIGNALS_JSON = os.path.join(DASHBOARD_DIR, "ee2_signals.json")
COUNTER_FILE = "/tmp/ee2_signal_counter.txt"
COOLDOWN_FILE = "/tmp/ee2_cooldowns.json"

BOT_TOKEN = "8623243424:AAEqo7FlHPqZzZHrpLMQJFBxGnNY382YhW4"
CHANNEL_ID = "-1003770314055"
CHRIS_ID = "351653518"

COINS_A = ["BTC", "ETH", "ADA", "AVAX", "BCH", "BNB", "DOGE", "HBAR", "LINK", "LTC", "SOL", "SUI", "TRX", "XMR", "XRP"]
COINS_B = ["AAVE", "BAT", "BNT", "CFX", "CRV", "DOT", "DYDX", "ENS", "FIDA", "FIL", "ICP", "IMX", "IP", "JASMY", "JUP", "KAS", "METIS", "NEAR", "OGN", "ONE", "OP", "SEI", "SUSHI", "THETA", "TON", "UNI", "WLD"]
COINS = COINS_A + COINS_B

def get_leverage(coin, tf):
    """Get leverage — Group A: original EE2, Group B: max 15x"""
    if coin in COINS_A:
        if tf == "30m":
            return {"BTC": 22, "ETH": 22, "BNB": 22}.get(coin, 15)
        else:
            return {"BTC": 17, "ETH": 17, "BNB": 17}.get(coin, 12)
    else:  # Group B — max 15x
        if tf == "30m":
            return min(15, 15)
        else:
            return min(12, 12)

def get_group(coin):
    return "A" if coin in COINS_A else "TEST B"


TIMEFRAMES = ["30m", "1h", "2h"]

LEVERAGE = {
    "30m": {"BTC": 22, "ETH": 22, "BNB": 22, "default": 15},
    "1h":  {"BTC": 17, "ETH": 17, "BNB": 17, "default": 12},
    "2h":  {"BTC": 17, "ETH": 17, "BNB": 17, "default": 12},
}

TMO_EXTREME = 9.7
COOLDOWN_HOURS = 8
MAX_SL_MARGIN_PCT = 25.0

# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EE2] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("ee2")

# ── SSL Context ─────────────────────────────────────────────────
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

# ── Telegram ────────────────────────────────────────────────────
def tg_send(chat_id, text):
    """Send message via Telegram using urllib"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text
        }).encode()
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "EE2Scanner/1.0"})
        urllib.request.urlopen(req, context=ssl_ctx, timeout=15)
    except Exception as e:
        log.error(f"TG send error: {e}")

def tg_broadcast(text):
    """Send signals ONLY to channel — not to Chris bot (that's for system info only)"""
    tg_send(CHANNEL_ID, text)

# ── Signal Counter ──────────────────────────────────────────────
def get_signal_number():
    try:
        with open(COUNTER_FILE, "r") as f:
            n = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        n = 0
    n += 1
    with open(COUNTER_FILE, "w") as f:
        f.write(str(n))
    return n

# ── Cooldown ────────────────────────────────────────────────────
def load_cooldowns():
    try:
        with open(COOLDOWN_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_cooldowns(cd):
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(cd, f)

def check_cooldown(coin, tf):
    cd = load_cooldowns()
    key = f"{coin}_{tf}"
    if key in cd:
        last = cd[key]
        if time.time() - last < COOLDOWN_HOURS * 3600:
            return False
    return True

def set_cooldown(coin, tf):
    cd = load_cooldowns()
    cd[f"{coin}_{tf}"] = time.time()
    save_cooldowns(cd)

# ── Trading Hours ───────────────────────────────────────────────
def is_trading_time():
    """Check EE2 trading hours (CEST = UTC+2)"""
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2)))
    day = now.weekday()  # 0=Mon
    hour = now.hour

    if day >= 5:
        return False  # Sat/Sun
    if day == 0 and hour < 16:
        return False  # Mon after ~15:30 CET
    if day == 4 and hour >= 16:
        return False  # Fri after ~15:30
    # Tue-Thu: skip 15:00-16:30 (US market open)
    if day in (1, 2, 3) and 15 <= hour <= 16:
        return False
    return True

# ── Data Fetching ───────────────────────────────────────────────
def fetch_klines(symbol, interval, limit=50):
    """Fetch klines from Binance Futures API"""
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}USDT&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EE2Scanner/1.0"})
        resp = urllib.request.urlopen(req, context=ssl_ctx, timeout=15)
        raw = json.loads(resp.read().decode())
        candles = []
        for k in raw:
            candles.append({
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "time": int(k[0])
            })
        return candles
    except Exception as e:
        log.error(f"Fetch error {symbol} {interval}: {e}")
        return []

# ── TMO Calculation ────────────────────────────────────────────
def calc_ema(data, period):
    """Exponential Moving Average"""
    if not data:
        return []
    ema = [data[0]]
    mult = 2 / (period + 1)
    for i in range(1, len(data)):
        ema.append(data[i] * mult + ema[-1] * (1 - mult))
    return ema

def calc_ee_momentum(opens, closes, length=15, calc_ema_len=3, smooth_ema=5, signal_ema=3):
    """
    EE Momentum Shift — exact replica of Pine Script ER1_v3.pine
    Settings: Length=15, Calc=3, Smooth=5, Signal=3
    raw = sum of (close > open[j] ? +1 : close < open[j] ? -1 : 0) for j=1 to length-1
    ee_s1 = EMA(raw, 3), ee_main = EMA(ee_s1, 5), ee_sig = EMA(ee_main, 3)
    Range: approx -14 to +14. Extreme zones: <= -9.7 (oversold) or >= +9.7 (overbought)
    """
    if len(closes) < length + 10:
        return [], []
    
    raw_values = []
    for i in range(length, len(closes)):
        raw = 0
        for j in range(1, length):
            if closes[i] > opens[i - j]:
                raw += 1
            elif closes[i] < opens[i - j]:
                raw -= 1
        raw_values.append(raw)
    
    ee_s1 = calc_ema(raw_values, calc_ema_len)
    ee_main = calc_ema(ee_s1, smooth_ema)
    ee_sig = calc_ema(ee_main, signal_ema)
    return ee_main, ee_sig

def find_swing_lows(lows, lookback=2):
    """Swing low = candle lower than lookback candles on each side"""
    swings = []
    for i in range(lookback, len(lows) - lookback):
        if all(lows[i] <= lows[i - j] for j in range(1, lookback + 1)) and \
           all(lows[i] <= lows[i + j] for j in range(1, lookback + 1)):
            swings.append((i, lows[i]))
    return swings

def find_swing_highs(highs, lookback=2):
    """Swing high = candle higher than lookback candles on each side"""
    swings = []
    for i in range(lookback, len(highs) - lookback):
        if all(highs[i] >= highs[i - j] for j in range(1, lookback + 1)) and \
           all(highs[i] >= highs[i + j] for j in range(1, lookback + 1)):
            swings.append((i, highs[i]))
    return swings

# ── TP/SL Calculation ──────────────────────────────────────────
def calc_tp_sl(entry, direction, leverage, candles, swing_low=None, swing_high=None):
    """
    TP: 15%, 22.5%, 30% on MARGIN = divide by leverage for PRICE
    SL: Below swing low (LONG) or above swing high (SHORT) with 0.2% buffer
    """
    tp1_price_pct = 0.15 / leverage
    tp2_price_pct = 0.225 / leverage
    tp3_price_pct = 0.30 / leverage

    if direction == "LONG":
        tp1 = entry * (1 + tp1_price_pct)
        tp2 = entry * (1 + tp2_price_pct)
        tp3 = entry * (1 + tp3_price_pct)
        # SL below the swing low (the LL point) with small buffer
        if swing_low is not None:
            sl = swing_low * 0.998  # 0.2% below swing low
        else:
            sl = min(k["low"] for k in candles[-3:]) * 0.998
    else:
        tp1 = entry * (1 - tp1_price_pct)
        tp2 = entry * (1 - tp2_price_pct)
        tp3 = entry * (1 - tp3_price_pct)
        # SL above the swing high (the HH point) with small buffer
        if swing_high is not None:
            sl = swing_high * 1.002  # 0.2% above swing high
        else:
            sl = max(k["high"] for k in candles[-3:]) * 1.002

    # SL distance as margin %
    sl_margin_pct = abs(entry - sl) / entry * leverage * 100

    return tp1, tp2, tp3, sl, sl_margin_pct

# ── Price Formatting ────────────────────────────────────────────
def fmt_price(price, coin):
    """Format price based on coin"""
    if coin in ("BTC",):
        return f"{price:.1f}"
    elif coin in ("ETH", "BNB", "BCH", "XMR"):
        return f"{price:.2f}"
    elif coin in ("SOL", "AVAX", "LINK", "LTC"):
        return f"{price:.3f}"
    elif coin in ("ADA", "DOGE", "HBAR", "SUI", "TRX", "XRP"):
        return f"{price:.4f}"
    else:
        if price > 1000:
            return f"{price:.2f}"
        elif price > 1:
            return f"{price:.4f}"
        else:
            return f"{price:.6f}"

# ── Signal Detection ───────────────────────────────────────────
def detect_signals(coin, tf, candles):
    """Detect EE2 signals: Variante A (LL/HH + TMO Extreme) and Variante B (Divergence)"""
    if len(candles) < 30:
        return []

    opens = [c["open"] for c in candles]
    closes = [c["close"] for c in candles]
    lows = [c["low"] for c in candles]
    highs = [c["high"] for c in candles]

    tmo, signal = calc_ee_momentum(opens, closes)
    if not tmo or len(tmo) < 5:
        return []

    # TMO array is shorter than candles due to lookback
    # Offset: length(14) + calc_length-1(4) = 18 candles consumed before first tmo_raw
    # Then smooth1 doesn't consume but starts from 0
    tmo_offset = 15  # length consumed before first raw value  # = 18

    swing_lows = find_swing_lows(lows, lookback=2)
    swing_highs = find_swing_highs(highs, lookback=2)

    last_candle_idx = len(candles) - 1
    signals = []

    def get_tmo_at_candle(candle_idx):
        """Get TMO value at a candle index, accounting for offset"""
        tmo_idx = candle_idx - tmo_offset
        if 0 <= tmo_idx < len(tmo):
            return tmo[tmo_idx]
        return None

    # ── Variante A: LONG (Lower Low + TMO Extreme) ──
    if len(swing_lows) >= 2:
        sl1_idx, sl1_price = swing_lows[-2]
        sl2_idx, sl2_price = swing_lows[-1]

        # 1. Lower Low
        if sl2_price < sl1_price:
            # 2. Second swing = confirmed within last 3 candles (2 needed for swing confirmation + 1 buffer)
            if last_candle_idx - sl2_idx <= 3:
                tmo_at_sl1 = get_tmo_at_candle(sl1_idx)
                tmo_at_sl2 = get_tmo_at_candle(sl2_idx)
                tmo_current = tmo[-1] if tmo else None
                tmo_prev = tmo[-2] if len(tmo) >= 2 else None

                # 3. TMO was at or below -9.7 at EITHER swing low
                if tmo_at_sl1 is not None and tmo_at_sl2 is not None:
                    if tmo_at_sl1 <= -TMO_EXTREME or tmo_at_sl2 <= -TMO_EXTREME:
                        # 4. TMO turning up
                        if tmo_current is not None and tmo_prev is not None and tmo_current > tmo_prev:
                            signals.append({
                                "direction": "LONG",
                                "variante": "A (LL + TMO Extreme)",
                                "swing1": sl1_price,
                                "swing2": sl2_price,
                                "swing1_idx": sl1_idx,
                                "swing2_idx": sl2_idx,
                                "tmo_at_swing1": tmo_at_sl1,
                                "tmo_at_swing2": tmo_at_sl2,
                                "tmo_current": tmo_current,
                                "pattern": "LL"
                            })

    # ── Variante A: SHORT (Higher High + TMO Extreme) ──
    if len(swing_highs) >= 2:
        sh1_idx, sh1_price = swing_highs[-2]
        sh2_idx, sh2_price = swing_highs[-1]

        # 1. Higher High
        if sh2_price > sh1_price:
            # 2. Second swing = confirmed within last 3 candles (2 needed for swing confirmation + 1 buffer)
            if last_candle_idx - sh2_idx <= 3:
                tmo_at_sh1 = get_tmo_at_candle(sh1_idx)
                tmo_at_sh2 = get_tmo_at_candle(sh2_idx)
                tmo_current = tmo[-1] if tmo else None
                tmo_prev = tmo[-2] if len(tmo) >= 2 else None

                # 3. TMO was at or above +9.7 at EITHER swing high
                if tmo_at_sh1 is not None and tmo_at_sh2 is not None:
                    if tmo_at_sh1 >= TMO_EXTREME or tmo_at_sh2 >= TMO_EXTREME:
                        # 4. TMO turning down
                        if tmo_current is not None and tmo_prev is not None and tmo_current < tmo_prev:
                            signals.append({
                                "direction": "SHORT",
                                "variante": "A (HH + TMO Extreme)",
                                "swing1": sh1_price,
                                "swing2": sh2_price,
                                "swing1_idx": sh1_idx,
                                "swing2_idx": sh2_idx,
                                "tmo_at_swing1": tmo_at_sh1,
                                "tmo_at_swing2": tmo_at_sh2,
                                "tmo_current": tmo_current,
                                "pattern": "HH"
                            })

    # ── Variante B: LONG (Bullish Divergence) ──
    if len(swing_lows) >= 2:
        sl1_idx, sl1_price = swing_lows[-2]
        sl2_idx, sl2_price = swing_lows[-1]

        # 1. Lower Low on price
        if sl2_price < sl1_price:
            # Within last 5 candles
            if last_candle_idx - sl2_idx <= 3:
                tmo_at_sl1 = get_tmo_at_candle(sl1_idx)
                tmo_at_sl2 = get_tmo_at_candle(sl2_idx)

                if tmo_at_sl1 is not None and tmo_at_sl2 is not None:
                    # 2. TMO at second low HIGHER than TMO at first low (divergence)
                    if tmo_at_sl2 > tmo_at_sl1:
                        # 3. TMO was in oversold zone at first low
                        if tmo_at_sl1 <= -TMO_EXTREME:
                            # Check not already detected as Variante A
                            already = any(s["direction"] == "LONG" and s["variante"].startswith("A") for s in signals)
                            if not already:
                                signals.append({
                                    "direction": "LONG",
                                    "variante": "B (Bullish Divergence)",
                                    "swing1": sl1_price,
                                    "swing2": sl2_price,
                                    "swing1_idx": sl1_idx,
                                    "swing2_idx": sl2_idx,
                                    "tmo_at_swing1": tmo_at_sl1,
                                    "tmo_at_swing2": tmo_at_sl2,
                                    "tmo_current": tmo[-1],
                                    "pattern": "LL"
                                })

    # ── Variante B: SHORT (Bearish Divergence) ──
    if len(swing_highs) >= 2:
        sh1_idx, sh1_price = swing_highs[-2]
        sh2_idx, sh2_price = swing_highs[-1]

        # 1. Higher High on price
        if sh2_price > sh1_price:
            # Within last 5 candles
            if last_candle_idx - sh2_idx <= 3:
                tmo_at_sh1 = get_tmo_at_candle(sh1_idx)
                tmo_at_sh2 = get_tmo_at_candle(sh2_idx)

                if tmo_at_sh1 is not None and tmo_at_sh2 is not None:
                    # 2. TMO at second high LOWER than TMO at first high (divergence)
                    if tmo_at_sh2 < tmo_at_sh1:
                        # 3. TMO was in overbought zone at first high
                        if tmo_at_sh1 >= TMO_EXTREME:
                            already = any(s["direction"] == "SHORT" and s["variante"].startswith("A") for s in signals)
                            if not already:
                                signals.append({
                                    "direction": "SHORT",
                                    "variante": "B (Bearish Divergence)",
                                    "swing1": sh1_price,
                                    "swing2": sh2_price,
                                    "swing1_idx": sh1_idx,
                                    "swing2_idx": sh2_idx,
                                    "tmo_at_swing1": tmo_at_sh1,
                                    "tmo_at_swing2": tmo_at_sh2,
                                    "tmo_current": tmo[-1],
                                    "pattern": "HH"
                                })

    return signals

# ── Format Signal Message ──────────────────────────────────────
def format_signal_message(coin, tf, direction, variante, entry, sl, tp1, tp2, tp3,
                          sl_margin_pct, leverage, tmo_val, pattern, swing1, swing2, sig_num):
    """Format the Telegram signal message"""
    sl_dist_pct = abs(entry - sl) / entry * 100

    # TP trailing SL adjustments (on margin basis)
    if direction == "LONG":
        tp1_trail_sl = entry * (1 + 0.025 / leverage)
        tp2_trail_sl = entry * (1 + 0.075 / leverage)
    else:
        tp1_trail_sl = entry * (1 - 0.025 / leverage)
        tp2_trail_sl = entry * (1 - 0.075 / leverage)

    fp = lambda p: fmt_price(p, coin)
    pattern_label = "Lower Low" if pattern == "LL" else "Higher High"

    msg = (
        f"🎯 KODA — Signal Engine\nEE2 SIGNAL #{sig_num} [{get_group(coin)}]\n\n"
        f"{coin}USDT — {direction}\n"
        f"📊 TF: {tf}\n"
        f"⚙️ Hebel: {get_leverage(coin, tf)}x\n\n"
        f"📍 Entry: ${fp(entry)}\n"
        f"🛑 SL: ${fp(sl)} ({sl_dist_pct:.1f}%)\n\n"
        f"💰 Auszahlungsplan:\n"
        f"TP1 (+15%): ${fp(tp1)} → 33.3% schließen, SL → +2.5%\n"
        f"TP2 (+22.5%): ${fp(tp2)} → 33.3% schließen, SL → +7.5%\n"
        f"TP3 (+30%): ${fp(tp3)} → Rest schließen\n\n"
        f"🔍 Setup: {variante} | TMO: {tmo_val:.2f}\n"
        f"📈 {pattern_label}: ${fp(swing1)} → ${fp(swing2)}"
    )
    return msg

# ── Save signals to JSON ───────────────────────────────────────
def save_signal(signal_data):
    """Append signal to ee2_signals.json"""
    try:
        if os.path.exists(SIGNALS_JSON):
            with open(SIGNALS_JSON, "r") as f:
                signals = json.load(f)
        else:
            signals = []
    except (json.JSONDecodeError, IOError):
        signals = []

    signals.append(signal_data)
    # Keep last 200
    if len(signals) > 200:
        signals = signals[-200:]

    os.makedirs(os.path.dirname(SIGNALS_JSON), exist_ok=True)
    with open(SIGNALS_JSON, "w") as f:
        json.dump(signals, f, indent=2)

# ── Main Scan ───────────────────────────────────────────────────
def run_scan():
    """Scan all coins x timeframes for EE2 signals"""
    log.info("=" * 60)
    log.info("Starting EE2 scan cycle")

    if not is_trading_time():
        log.info("Outside trading hours — skipping")
        return

    total_signals = 0

    for coin in COINS:
        for tf in TIMEFRAMES:
            # Check cooldown
            if not check_cooldown(coin, tf):
                log.info(f"  {coin} {tf}: cooldown active — skip")
                continue

            # Fetch data
            candles = fetch_klines(coin, tf, limit=50)
            if len(candles) < 30:
                log.warning(f"  {coin} {tf}: insufficient data ({len(candles)} candles)")
                continue

            # Detect signals
            detected = detect_signals(coin, tf, candles)
            if not detected:
                continue

            # Get leverage
            lev_map = LEVERAGE.get(tf, {"default": 12})
            lev = lev_map.get(coin, lev_map.get("default", 12))

            for sig in detected:
                entry = candles[-1]["close"]
                direction = sig["direction"]

                # Calculate TP/SL
                tp1, tp2, tp3, sl, sl_margin_pct = calc_tp_sl(
                    entry, direction, lev, candles,
                    swing_low=sig.get("swing2") if direction == "LONG" else None,
                    swing_high=sig.get("swing2") if direction == "SHORT" else None
                )

                # Skip if SL too wide (>25% margin)
                if sl_margin_pct > MAX_SL_MARGIN_PCT:
                    log.info(f"  {coin} {tf} {direction}: SL too wide ({sl_margin_pct:.1f}% > {MAX_SL_MARGIN_PCT}%) — SKIP")
                    continue

                sig_num = get_signal_number()
                total_signals += 1

                log.info(f"  SIGNAL #{sig_num}: {coin} {tf} {direction} | {sig['variante']} | TMO: {sig['tmo_current']:.2f}")

                # Format and send message
                msg = format_signal_message(
                    coin=coin, tf=tf, direction=direction,
                    variante=sig["variante"], entry=entry,
                    sl=sl, tp1=tp1, tp2=tp2, tp3=tp3,
                    sl_margin_pct=sl_margin_pct, leverage=lev,
                    tmo_val=sig["tmo_current"],
                    pattern=sig["pattern"],
                    swing1=sig["swing1"], swing2=sig["swing2"],
                    sig_num=sig_num
                )
                tg_broadcast(msg)

                # Save to JSON
                signal_data = {
                    "number": sig_num,
                    "coin": coin,
                    "tf": tf,
                    "direction": direction,
                    "variante": sig["variante"],
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1,
                    "tp2": tp2,
                    "tp3": tp3,
                    "sl_margin_pct": sl_margin_pct,
                    "leverage": lev,
                    "tmo": sig["tmo_current"],
                    "swing1": sig["swing1"],
                    "swing2": sig["swing2"],
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
                save_signal(signal_data)

                # Set cooldown
                set_cooldown(coin, tf)

            time.sleep(0.1)  # Rate limit between API calls

    log.info(f"Scan complete — {total_signals} signals detected")

# ── Main Loop ───────────────────────────────────────────────────
def main():
    log.info("EE2 Scanner started")
    tg_send(CHRIS_ID, "🟢 EE2 Scanner gestartet")

    while True:
        try:
            run_scan()
        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
            tg_send(CHRIS_ID, f"⚠️ EE2 Scanner Fehler: {e}")

        log.info("Sleeping 900s (15 min) until next scan")
        time.sleep(900)  # scan every 15 min

if __name__ == "__main__":
    main()
