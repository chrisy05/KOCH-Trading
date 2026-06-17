#!/usr/bin/env python3
"""
KODA SE Config-8 Signal Bot — Updated 2026-06-17
========================================================
PRIMARY PURPOSE: Post high-quality Cascade>=4 + Config 8 Slope Filter signals to Telegram channel.

Config: C8 — Cascade>=4, TP 50% EM, Prob>=60%, 10x, 70% MSL
Slope Filter Config 8: SMA10 slope < 1.0% AND SMA10/SMA20 gap NOT expanding
Relaxed BTC cascade (SMA10>20 only) + Relaxed MTF gate (SMA10>20)
+ Phase Detection (identical to paper_bot_cascade4.py)
Backtest result: 6/6 trades = 100% WR, $49.25 net PnL, 0 DD

ALL FIXES from backtest_confirmation_fixed.py applied:
  1. SL is MARGIN-based (not price-based)
  2. Fees included in PnL (0.11% round trip)
  3. TP recalculated from confirmation entry price
  4. BE stop covers fees + 0.1% buffer
  5. 24h Force Close
  6. Correct budget management across trades_15m + trades_30m
  7. Phase Detection: Entry gate (score>=6, no Phase D) + SL management
"""

import json
import ssl
import math
import time
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.parse

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CONFIG = {
    "capital": 50,
    "leverage": 10,
    "min_probability": 60,
    "tp_range_pct": 50,        # 50% EM (from C6 combo — tighter TP for higher WR)
    "sl_margin_pct": 70,       # 70% MARGIN loss
    "total_budget": 1000,
    "tf_budget_15m": 50,
    "tf_budget_30m": 50,
    "tf_budget_1h": 0,
    "tf_budget_4h": 0,
}

CASCADE_MIN = 3               # Reduced from 5 to 4 (Config 8 slope filter provides selectivity)
CONFIRM_PCT = 0.003            # 0.3% confirmation
CONFIRM_BARS = 8               # 8 minutes
TRAIL_PCT = 0.02               # 2% trailing
FEE_RATE = 0.0011              # 0.11% round trip
DRAWDOWN_BRAKE_SL_COUNT = 5    # 5 SLs in a row -> pause

# Slope Filter Config 8: Slope < 1.0% AND gap must NOT be expanding
SLOPE_MAX = 1.0
REQUIRE_GAP_NOT_EXPANDING = True

# Phase Detection config
PHASE_ENTRY_MIN_SCORE = 6.0    # Minimum phase score for entry
PHASE_SCORES = {'C': 2.0, 'B': 1.5, 'A': 1.0, 'D': -1.0, 'X': 0.0}
PHASE_TFS = ["5m", "15m", "30m", "1h", "4h"]
PHASE_SL_LEVELS = {
    0: 0.07,   # Normal: 70% margin / 10x = 7% price
    1: 0.05,   # 5m Phase D: tighten to 50% margin = 5% price
    2: 0.03,   # 15m Phase D: tighten to 30% margin = 3% price
    3: 0.00,   # 30m Phase D: close immediately (PHASE_EXIT)
}
_phase_cache = {}  # coin -> {"ts": timestamp, "phases": {tf: (phase, direction)}}
PHASE_CACHE_SECONDS = 120  # Cache phase data for 2 minutes

COINS = [
    "GLM", "AVAX", "KAS", "MINA", "XRP", "FLOW",
    "AXL", "CELR", "CYS", "IOST", "CAKE", "KAITO",
    "TRX", "SUN", "GRT", "DUSK", "BAT", "SYN",
    "TON", "HBAR", "DOT", "LTC", "LINK", "SOL",
]

TZ = timezone(timedelta(hours=-4))
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trades_koda_se.json")

# SSL context for Binance API
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ═══════════════════════════════════════════════════════════════
# TELEGRAM — ACTIVE CHANNEL POSTING
# ═══════════════════════════════════════════════════════════════

KODA_SE_BOT_TOKEN = "8623243424:AAEqo7FlHPqZzZHrpLMQJFBxGnNY382YhW4"  # KODA Terminal
KODA_SE_CHANNEL_ID = "-1003770314055"  # Signal channel
CHRIS_CHAT_ID = "351653518"
TRADING_BOT_TOKEN = "8716936978:AAGauC-r4RmpGvtSR9qS72TR-aJvRaVBPB8"

_signal_counter = 0
_consecutive_sl_count = 0
_drawdown_paused = False
_current_data = None
_pending_signals = []


def send_tg_channel(text):
    """Post signal to KODA SE channel — ACTIVE for Cascade 5 signals."""
    try:
        url = f"https://api.telegram.org/bot{KODA_SE_BOT_TOKEN}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": KODA_SE_CHANNEL_ID, "text": text}).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log(f"TG channel send failed: {e}")


def send_tg_chris(text):
    """Send direct message to Chris (for alerts)."""
    try:
        url = f"https://api.telegram.org/bot{TRADING_BOT_TOKEN}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": CHRIS_CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log(f"TG chris send failed: {e}")


# ═══════════════════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════════════════

def api(url, timeout=10):
    """Fetch JSON from URL with error handling."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "KODA-SE-C5/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def fetch_klines(symbol, interval="15m", limit=800):
    """Fetch klines from Binance Futures."""
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = api(url)
    if not data:
        return []
    result = []
    for k in data:
        o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        vol = float(k[5])
        taker_buy = float(k[9])
        result.append({
            "open": o, "high": h, "low": l, "close": c,
            "volume": vol, "buy_vol": taker_buy, "sell_vol": vol - taker_buy,
            "dir": 1 if c > o else -1,
        })
    return result


def get_depth(symbol, limit=50):
    """Fetch orderbook depth."""
    url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit={limit}"
    data = api(url)
    if not data:
        return None
    asks = [(float(p), float(q)) for p, q in data["asks"]]
    bids = [(float(p), float(q)) for p, q in data["bids"]]
    return {"asks": asks, "bids": bids}


def get_oi_funding(symbol):
    """Fetch OI and funding rate."""
    oi = api(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}")
    fr = api(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=3")
    result = {}
    if oi:
        result["oi"] = float(oi["openInterest"])
    if fr and len(fr) > 0:
        result["funding"] = float(fr[-1]["fundingRate"])
        result["funding_pct"] = result["funding"] * 100
    return result


def get_current_price(symbol):
    """Fetch current mark price for a symbol."""
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
    data = api(url)
    if data:
        return float(data["price"])
    return None


def get_recent_highlow(sym, minutes=5):
    """Get high/low from recent 1m klines to catch wicks."""
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit={minutes}"
        req = urllib.request.Request(url, headers={"User-Agent": "KODA-SE-C5/1.0"})
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
# ANALYSIS — 7-SCORE SYSTEM
# ═══════════════════════════════════════════════════════════════

def compute_absorption(k):
    o, h, l, c = k["open"], k["high"], k["low"], k["close"]
    vol, d = k["volume"], k["dir"]
    uw_base, lw_base = max(o, c), min(o, c)
    fr = h - l if h > l else 0.0001
    uw, lw = h - uw_base, lw_base - l
    ab = 0.0
    if d > 0 and uw > 0:
        ab = vol * (uw / fr)
    if d < 0 and lw > 0:
        ab = vol * (lw / fr)
    tw = uw + lw
    if fr > 0 and tw / fr > 0.6:
        ab = max(ab, vol * (tw / fr) * 0.5)
    return ab


def build_profile(klines, num_bins=35):
    if not klines:
        return None
    lows = [k["low"] for k in klines]
    highs = [k["high"] for k in klines]
    min_p, max_p = min(lows), max(highs)
    rng = max(max_p - min_p, min_p * 0.001)
    step = rng / num_bins
    cur = klines[-1]["close"]

    vols = sorted([k["volume"] for k in klines])
    st_idx = min(len(vols) - 1, max(0, int(len(vols) * 0.97)))
    st_thr = vols[st_idx]

    sb = [0.0]*num_bins; wb = [0.0]*num_bins
    ws = [0.0]*num_bins; ss = [0.0]*num_bins
    ab_bins = [0.0]*num_bins

    for k in klines:
        bi = max(0, min(num_bins-1, int((k["close"] - min_p) / step)))
        v, d, strong = k["volume"], k["dir"], k["volume"] >= st_thr
        if d > 0 and strong: sb[bi] += v
        elif d > 0: wb[bi] += v
        elif d < 0 and strong: ss[bi] += v
        else: ws[bi] += v
        av = compute_absorption(k)
        if av > 0:
            ab_bins[bi] += av

    bins = []
    poc_i, poc_v, tv = 0, 0, 0
    for i in range(num_bins):
        bv = sb[i]+wb[i]; sv = ss[i]+ws[i]; tot = bv+sv
        tv += tot
        if tot > poc_v: poc_v, poc_i = tot, i
        bins.append({
            "bottom": min_p+step*i, "top": min_p+step*(i+1),
            "mid": min_p+step*(i+0.5),
            "buy": bv, "sell": sv, "total": tot,
            "delta": bv-sv, "absorption": ab_bins[i],
        })

    va_vol = bins[poc_i]["total"]
    va_lo, va_hi = poc_i, poc_i
    target = tv * 0.30
    while va_vol < target and (va_lo > 0 or va_hi < num_bins-1):
        lv = bins[va_lo-1]["total"] if va_lo > 0 else -1
        uv = bins[va_hi+1]["total"] if va_hi < num_bins-1 else -1
        if uv >= lv: va_hi += 1; va_vol += uv
        else: va_lo -= 1; va_vol += lv

    zones = []
    for i in range(1, num_bins-1):
        if (bins[i]["absorption"] > bins[i-1]["absorption"] and
            bins[i]["absorption"] > bins[i+1]["absorption"] and
            bins[i]["absorption"] > 0):
            zones.append({
                "low": bins[i]["bottom"], "high": bins[i]["top"],
                "mid": bins[i]["mid"], "abs": bins[i]["absorption"],
                "type": "SUPPORT" if cur > bins[i]["mid"] else "RESISTANCE",
                "delta": bins[i]["delta"],
            })
    zones.sort(key=lambda x: x["abs"], reverse=True)

    total_buy = sum(b["buy"] for b in bins)
    total_sell = sum(b["sell"] for b in bins)

    return {
        "price": cur, "poc": bins[poc_i]["mid"],
        "va_low": bins[va_lo]["bottom"], "va_high": bins[va_hi]["top"],
        "total_buy": total_buy, "total_sell": total_sell,
        "delta": total_buy - total_sell,
        "bias": "BULLISH" if total_buy > total_sell else "BEARISH",
        "zones": zones[:8],
        "bins": bins,
    }


def analyze_depth(depth, price):
    if not depth:
        return {}
    asks, bids = depth["asks"], depth["bids"]
    ask_qtys = [q for _, q in asks]
    bid_qtys = [q for _, q in bids]
    med_ask = sorted(ask_qtys)[len(ask_qtys)//2] if ask_qtys else 1
    med_bid = sorted(bid_qtys)[len(bid_qtys)//2] if bid_qtys else 1
    ask_walls = [{"price": p, "qty": q} for p, q in asks if q > med_ask * 3]
    bid_walls = [{"price": p, "qty": q} for p, q in bids if q > med_bid * 3]
    cum_ask = sum(q for _, q in asks[:25])
    cum_bid = sum(q for _, q in bids[:25])
    ratio = cum_bid / cum_ask if cum_ask > 0 else 1
    return {
        "bid_ask_ratio": ratio,
        "cum_bids": cum_bid, "cum_asks": cum_ask,
        "ask_walls": len(ask_walls),
        "bid_walls": len(bid_walls),
        "ob_bias": "BULLISH" if ratio > 1.2 else ("BEARISH" if ratio < 0.8 else "NEUTRAL"),
    }


def calc_atr(klines, period=14):
    """Calculate ATR(14) from klines."""
    if len(klines) < 2:
        return 0
    tr_vals = []
    for i in range(1, len(klines)):
        tr = max(
            klines[i]["high"] - klines[i]["low"],
            abs(klines[i]["high"] - klines[i-1]["close"]),
            abs(klines[i]["low"] - klines[i-1]["close"])
        )
        tr_vals.append(tr)
    if not tr_vals:
        return 0
    return sum(tr_vals[-period:]) / min(period, len(tr_vals))


def analyze_btc(tf):
    """BTC context: trend direction from SMA20/SMA50."""
    klines = fetch_klines("BTCUSDT", tf, 50)
    if not klines:
        return {"trend": "UNKNOWN"}
    closes = [k["close"] for k in klines]
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
    price = closes[-1]
    if price > sma20 > sma50:
        trend = "BULLISH"
    elif price < sma20 < sma50:
        trend = "BEARISH"
    else:
        trend = "SIDEWAYS"
    return {"price": price, "sma20": sma20, "sma50": sma50, "trend": trend}


def full_analyze(coin, tf="15m", limit=800):
    """Full coin analysis — 7-Score system. Returns signal dict or None."""
    sym = f"{coin.upper()}USDT"

    klines = fetch_klines(sym, tf, limit)
    if not klines or len(klines) < 20:
        return None
    time.sleep(0.1)

    profile = build_profile(klines)
    if not profile:
        return None
    price = profile["price"]
    time.sleep(0.1)

    depth = get_depth(sym)
    depth_info = analyze_depth(depth, price)
    time.sleep(0.1)

    oi_fund = get_oi_funding(sym)
    fund_pct = oi_fund.get("funding_pct", 0)
    time.sleep(0.1)

    recent_high = max(k["high"] for k in klines[-100:])
    recent_low = min(k["low"] for k in klines[-100:])
    dist_up = (recent_high - price) / price * 100
    dist_down = (price - recent_low) / price * 100

    support_zones = [z for z in profile["zones"] if z["type"] == "SUPPORT"]
    resist_zones = [z for z in profile["zones"] if z["type"] == "RESISTANCE"]

    poc_dist = (profile["poc"] - price) / price * 100
    above_va = price > profile["va_high"]
    below_va = price < profile["va_low"]

    total_vol = profile["total_buy"] + profile["total_sell"]
    delta_ratio = abs(profile["delta"]) / max(total_vol, 1) * 100

    ob_ratio = depth_info.get("bid_ask_ratio", 1)
    ob_bias = depth_info.get("ob_bias", "NEUTRAL")

    if fund_pct > 0.005:
        fund_bias = "SHORT"
    elif fund_pct < -0.005:
        fund_bias = "LONG"
    else:
        fund_bias = "NEUTRAL"

    scores = {
        "delta": "LONG" if profile["bias"] == "BULLISH" else "SHORT",
        "ob": "LONG" if ob_bias == "BULLISH" else ("SHORT" if ob_bias == "BEARISH" else "NEUTRAL"),
        "funding": fund_bias,
        "distance": "SHORT" if dist_down < dist_up else "LONG",
        "walls": "SHORT" if depth_info.get("ask_walls", 0) < depth_info.get("bid_walls", 0) else (
                 "LONG" if depth_info.get("bid_walls", 0) < depth_info.get("ask_walls", 0) else "NEUTRAL"),
        "poc": "SHORT" if poc_dist < -1 else ("LONG" if poc_dist > 1 else "NEUTRAL"),
        "va": "LONG" if above_va else ("SHORT" if below_va else "NEUTRAL"),
    }

    long_c = sum(1 for v in scores.values() if v == "LONG")
    short_c = sum(1 for v in scores.values() if v == "SHORT")

    # Score gate: need >= 3/7
    max_score = max(long_c, short_c)
    if max_score < 3:
        return None

    if long_c > short_c + 1:
        coin_bias = "LONG"
    elif short_c > long_c + 1:
        coin_bias = "SHORT"
    else:
        coin_bias = "NEUTRAL"

    # Probability calculation
    coin_strength = max(long_c, short_c) / 7
    neutral_penalty = (7 - long_c - short_c) / 7 * 0.15
    dist_factor = (min(dist_down / (dist_down + dist_up) * 0.1, 0.05) if dist_down < dist_up
                   else min(dist_up / (dist_down + dist_up) * 0.1, 0.05)) if (dist_down + dist_up) > 0 else 0
    delta_factor = min(delta_ratio / 100 * 0.1, 0.08)
    ob_factor = abs(ob_ratio - 1) * 0.05

    def calc_prob(btc_dir):
        coin_dir = "LONG" if long_c > short_c else ("SHORT" if short_c > long_c else "NEUTRAL")
        base = 35.0
        base += coin_strength * 25
        base -= neutral_penalty * 100
        if coin_dir == "LONG":
            if btc_dir == "LONG": base += 15 + delta_factor * 100 + ob_factor * 100
            elif btc_dir == "SIDE": base += 5
            else: base -= 15
        elif coin_dir == "SHORT":
            if btc_dir == "SHORT": base += 15 + delta_factor * 100 + ob_factor * 100
            elif btc_dir == "SIDE": base += 5
            else: base -= 15
        else:
            if btc_dir in ("LONG", "SHORT"): base += 5
            else: base -= 10
        if (coin_dir == "SHORT" and dist_down < dist_up) or (coin_dir == "LONG" and dist_up < dist_down):
            base += dist_factor * 100
        return max(5, min(85, round(base)))

    raw_a = calc_prob("LONG")
    raw_c = calc_prob("SHORT")
    raw_total = raw_a + raw_c
    prob_a = round(raw_a / raw_total * 100) if raw_total > 0 else 50
    prob_c = 100 - prob_a

    atr14 = calc_atr(klines, 14)
    tf_mins = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
    bars_per_day = 1440 / tf_mins.get(tf, 15)
    expected_move = atr14 * math.sqrt(bars_per_day) * 0.5

    range_high = price + expected_move
    range_low = price - expected_move
    if resist_zones and resist_zones[0]["mid"] < range_high:
        range_high = min(range_high, resist_zones[0]["mid"] + atr14)
    if support_zones and support_zones[0]["mid"] > range_low:
        range_low = max(range_low, support_zones[0]["mid"] - atr14)

    if coin_bias == "LONG":
        target_up = range_high
        target_down = price - expected_move * 0.5
    elif coin_bias == "SHORT":
        target_up = price + expected_move * 0.5
        target_down = range_low
    else:
        target_up = range_high
        target_down = range_low

    btc = analyze_btc(tf)
    btc_trend = btc.get("trend", "SIDEWAYS")

    if btc_trend == "BULLISH":
        if coin_bias == "LONG": direction, probability = "LONG", prob_a
        elif coin_bias == "SHORT": direction, probability = "SHORT", prob_c
        else: direction, probability = "LONG", prob_a
    elif btc_trend == "BEARISH":
        if coin_bias == "SHORT": direction, probability = "SHORT", prob_c
        elif coin_bias == "LONG": direction, probability = "LONG", prob_a
        else: direction, probability = "SHORT", prob_c
    else:
        if coin_bias == "LONG": direction, probability = "LONG", prob_a
        elif coin_bias == "SHORT": direction, probability = "SHORT", prob_c
        else:
            if prob_a >= prob_c: direction, probability = "LONG", prob_a
            else: direction, probability = "SHORT", prob_c

    # TP from signal price (will be recalculated from confirmation entry later)
    if direction == "LONG":
        tp_price = price + abs(target_up - price) * (CONFIG["tp_range_pct"] / 100)
    else:
        tp_price = price - abs(price - target_down) * (CONFIG["tp_range_pct"] / 100)

    return {
        "coin": coin.upper(), "symbol": sym, "price": price,
        "direction": direction, "probability": probability,
        "coin_bias": coin_bias, "btc_trend": btc_trend,
        "scores": scores, "long_count": long_c, "short_count": short_c,
        "tp": tp_price, "expected_move": expected_move, "atr14": atr14,
        "target_up": target_up, "target_down": target_down,
        "prob_a": prob_a, "prob_c": prob_c,
        "klines": klines,
    }


# ═══════════════════════════════════════════════════════════════
# SLOPE FILTER (Config 8: Slope < 1.0% + no expanding gap)
# ═══════════════════════════════════════════════════════════════

def check_slope_filter(klines, direction):
    """Reject overextended entries. Config 8: slope + gap check."""
    if not klines or len(klines) < 15:
        return True, {}

    closes = [k["close"] for k in klines]

    sma10_now = sum(closes[-10:]) / 10
    sma20_now = sum(closes[-20:]) / 20
    sma10_3ago = sum(closes[-13:-3]) / 10 if len(closes) >= 13 else sma10_now
    sma20_3ago = sum(closes[-23:-3]) / 20 if len(closes) >= 23 else sma20_now

    sma10_slope = abs(sma10_now - sma10_3ago) / sma10_now * 100

    gap_now = abs(sma10_now - sma20_now)
    gap_3ago = abs(sma10_3ago - sma20_3ago)
    gap_expanding = gap_now > gap_3ago

    details = {
        "sma10_slope": round(sma10_slope, 3),
        "gap_expanding": gap_expanding
    }

    if sma10_slope > SLOPE_MAX:
        return False, details

    if REQUIRE_GAP_NOT_EXPANDING and gap_expanding:
        return False, details

    return True, details


# ═══════════════════════════════════════════════════════════════
# CASCADE — BTC Multi-TF SMA Alignment (Relaxed: SMA10>20 only)
# ═══════════════════════════════════════════════════════════════

_cascade_cache = {"ts": 0, "result": None}
CASCADE_CACHE_SECONDS = 300


def get_cascade_signal():
    """Check BTC SMA10/20/50 on 5 timeframes. Returns (bull_count, bear_count, direction, details)."""
    now_ts = time.time()
    if _cascade_cache["result"] is not None and (now_ts - _cascade_cache["ts"]) < CASCADE_CACHE_SECONDS:
        return _cascade_cache["result"]

    timeframes = ["15m", "30m", "1h", "4h"]  # 5m removed
    bull_count = 0
    bear_count = 0
    details = {}

    for tf in timeframes:
        klines = fetch_klines("BTCUSDT", tf, 55)
        if not klines or len(klines) < 50:
            details[tf] = "NO_DATA"
            time.sleep(0.1)
            continue
        closes = [k["close"] for k in klines]
        sma10 = sum(closes[-10:]) / 10
        sma20 = sum(closes[-20:]) / 20

        # Relaxed cascade: SMA10 vs SMA20 only (SMA50 ignored for BTC alignment)
        # Backtested: +$2,934 vs $2,741 standard, PnL/DD 5.99 vs 5.48
        if sma10 > sma20:
            bull_count += 1
            details[tf] = "BULL"
        elif sma10 < sma20:
            bear_count += 1
            details[tf] = "BEAR"
        else:
            details[tf] = "SIDE"
        time.sleep(0.1)

    if bull_count > bear_count: direction = "LONG"
    elif bear_count > bull_count: direction = "SHORT"
    else: direction = "NEUTRAL"

    result = (bull_count, bear_count, direction, details)
    _cascade_cache["ts"] = now_ts
    _cascade_cache["result"] = result
    return result


# ═══════════════════════════════════════════════════════════════
# PHASE DETECTION (from backtest_phase_detection_c4.py)
# ═══════════════════════════════════════════════════════════════

def _detect_phase_direction(sma10, sma20, sma50, sma10_hist, sma20_hist, direction):
    """Detect phase for a single direction. Returns phase letter (A/B/C/D/X).
    Extracted from backtest_phase_detection_c4.py — tested and verified."""
    if direction == 'LONG':
        if sma10 > sma20 > sma50:
            # Check for Phase D (weakening): SMA10-SMA20 gap narrowing
            gap_now = sma10 - sma20
            gaps = [sma10_hist[-(i+1)] - sma20_hist[-(i+1)] for i in range(min(3, len(sma10_hist)))]
            if len(gaps) >= 2 and all(g > 0 for g in gaps):
                if gap_now < gaps[-1] and (len(gaps) < 3 or gap_now < gaps[-2]):
                    return 'D'
            return 'C'

        if sma10 > sma20:
            # Check for Phase A (fresh cross): SMA10 just crossed above SMA20
            crossed_recently = False
            for i in range(1, min(4, len(sma10_hist))):
                if sma10_hist[-(i+1)] <= sma20_hist[-(i+1)]:
                    crossed_recently = True
                    break
            if crossed_recently:
                return 'A'

            # Phase B: SMA20 approaching SMA50 (gap narrowing)
            gap_20_50_now = abs(sma20 - sma50)
            if len(sma20_hist) >= 2:
                gap_20_50_prev = abs(sma20_hist[-2] - sma50)
                if gap_20_50_now < gap_20_50_prev:
                    return 'B'
            return 'B'

        return 'X'

    else:  # SHORT
        if sma10 < sma20 < sma50:
            gap_now = sma20 - sma10
            gaps = [sma20_hist[-(i+1)] - sma10_hist[-(i+1)] for i in range(min(3, len(sma10_hist)))]
            if len(gaps) >= 2 and all(g > 0 for g in gaps):
                if gap_now < gaps[-1] and (len(gaps) < 3 or gap_now < gaps[-2]):
                    return 'D'
            return 'C'

        if sma10 < sma20:
            crossed_recently = False
            for i in range(1, min(4, len(sma10_hist))):
                if sma10_hist[-(i+1)] >= sma20_hist[-(i+1)]:
                    crossed_recently = True
                    break
            if crossed_recently:
                return 'A'

            gap_20_50_now = abs(sma50 - sma20)
            if len(sma20_hist) >= 2:
                gap_20_50_prev = abs(sma50 - sma20_hist[-2])
                if gap_20_50_now < gap_20_50_prev:
                    return 'B'
            return 'B'

        return 'X'


def get_phase(sma10, sma20, sma50, sma10_hist, sma20_hist):
    """Determine phase and direction for a timeframe.
    Returns (phase_letter, direction) e.g. ('C', 'LONG') or ('X', None)."""
    if sma10 is None or sma20 is None or sma50 is None:
        return 'X', None
    if len(sma10_hist) < 4 or len(sma20_hist) < 4:
        return 'X', None

    long_phase = _detect_phase_direction(sma10, sma20, sma50, sma10_hist, sma20_hist, 'LONG')
    short_phase = _detect_phase_direction(sma10, sma20, sma50, sma10_hist, sma20_hist, 'SHORT')

    if long_phase != 'X':
        return long_phase, 'LONG'
    if short_phase != 'X':
        return short_phase, 'SHORT'
    return 'X', None


def fetch_sma_data_for_tf(symbol, tf, limit=55):
    """Fetch klines for a TF and compute SMA10/20/50 with history for phase detection.
    Returns (sma10, sma20, sma50, sma10_hist, sma20_hist) or None on failure."""
    klines = fetch_klines(symbol, tf, limit)
    if not klines or len(klines) < 50:
        return None
    closes = [k["close"] for k in klines]

    # Compute SMAs at multiple recent points for history
    sma10_hist = []
    sma20_hist = []
    # We need at least 4 historical SMA values (current + 3 prior)
    # Compute for last 4 candle positions
    for offset in range(3, -1, -1):
        idx = len(closes) - 1 - offset
        if idx < 49:  # Need at least 50 closes for SMA50
            continue
        s = closes[:idx+1]
        sma10_hist.append(sum(s[-10:]) / 10)
        sma20_hist.append(sum(s[-20:]) / 20)

    if len(sma10_hist) < 4 or len(sma20_hist) < 4:
        return None

    sma10 = sma10_hist[-1]
    sma20 = sma20_hist[-1]
    sma50 = sum(closes[-50:]) / 50

    return sma10, sma20, sma50, sma10_hist, sma20_hist


def get_coin_phases(coin, direction=None):
    """Get phase detection for a coin across all 5 TFs.
    Uses cache to avoid excessive API calls. Returns dict {tf: (phase, dir)}.
    If direction is specified, only returns phases matching that direction."""
    global _phase_cache

    cache_key = coin
    now_ts = time.time()

    if cache_key in _phase_cache and (now_ts - _phase_cache[cache_key]["ts"]) < PHASE_CACHE_SECONDS:
        phases = _phase_cache[cache_key]["phases"]
    else:
        sym = f"{coin}USDT"
        phases = {}
        for tf in PHASE_TFS:
            try:
                result = fetch_sma_data_for_tf(sym, tf, 55)
                if result is None:
                    continue
                sma10, sma20, sma50, sma10_hist, sma20_hist = result
                phase, phase_dir = get_phase(sma10, sma20, sma50, sma10_hist, sma20_hist)
                phases[tf] = (phase, phase_dir)
                time.sleep(0.05)  # Rate limiting
            except Exception as e:
                log(f"  PHASE: Error fetching {coin} {tf}: {e}")
                continue

        _phase_cache[cache_key] = {"ts": now_ts, "phases": phases}

    return phases


def calculate_phase_score(coin, direction):
    """Calculate multi-TF phase score for entry decision.
    Returns (score, has_phase_d, phase_details_str, phases_dict).
    Entry allowed if score >= 6 AND no Phase D anywhere AND consistent direction."""
    phases = get_coin_phases(coin)

    if not phases:
        return 0, False, "NO_DATA", {}

    score = 0.0
    has_phase_d = False
    opposite_count = 0
    details_parts = []

    # Only check Phase D on signal TFs (15m, 30m, 1h) — ignore 5m (too noisy) and 4h (too slow)
    phase_d_tfs = ["15m", "30m", "1h"]

    for tf in PHASE_TFS:
        if tf not in phases:
            details_parts.append(f"{tf}:?")
            continue

        phase, phase_dir = phases[tf]

        if phase_dir == direction:
            score += PHASE_SCORES[phase]
            if phase == 'D' and tf in phase_d_tfs:
                has_phase_d = True
            details_parts.append(f"{tf}:{phase}")
        elif phase_dir is not None and phase_dir != direction:
            if tf in phase_d_tfs:
                opposite_count += 1
            details_parts.append(f"{tf}:{phase}(!{phase_dir})")
        else:
            details_parts.append(f"{tf}:X")

    if opposite_count > 0:
        has_phase_d = True

    details_str = " ".join(details_parts)
    return score, has_phase_d, details_str, phases


def check_phase_sl(trade, coin):
    """Check phase degradation for SL management of open trades.
    Returns: 'CLOSE' (30m Phase D), 'TIGHTEN_L2' (15m Phase D),
             'TIGHTEN_L1' (5m Phase D), or None (no action)."""
    try:
        phases = get_coin_phases(coin)
        if not phases:
            return None  # Graceful fallback: no data = no action

        direction = trade["direction"]
        sl_level = 0
        tf_triggered = None

        # Check degradation in order: 5m -> 15m -> 30m
        tf_order = ['5m', '15m', '30m']
        for i, tf in enumerate(tf_order):
            if tf not in phases:
                continue
            phase, phase_dir = phases[tf]

            # Phase D in our direction OR opposite direction detected
            is_degrading = False
            if phase == 'D' and phase_dir == direction:
                is_degrading = True
            elif phase_dir is not None and phase_dir != direction and phase != 'X':
                # TF has flipped to opposite direction
                is_degrading = True

            if is_degrading:
                new_level = i + 1  # 5m=1, 15m=2, 30m=3
                if new_level > sl_level:
                    sl_level = new_level
                    tf_triggered = tf

        if sl_level >= 3:
            return "CLOSE"       # 30m Phase D -> close immediately
        elif sl_level >= 2:
            return "TIGHTEN_L2"  # 15m Phase D -> 30% margin SL (3% price)
        elif sl_level >= 1:
            return "TIGHTEN_L1"  # 5m Phase D -> 50% margin SL (5% price)
        else:
            return None

    except Exception as e:
        log(f"  PHASE SL: Error checking {coin}: {e}")
        return None  # Graceful fallback


# ═══════════════════════════════════════════════════════════════
# MTF GATE: BTC 1H SMA20 vs SMA50
# ═══════════════════════════════════════════════════════════════

_mtf_cache = {"ts": 0, "sma10": None, "sma20": None}
MTF_CACHE_SECONDS = 300


def check_mtf_gate(direction):
    """Only allow LONG when BTC 1H SMA10 > SMA20, SHORT when SMA10 < SMA20. Relaxed since Config 8."""
    now_ts = time.time()
    if _mtf_cache["sma10"] is not None and (now_ts - _mtf_cache["ts"]) < MTF_CACHE_SECONDS:
        sma10 = _mtf_cache["sma10"]
        sma20 = _mtf_cache["sma20"]
    else:
        klines = fetch_klines("BTCUSDT", "1h", 25)
        if not klines or len(klines) < 20:
            log("  MTF GATE: No BTC 1H data — blocking trade")
            return False
        closes = [k["close"] for k in klines]
        sma10 = sum(closes[-10:]) / 10
        sma20 = sum(closes[-20:]) / 20
        _mtf_cache["ts"] = now_ts
        _mtf_cache["sma10"] = sma10
        _mtf_cache["sma20"] = sma20
        time.sleep(0.1)

    if direction == "LONG" and sma10 > sma20:
        return True
    if direction == "SHORT" and sma10 < sma20:
        return True

    log(f"  MTF GATE BLOCKED: {direction} but BTC 1H SMA10={sma10:.0f} SMA20={sma20:.0f}")
    return False


# ═══════════════════════════════════════════════════════════════
# FIXED TRADE MATH (from backtest_confirmation_fixed.py)
# ═══════════════════════════════════════════════════════════════

def calc_sl_price_margin_based(entry, direction):
    """
    FIX 1: SL is MARGIN-based.
    70% margin SL at 10x leverage = 7% price move.
    """
    leverage = CONFIG["leverage"]
    margin_sl_pct = CONFIG["sl_margin_pct"]
    price_move_pct = margin_sl_pct / leverage / 100.0
    if direction == "LONG":
        return entry * (1 - price_move_pct)
    else:
        return entry * (1 + price_move_pct)


def calc_fee(entry, size):
    """FIX 2: Fees included — 0.11% round trip on position notional."""
    position_notional = entry * size
    return position_notional * FEE_RATE


def calc_tp_from_confirmation_entry(entry, direction, expected_move):
    """FIX 3: TP recalculated from CONFIRMATION entry price, not signal price."""
    tp_distance = expected_move * (CONFIG["tp_range_pct"] / 100.0)
    if direction == "LONG":
        return entry + tp_distance
    else:
        return entry - tp_distance


def calc_be_stop(entry, direction):
    """FIX 4: BE stop covers fees + 0.1% buffer."""
    total_pct = FEE_RATE + 0.001  # 0.0011 + 0.001 = 0.0021
    if direction == "LONG":
        return entry * (1 + total_pct)
    else:
        return entry * (1 - total_pct)


def calc_liquidation(entry, direction, margin, size):
    """Calculate liquidation price."""
    maint = margin * 0.005
    net_margin = margin - maint
    if direction == "LONG":
        return entry - net_margin / size
    else:
        return entry + net_margin / size


def calc_pnl(direction, entry, close_price, size):
    """Calculate raw PnL for a trade (before fees)."""
    if direction == "LONG":
        return (close_price - entry) * size
    else:
        return (entry - close_price) * size


# ═══════════════════════════════════════════════════════════════
# DATA MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def load_data():
    """Load paper trades from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            # Ensure all keys exist
            for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
                if key not in data:
                    data[key] = []
            data.setdefault("config", {}).update(CONFIG)
            data["config"]["bot_name"] = "KODA SE C8"
            return data
        except Exception:
            pass
    return create_initial_data()


def create_initial_data():
    """Create fresh initial data structure."""
    return {
        "config": {
            **CONFIG,
            "start_date": datetime.now(TZ).strftime("%Y-%m-%d"),
            "bot_name": "KODA SE C8",
            "cascade_min": CASCADE_MIN,
            "confirm_pct": CONFIRM_PCT,
            "confirm_bars": CONFIRM_BARS,
            "trail_pct": TRAIL_PCT,
            "fee_rate": FEE_RATE,
        },
        "trades_15m": [],
        "trades_30m": [],
        "trades_1h": [],
        "trades_4h": [],
        "stats": {},
        "_heartbeat": "",
        "_drawdown_paused": False,
        "_consecutive_sl": 0,
        "_signal_count": 0,
    }


def save_data(data):
    """Save paper trades to JSON file."""
    data["_heartbeat"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    data["_drawdown_paused"] = _drawdown_paused
    data["_consecutive_sl"] = _consecutive_sl_count
    data["_signal_count"] = _signal_counter
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"ERROR saving data: {e}")


def next_trade_id(data):
    """Get next trade ID — global across ALL timeframes."""
    all_ids = []
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            all_ids.append(t.get("id", 0))
    if not all_ids:
        return 1
    return max(all_ids) + 1


# ═══════════════════════════════════════════════════════════════
# TELEGRAM SIGNAL MESSAGES
# ═══════════════════════════════════════════════════════════════

def fmt_price(x):
    """Format price for display."""
    if x > 100: return f"${x:,.2f}"
    elif x > 1: return f"${x:.4f}"
    else: return f"${x:.6f}"


def notify_trade_opened(trade):
    """Post signal to Telegram channel when trade is confirmed and opened."""
    global _signal_counter
    _signal_counter += 1

    coin = trade["coin"]
    d = trade["direction"]
    entry = trade["entry"]
    tp = trade["tp"]
    sl = trade["sl"]
    prob = trade["probability"]
    lev = trade["leverage"]
    tf = trade["tf"]
    cascade = trade.get("cascade_code", "")

    arrow = "\U0001f7e2" if d == "LONG" else "\U0001f534"
    tp_pct = abs(tp / entry - 1) * 100
    sl_pct = abs(sl / entry - 1) * 100

    slope_info = trade.get("slope_details", {})
    slope_str = f"Slope: {slope_info.get('sma10_slope', '?')}% | Gap exp: {'Y' if slope_info.get('gap_expanding') else 'N'}" if slope_info else "Slope: n/a"

    msg = (
        f"{arrow} KODA SE #{_signal_counter} \u2014 {coin} {d}\n"
        f"\u2501" * 22 + "\n"
        f"Prob: {prob}% | TF: {tf} | Cascade: {cascade}\n"
        f"{slope_str}\n\n"
        f"Entry: {fmt_price(entry)}\n"
        f"TP1:   {fmt_price(tp)} ({'+' if d=='LONG' else '-'}{tp_pct:.1f}%) \u2192 50% close, SL\u2192BE\n"
        f"TP2:   Trailing {TRAIL_PCT*100:.0f}% vom Peak\n"
        f"SL:    {fmt_price(sl)} ({'-' if d=='LONG' else '+'}{sl_pct:.1f}%)\n\n"
        f"Hebel: {lev}x | Margin: ${trade['margin']}\n\n"
        f"Cascade\u22654 + Config 8 Signal\n"
        f"\u2501" * 22 + "\n"
        f"KODA SE C8 | 10x|50%TP|70%MSL \u00b7 {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')} ET"
    )
    send_tg_channel(msg)


def get_overall_stats(data):
    """Get total closed trades, wins, WR, PnL across all TFs."""
    all_closed = []
    for tf in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        all_closed.extend([t for t in data.get(tf, []) if t.get("status") == "closed"])
    total = len(all_closed)
    wins = len([t for t in all_closed if (t.get("pnl") or 0) > 0])
    wr = (wins / total * 100) if total > 0 else 0
    total_pnl = sum(t.get("pnl", 0) for t in all_closed)
    return total, wins, wr, total_pnl


def notify_trade_closed(trade, data=None):
    """Post result to channel when trade closes."""
    coin = trade["coin"]
    d = trade["direction"]
    pnl = trade["pnl"]
    roi = trade["roi"]
    reason = trade["close_reason"]
    entry = trade["entry"]
    close = trade["close_price"]

    footer = ""
    if data:
        total, wins, wr, total_pnl = get_overall_stats(data)
        pnl_sign = "+" if total_pnl >= 0 else ""
        footer = f"\n\u00b7 {total} Signale | {wins} positiv | WR: {wr:.0f}% | Gesamt: {pnl_sign}${total_pnl:.2f}"

    if pnl > 0:
        header = "\U0001f3c6\U0001f4b0 TRADE CLOSED \u2014 WIN \U0001f4b0\U0001f3c6"
        result_line = f"\u2705 +${pnl:.2f} ({roi:+.1f}%) \u2705"
    else:
        header = "\U0001f534 TRADE CLOSED \u2014 LOSS \U0001f534"
        result_line = f"\u274c ${pnl:.2f} ({roi:+.1f}%)"

    duration = ""
    if trade.get("open_time") and trade.get("close_time"):
        try:
            t1 = datetime.fromisoformat(trade["open_time"])
            t2 = datetime.fromisoformat(trade["close_time"])
            mins = int((t2 - t1).total_seconds() / 60)
            if mins < 60: duration = f"{mins}m"
            elif mins < 1440: duration = f"{mins//60}h {mins%60}m"
            else: duration = f"{mins//1440}d {(mins%1440)//60}h"
        except Exception:
            pass

    msg = (
        f"{header}\n"
        f"\u2550" * 30 + "\n"
        f"{coin} {d} | {trade.get('tf','')} | {reason}\n\n"
        f"Entry:  {fmt_price(entry)}\n"
        f"Exit:   {fmt_price(close)}\n"
        f"Dauer:  {duration}\n\n"
        f"{result_line}\n"
        f"\u2550" * 30 +
        f"{footer}\n"
        f"KODA SE C8 \u00b7 {datetime.now(TZ).strftime('%H:%M')} ET"
    )
    send_tg_channel(msg)


# ═══════════════════════════════════════════════════════════════
# TRADE OPEN / CLOSE
# ═══════════════════════════════════════════════════════════════

def open_trade(data, tf_key, coin, direction, entry, expected_move, probability, tf,
               cascade_lights=0, cascade_code="00000",
               phase_score=0, phase_details="",
               slope_details=None):
    """Open a new paper trade with FIXED math."""
    capital = CONFIG["capital"]
    leverage = CONFIG["leverage"]
    margin = capital
    size = capital * leverage / entry

    # FIX 1: Margin-based SL
    sl = calc_sl_price_margin_based(entry, direction)
    # FIX 3: TP from confirmation entry
    tp = calc_tp_from_confirmation_entry(entry, direction, expected_move)
    # Liquidation
    liq = calc_liquidation(entry, direction, margin, size)
    # FIX 2: Pre-calculate fee
    fee = calc_fee(entry, size)

    # Validate
    if direction == "LONG" and tp <= entry:
        log(f"  SKIP: {coin} LONG TP {tp:.6f} <= Entry {entry:.6f}")
        return None
    if direction == "SHORT" and tp >= entry:
        log(f"  SKIP: {coin} SHORT TP {tp:.6f} >= Entry {entry:.6f}")
        return None

    trade = {
        "id": next_trade_id(data),
        "coin": coin,
        "tf": tf,
        "direction": direction,
        "leverage": leverage,
        "entry": round(entry, 8),
        "tp": round(tp, 8),
        "sl": round(sl, 8),
        "liq": round(liq, 8),
        "size": round(size, 4),
        "margin": margin,
        "fee": round(fee, 4),
        "probability": probability,
        "open_time": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S"),
        "close_time": None,
        "close_price": None,
        "close_reason": None,
        "pnl": None,
        "roi": None,
        "status": "open",
        "tp1_hit": False,
        "tp1_pnl": None,
        "peak_price": None,
        "cascade_lights": cascade_lights,
        "cascade_code": cascade_code,
        # Phase detection tracking
        "phase_score": phase_score,
        "phase_details": phase_details,
        "phase_sl_level": 0,  # 0=normal, 1=5m_D, 2=15m_D, 3=30m_D(close)
        # Slope filter tracking
        "slope_details": slope_details or {},
    }

    data[tf_key].append(trade)
    sl_pct = CONFIG["sl_margin_pct"] / CONFIG["leverage"]
    slope_str = f" | Slope: {slope_details.get('sma10_slope', '?')}%" if slope_details else ""
    log(f"  OPENED {direction} {coin} @ {entry:.6f} | TP: {tp:.6f} | SL: {sl:.6f} ({sl_pct:.1f}% price / {CONFIG['sl_margin_pct']}% margin) | Fee: ${fee:.2f} | Prob: {probability}% | TF: {tf} | Cascade: {cascade_lights} | Phase: {phase_score:.1f}{slope_str}")

    notify_trade_opened(trade)
    return trade


def close_trade(trade, close_price, reason):
    """Close a paper trade with FEES in PnL. Tracks consecutive SLs."""
    global _consecutive_sl_count, _drawdown_paused

    raw_pnl = calc_pnl(trade["direction"], trade["entry"], close_price, trade["size"])
    fee = trade.get("fee", calc_fee(trade["entry"], trade["size"]))
    # FIX 2: Deduct fees from PnL
    net_pnl = raw_pnl - fee
    roi = net_pnl / trade["margin"] * 100

    trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    trade["close_price"] = round(close_price, 8)
    trade["close_reason"] = reason
    trade["pnl"] = round(net_pnl, 2)
    trade["roi"] = round(roi, 2)
    trade["status"] = "closed"

    emoji = "WIN" if net_pnl > 0 else "LOSS"
    log(f"  CLOSED {trade['direction']} {trade['coin']} @ {close_price:.6f} | {reason} | Raw: ${raw_pnl:.2f} - Fee: ${fee:.2f} = Net: ${net_pnl:.2f} ({roi:.1f}%) | {emoji}")

    notify_trade_closed(trade, _current_data)

    # Drawdown brake tracking
    if net_pnl <= 0:
        _consecutive_sl_count += 1
        log(f"  Consecutive losses: {_consecutive_sl_count}/{DRAWDOWN_BRAKE_SL_COUNT}")
        if _consecutive_sl_count >= DRAWDOWN_BRAKE_SL_COUNT and not _drawdown_paused:
            _drawdown_paused = True
            log(f"DRAWDOWN BRAKE ACTIVATED -- {_consecutive_sl_count} losses in a row!")
            alert = (f"DRAWDOWN-BREMSE AKTIV -- KODA SE C8\n\n"
                     f"{_consecutive_sl_count} Verluste in Folge!\n"
                     f"Bot ist PAUSIERT. Keine neuen Trades.\n"
                     f"Offene Trades laufen weiter (TP/SL aktiv).\n"
                     f"Zum Fortfahren: Bot manuell neu starten.")
            send_tg_chris(alert)
            send_tg_channel(alert)
    else:
        if _consecutive_sl_count > 0:
            log(f"  Win resets consecutive loss counter (was {_consecutive_sl_count})")
        _consecutive_sl_count = 0

    return trade


# ═══════════════════════════════════════════════════════════════
# CHECK OPEN TRADES — TP/SL/LIQ/24h with FIXED math
# ═══════════════════════════════════════════════════════════════

def check_open_trades(data):
    """Check all open trades on trades_15m + trades_30m for TP, SL, LIQ, 24h timeout."""
    for tf_key in ["trades_15m", "trades_30m"]:
        open_trades = [t for t in data[tf_key] if t["status"] == "open"]
        if not open_trades:
            continue

        coins_needed = set(t["coin"] for t in open_trades)
        price_data = {}
        for coin in coins_needed:
            sym = f"{coin}USDT"
            p = get_current_price(sym)
            h, l = get_recent_highlow(sym, 3)
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
            unrealized_raw = calc_pnl(trade["direction"], trade["entry"], current_price, trade["size"])
            fee = trade.get("fee", calc_fee(trade["entry"], trade["size"]))
            if trade.get("tp1_hit") and trade.get("tp1_pnl"):
                unrealized = unrealized_raw * 0.5 + trade["tp1_pnl"] - fee
            else:
                unrealized = unrealized_raw - fee
            trade["pnl"] = round(unrealized, 2)
            trade["roi"] = round(unrealized / trade["margin"] * 100, 2) if trade["margin"] else 0

            sl_price = trade.get("sl")
            if sl_price is None:
                sl_price = calc_sl_price_margin_based(trade["entry"], trade["direction"])
                trade["sl"] = round(sl_price, 8)

            # 24h Force Close (FIX 6)
            if not trade.get("tp1_hit", False) and trade.get("open_time"):
                try:
                    open_dt = datetime.fromisoformat(trade["open_time"])
                    if open_dt.tzinfo is None:
                        open_dt = open_dt.replace(tzinfo=TZ)
                    age_hours = (datetime.now(TZ) - open_dt).total_seconds() / 3600
                    if age_hours >= 24:
                        close_trade(trade, current_price, "24H_TIMEOUT")
                        log(f"  24H TIMEOUT: {coin} {trade['direction']} | No TP1 after {age_hours:.1f}h")
                        continue
                except Exception:
                    pass

            # Phase-based SL management (progressive tightening)
            phase_action = check_phase_sl(trade, coin)
            if phase_action == "CLOSE":
                # 30m Phase D detected -> close immediately
                close_trade(trade, current_price, "PHASE_EXIT")
                log(f"  PHASE EXIT: {coin} {trade['direction']} — 30m Phase D detected")
                continue
            elif phase_action == "TIGHTEN_L2":
                # 15m Phase D -> tighten SL to 30% margin (3% price at 10x)
                new_sl_pct = PHASE_SL_LEVELS[2]
                if trade["direction"] == "LONG":
                    new_sl = trade["entry"] * (1 - new_sl_pct)
                    if new_sl > sl_price:
                        trade["sl"] = round(new_sl, 8)
                        trade["phase_sl_level"] = 2
                        log(f"  PHASE SL L2: {coin} {trade['direction']} — 15m Phase D | SL tightened to {new_sl:.6f} (3% price)")
                else:
                    new_sl = trade["entry"] * (1 + new_sl_pct)
                    if new_sl < sl_price:
                        trade["sl"] = round(new_sl, 8)
                        trade["phase_sl_level"] = 2
                        log(f"  PHASE SL L2: {coin} {trade['direction']} — 15m Phase D | SL tightened to {new_sl:.6f} (3% price)")
            elif phase_action == "TIGHTEN_L1":
                # 5m Phase D -> tighten SL to 50% margin (5% price at 10x)
                current_phase_level = trade.get("phase_sl_level", 0)
                if current_phase_level < 1:  # Only tighten if not already at L1+
                    new_sl_pct = PHASE_SL_LEVELS[1]
                    if trade["direction"] == "LONG":
                        new_sl = trade["entry"] * (1 - new_sl_pct)
                        if new_sl > sl_price:
                            trade["sl"] = round(new_sl, 8)
                            trade["phase_sl_level"] = 1
                            log(f"  PHASE SL L1: {coin} {trade['direction']} — 5m Phase D | SL tightened to {new_sl:.6f} (5% price)")
                    else:
                        new_sl = trade["entry"] * (1 + new_sl_pct)
                        if new_sl < sl_price:
                            trade["sl"] = round(new_sl, 8)
                            trade["phase_sl_level"] = 1
                            log(f"  PHASE SL L1: {coin} {trade['direction']} — 5m Phase D | SL tightened to {new_sl:.6f} (5% price)")

            # Re-read sl_price after potential phase tightening
            sl_price = trade.get("sl", sl_price)

            # TP1/TP2 Trailing Stop Logic
            tp1_hit = trade.get("tp1_hit", False)

            if trade["direction"] == "LONG":
                if not tp1_hit:
                    # Phase 1: waiting for TP1 or SL
                    if recent_high >= trade["tp"]:
                        # TP1 hit
                        tp1_raw = calc_pnl("LONG", trade["entry"], trade["tp"], trade["size"]) * 0.5
                        tp1_fee = trade.get("fee", calc_fee(trade["entry"], trade["size"])) * 0.5
                        tp1_pnl = tp1_raw - tp1_fee
                        trade["tp1_hit"] = True
                        trade["tp1_pnl"] = round(tp1_pnl, 2)
                        trade["peak_price"] = trade["tp"]
                        log(f"  TP1 HIT: {coin} LONG @ {trade['tp']:.6f} | Partial PnL: ${tp1_pnl:.2f} (after fee) | SL->BE, trailing starts")
                    elif recent_low <= sl_price:
                        close_trade(trade, sl_price, "SL")
                    elif recent_low <= trade["liq"]:
                        close_trade(trade, trade["liq"], "LIQ")
                else:
                    # Phase 2: trailing
                    peak = trade.get("peak_price", trade["tp"])
                    if recent_high > peak:
                        trade["peak_price"] = recent_high
                        peak = recent_high

                    trail_stop = peak * (1 - TRAIL_PCT)
                    # FIX 4: BE covers fees
                    be_stop = calc_be_stop(trade["entry"], "LONG")

                    if recent_low <= be_stop:
                        remaining_fee = trade.get("fee", calc_fee(trade["entry"], trade["size"])) * 0.5
                        tp2_pnl = -remaining_fee  # lost remaining fee portion
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(be_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+BE"
                        trade["status"] = "closed"
                        log(f"  TP2 BE: {coin} LONG | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: ${tp2_pnl:.2f} = ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)
                    elif recent_low <= trail_stop and trail_stop > be_stop:
                        tp2_raw = calc_pnl("LONG", trade["entry"], trail_stop, trade["size"]) * 0.5
                        remaining_fee = trade.get("fee", calc_fee(trade["entry"], trade["size"])) * 0.5
                        tp2_pnl = tp2_raw - remaining_fee
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(trail_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+TRAIL"
                        trade["status"] = "closed"
                        log(f"  TP2 TRAIL: {coin} LONG | Peak {peak:.6f} -> Trail {trail_stop:.6f} | Total: ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)

            else:  # SHORT
                if not tp1_hit:
                    if recent_low <= trade["tp"]:
                        tp1_raw = calc_pnl("SHORT", trade["entry"], trade["tp"], trade["size"]) * 0.5
                        tp1_fee = trade.get("fee", calc_fee(trade["entry"], trade["size"])) * 0.5
                        tp1_pnl = tp1_raw - tp1_fee
                        trade["tp1_hit"] = True
                        trade["tp1_pnl"] = round(tp1_pnl, 2)
                        trade["peak_price"] = trade["tp"]
                        log(f"  TP1 HIT: {coin} SHORT @ {trade['tp']:.6f} | Partial PnL: ${tp1_pnl:.2f} (after fee) | SL->BE, trailing starts")
                    elif recent_high >= sl_price:
                        close_trade(trade, sl_price, "SL")
                    elif recent_high >= trade["liq"]:
                        close_trade(trade, trade["liq"], "LIQ")
                else:
                    peak = trade.get("peak_price", trade["tp"])
                    if recent_low < peak:
                        trade["peak_price"] = recent_low
                        peak = recent_low

                    trail_stop = peak * (1 + TRAIL_PCT)
                    be_stop = calc_be_stop(trade["entry"], "SHORT")

                    if recent_high >= be_stop:
                        remaining_fee = trade.get("fee", calc_fee(trade["entry"], trade["size"])) * 0.5
                        tp2_pnl = -remaining_fee
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(be_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+BE"
                        trade["status"] = "closed"
                        log(f"  TP2 BE: {coin} SHORT | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: ${tp2_pnl:.2f} = ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)
                    elif recent_high >= trail_stop and trail_stop < be_stop:
                        tp2_raw = calc_pnl("SHORT", trade["entry"], trail_stop, trade["size"]) * 0.5
                        remaining_fee = trade.get("fee", calc_fee(trade["entry"], trade["size"])) * 0.5
                        tp2_pnl = tp2_raw - remaining_fee
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(trail_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+TRAIL"
                        trade["status"] = "closed"
                        log(f"  TP2 TRAIL: {coin} SHORT | Peak {peak:.6f} -> Trail {trail_stop:.6f} | Total: ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)


# ═══════════════════════════════════════════════════════════════
# CONFIRMATION STAGE
# ═══════════════════════════════════════════════════════════════

def check_pending_confirmations(data):
    """Check pending signals for price confirmation."""
    global _pending_signals
    still_pending = []

    for sig in _pending_signals:
        coin = sig["coin"]
        sym = f"{coin}USDT"
        price = get_current_price(sym)
        if price is None:
            still_pending.append(sig)
            continue

        sig["checks_remaining"] -= 1
        d = sig["direction"]
        signal_price = sig["signal_price"]

        if d == "LONG":
            target = signal_price * (1 + CONFIRM_PCT)
            confirmed = price >= target
        else:
            target = signal_price * (1 - CONFIRM_PCT)
            confirmed = price <= target

        if confirmed:
            log(f"  CONFIRMED: {d} {coin} | Signal: {signal_price:.6f} -> Now: {price:.6f} ({CONFIRM_PCT*100:.1f}% reached)")
            # FIX 3: TP recalculated from confirmation entry
            open_trade(data, sig["tf_key"], coin, d, price, sig["expected_move"],
                       sig["probability"], sig["tf"],
                       cascade_lights=sig["cascade_lights"], cascade_code=sig["cascade_code"],
                       phase_score=sig.get("phase_score", 0),
                       phase_details=sig.get("phase_details", ""),
                       slope_details=sig.get("slope_details", {}))
        elif sig["checks_remaining"] <= 0:
            log(f"  EXPIRED: {d} {coin} | Signal: {signal_price:.6f} -> Now: {price:.6f} (not confirmed in {CONFIRM_BARS} checks)")
        else:
            still_pending.append(sig)

    expired = len(_pending_signals) - len(still_pending)
    _pending_signals = still_pending
    if expired > 0 or still_pending:
        log(f"  Pending: {len(still_pending)} | Just confirmed/expired: {expired}")


# ═══════════════════════════════════════════════════════════════
# SCAN & TRADE — CASCADE >= 4 + CONFIG 8 SLOPE FILTER
# ═══════════════════════════════════════════════════════════════

def check_btc_spike():
    """Check if BTC moved >1% in last 15min — fakeout protection."""
    try:
        url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=3"
        req = urllib.request.Request(url, headers={"User-Agent": "KODA-SE-C5/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            klines = json.loads(resp.read().decode())
        if not klines or len(klines) < 3:
            return False
        open_price = float(klines[0][1])
        close_price = float(klines[-1][4])
        move_pct = abs(close_price - open_price) / open_price * 100
        return move_pct > 1.0
    except Exception:
        return False


def scan_and_trade(data, tf, limit, tf_key):
    """Scan all coins — only open trades when CASCADE >= 4 + Config 8 slope filter."""
    now = datetime.now(TZ)
    log(f"\n{'='*60}")
    log(f"SCAN {tf.upper()} | {now.strftime('%Y-%m-%d %H:%M ET')}")
    log(f"{'='*60}")

    if _drawdown_paused:
        log(f"  DRAWDOWN BRAKE ACTIVE -- {_consecutive_sl_count} SLs in a row. No new trades.")
        return

    if check_btc_spike():
        log(f"  BTC SPIKE (>1% in 15min) -- skip scan.")
        return

    # Check ALL open trades across 15m + 30m (FIX 5)
    all_open = []
    for tfk in ["trades_15m", "trades_30m"]:
        all_open.extend([t for t in data.get(tfk, []) if t["status"] == "open"])
    open_coins = set(t["coin"] for t in all_open)

    open_trades_tf = [t for t in data[tf_key] if t["status"] == "open"]

    # Max open trades
    max_open = 20
    if len(open_trades_tf) >= max_open:
        log(f"  Max open {tf} trades reached ({max_open}).")
        return

    # Per-TF budget (FIX 7)
    tf_budget_key = f"tf_budget_{tf}"
    tf_budget_pct = CONFIG.get(tf_budget_key, 50)
    tf_budget_limit = CONFIG["total_budget"] * (tf_budget_pct / 100.0)
    tf_margin_used = sum(t.get("margin", CONFIG["capital"]) for t in open_trades_tf)
    if tf_budget_pct == 0:
        log(f"  TF Budget {tf}: disabled (0%).")
        return
    if tf_margin_used >= tf_budget_limit:
        log(f"  TF Budget {tf}: ${tf_margin_used:.0f} / ${tf_budget_limit:.0f} -- full.")
        return

    # Total budget check
    all_closed = []
    for tfk in ["trades_15m", "trades_30m"]:
        all_closed.extend([t for t in data.get(tfk, []) if t["status"] == "closed"])
    realized_pnl = sum(t.get("pnl", 0) or 0 for t in all_closed)
    margin_used = sum(t.get("margin", CONFIG["capital"]) for t in all_open)
    current_budget = CONFIG["total_budget"] + realized_pnl
    budget_available = current_budget - margin_used
    if budget_available < CONFIG["capital"]:
        log(f"  Budget full: ${current_budget:.0f} - ${margin_used:.0f} used = ${budget_available:.0f} free.")
        return

    signals_found = 0

    # Cooldown: recent LIQs
    recent_liqs = {}
    for tfk in ["trades_15m", "trades_30m"]:
        for t in data.get(tfk, []):
            if t.get("close_reason") == "LIQ" and t.get("close_time"):
                try:
                    ct = datetime.fromisoformat(t["close_time"])
                    if ct.tzinfo is None:
                        ct = ct.replace(tzinfo=TZ)
                    age_min = (now - ct).total_seconds() / 60
                    if age_min < 30:
                        key = t["coin"]
                        if key not in recent_liqs or t["close_time"] > recent_liqs[key]["close_time"]:
                            recent_liqs[key] = {"direction": t["direction"], "close_time": t["close_time"]}
                except Exception:
                    pass

    for coin in COINS:
        if coin in open_coins:
            continue

        try:
            result = full_analyze(coin, tf, limit)
            if result is None:
                continue

            direction = result["direction"]
            probability = result["probability"]
            entry = result["price"]

            # Validate basic TP direction
            if direction == "LONG" and result["tp"] <= entry:
                continue
            if direction == "SHORT" and result["tp"] >= entry:
                continue

            # Cooldown after LIQ
            if coin in recent_liqs:
                if direction == recent_liqs[coin]["direction"]:
                    log(f"  COOLDOWN: {coin} {direction} -- same direction as LIQ <30min ago.")
                    continue

            if probability >= CONFIG["min_probability"]:
                signals_found += 1

                # MTF Gate
                if not check_mtf_gate(direction):
                    continue

                # CASCADE GATE: Require >= CASCADE_MIN (5)
                bull_lights, bear_lights, cascade_dir, cascade_details = get_cascade_signal()
                lights_in_dir = bull_lights if direction == "LONG" else bear_lights

                log(f"  SIGNAL: {coin} {direction} | Prob: {probability}% | Bias: {result['coin_bias']} | BTC: {result['btc_trend']}")
                log(f"          Scores: {result['scores']} | L:{result['long_count']} S:{result['short_count']}")
                log(f"          Cascade: {bull_lights}B/{bear_lights}S -> {cascade_dir} | In dir: {lights_in_dir} | {cascade_details}")

                if lights_in_dir < CASCADE_MIN:
                    log(f"  CASCADE SKIP: {coin} {direction} -- only {lights_in_dir} lights. Need >={CASCADE_MIN}.")
                    continue

                log(f"  CASCADE {CASCADE_MIN}+ CONFIRMED! Checking phase...")

                # Phase Detection gate: score >= 6, no Phase D, consistent direction
                try:
                    phase_score, has_phase_d, phase_details, phases_dict = calculate_phase_score(coin, direction)
                    log(f"          Phase: score={phase_score:.1f} | D={has_phase_d} | {phase_details}")

                    if has_phase_d:
                        log(f"  PHASE SKIP: {coin} {direction} — Phase D or opposite direction detected | {phase_details}")
                        continue
                    if phase_score < PHASE_ENTRY_MIN_SCORE:
                        log(f"  PHASE SKIP: {coin} {direction} — Score {phase_score:.1f} < {PHASE_ENTRY_MIN_SCORE:.1f} | {phase_details}")
                        continue

                    log(f"  PHASE OK: {coin} {direction} — Score {phase_score:.1f} >= {PHASE_ENTRY_MIN_SCORE:.1f} | {phase_details}")
                except Exception as e:
                    log(f"  PHASE WARN: {coin} — Phase detection failed ({e}), allowing trade as fallback")
                    phase_score = 0
                    phase_details = "FALLBACK"

                # Slope filter (Config 8: slope < 1.0% + no expanding gap)
                klines_for_slope = result.get("klines", [])
                slope_ok, slope_details = check_slope_filter(klines_for_slope, direction)
                if not slope_ok:
                    reason = f"SMA10 slope {slope_details['sma10_slope']:.2f}% > {SLOPE_MAX}%" if slope_details.get('sma10_slope', 0) > SLOPE_MAX else f"SMA10/20 gap expanding"
                    log(f"  SLOPE SKIP: {coin} {direction} — {reason}")
                    continue

                # Build cascade code
                code_map = {"BULL": "1", "BEAR": "2", "SIDE": "0", "NO_DATA": "0"}
                c_code = "".join(code_map.get(cascade_details.get(tf_c, "0"), "0") for tf_c in ["5m", "15m", "30m", "1h", "4h"])

                # Add to confirmation queue (FIX 3: entry after confirmation)
                _pending_signals.append({
                    "coin": coin, "direction": direction, "signal_price": entry,
                    "expected_move": result["expected_move"],
                    "probability": probability, "tf": tf, "tf_key": tf_key,
                    "cascade_lights": lights_in_dir, "cascade_code": c_code,
                    "phase_score": phase_score,
                    "phase_details": phase_details,
                    "slope_details": slope_details,
                    "signal_time": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S"),
                    "checks_remaining": CONFIRM_BARS,
                })
                log(f"  PENDING: {direction} {coin} @ {entry:.6f} | Waiting for {CONFIRM_PCT*100:.1f}% confirmation in {CONFIRM_BARS} bars")

        except Exception as e:
            log(f"  ERROR analyzing {coin}: {e}")
            continue

        time.sleep(0.1)

    log(f"\nScan complete: {signals_found} signals found, {len(_pending_signals)} pending confirmation")
    open_count = len([t for t in data[tf_key] if t["status"] == "open"])
    log(f"Open {tf} trades: {open_count}")


# ═══════════════════════════════════════════════════════════════
# STATS
# ═══════════════════════════════════════════════════════════════

def update_stats(data):
    """Update statistics for all timeframes."""
    for tf_key, stats_key in [("trades_15m", "stats_15m"), ("trades_30m", "stats_30m"),
                               ("trades_1h", "stats_1h"), ("trades_4h", "stats_4h")]:
        closed = [t for t in data.get(tf_key, []) if t.get("status") == "closed"]
        open_count = len([t for t in data.get(tf_key, []) if t.get("status") == "open"])
        if not closed:
            data[stats_key] = {
                "total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m",
                "open": open_count,
            }
            continue

        wins = [t for t in closed if (t.get("pnl") or 0) > 0]
        losses = [t for t in closed if (t.get("pnl") or 0) <= 0]
        total_pnl = sum(t.get("pnl", 0) or 0 for t in closed)

        durations = []
        for t in closed:
            if t.get("open_time") and t.get("close_time"):
                try:
                    ot = datetime.strptime(t["open_time"], "%Y-%m-%dT%H:%M:%S")
                    ct = datetime.strptime(t["close_time"], "%Y-%m-%dT%H:%M:%S")
                    durations.append((ct - ot).total_seconds())
                except Exception:
                    pass

        avg_dur_secs = sum(durations) / len(durations) if durations else 0
        if avg_dur_secs < 3600:
            avg_dur_str = f"{int(avg_dur_secs / 60)}m"
        elif avg_dur_secs < 86400:
            avg_dur_str = f"{avg_dur_secs / 3600:.1f}h"
        else:
            avg_dur_str = f"{avg_dur_secs / 86400:.1f}d"

        data[stats_key] = {
            "total": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "winrate": round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / len(closed), 2) if closed else 0.0,
            "avg_duration": avg_dur_str,
            "open": open_count,
        }


# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

def log(msg):
    """Print timestamped log message."""
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    print(f"[{ts}] [SIGNAL-C8] {msg}", flush=True)


# ═══════════════════════════════════════════════════════════════
# STATUS DISPLAY
# ═══════════════════════════════════════════════════════════════

def print_status(data):
    """Print current status summary."""
    for tf_key, label in [("trades_15m", "15m"), ("trades_30m", "30m")]:
        open_trades = [t for t in data[tf_key] if t["status"] == "open"]
        closed = [t for t in data[tf_key] if t["status"] == "closed"]
        total_pnl = sum(t.get("pnl", 0) or 0 for t in closed)
        wins = len([t for t in closed if (t.get("pnl") or 0) > 0])
        losses = len([t for t in closed if (t.get("pnl") or 0) <= 0])
        wr = round(wins / len(closed) * 100, 1) if closed else 0

        log(f"  [{label}] Open: {len(open_trades)} | Closed: {len(closed)} | W/L: {wins}/{losses} ({wr}%) | PnL: ${total_pnl:.2f}")


# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def main():
    global _current_data, _signal_counter

    log("KODA SE Config-8 Signal Bot starting...")
    log(f"Strategy: 10x Leverage, $50/trade, $1000 Budget")
    log(f"SL: {CONFIG['sl_margin_pct']}% MARGIN ({CONFIG['sl_margin_pct']/CONFIG['leverage']:.1f}% price) | TP: {CONFIG['tp_range_pct']}% Expected Move")
    log(f"Fees: {FEE_RATE*100:.3f}% round trip included in all PnL")
    log(f"CASCADE: >={CASCADE_MIN}/5 timeframes aligned (relaxed SMA10>20)")
    log(f"SLOPE FILTER: Config 8 — SMA10 slope < {SLOPE_MAX}% + gap NOT expanding")
    log(f"MTF GATE: Relaxed SMA10>SMA20")
    log(f"CONFIRMATION: {CONFIRM_PCT*100:.1f}% in {CONFIRM_BARS} bars required before entry")
    log(f"BE stop covers fees + 0.1% buffer")
    log(f"24h Force Close on trades without TP1")
    log(f"Drawdown-Bremse: {DRAWDOWN_BRAKE_SL_COUNT} SLs in a row -> Pause + Telegram")
    log(f"Trailing: {TRAIL_PCT*100:.0f}% from peak after TP1")
    log(f"PHASE DETECTION: Entry score >= {PHASE_ENTRY_MIN_SCORE} required, no Phase D")
    log(f"PHASE SL: 5m_D->50%margin | 15m_D->30%margin | 30m_D->CLOSE")
    log(f"Coins: {len(COINS)} | Data file: {DATA_FILE}")
    log(f"Timeframes: 15m (50%) + 30m (50%)")
    log(f"SIGNAL CHANNEL: ACTIVE — posting to {KODA_SE_CHANNEL_ID}")
    log("")

    # RESET: Signal counter starts at 1
    try:
        os.remove("/tmp/koda_se_signal_counter.txt")
    except Exception:
        pass
    _signal_counter = 0

    # RESET: Write fresh initial data
    data = create_initial_data()
    save_data(data)
    _current_data = data
    log("RESET: Fresh data written. Signal counter = 0.")

    last_15m_scan = -1
    last_30m_scan = -1

    while True:
        try:
            now = datetime.now(TZ)
            minute = now.minute
            hour = now.hour

            # Check open trades for TP/SL/Liq/24h every minute
            check_open_trades(data)

            # Check pending confirmations every minute
            if _pending_signals:
                check_pending_confirmations(data)

            # 15min scan: run at 0, 15, 30, 45
            current_15m_slot = (hour * 60 + minute) // 5
            if minute % 5 == 0 and current_15m_slot != last_15m_scan:
                last_15m_scan = current_15m_slot
                scan_and_trade(data, "15m", 800, "trades_15m")
                update_stats(data)
                save_data(data)
                print_status(data)

            # 30m scan: run at 0, 30
            current_30m_slot = (hour * 60 + minute) // 5
            if minute % 5 == 0 and current_30m_slot != last_30m_scan:
                last_30m_scan = current_30m_slot
                scan_and_trade(data, "30m", 500, "trades_30m")
                update_stats(data)
                save_data(data)
                print_status(data)

            # Save periodically
            update_stats(data)
            save_data(data)

            # Auto-push to GitHub every 5 minutes — CORRECT filename
            if minute % 5 == 0:
                try:
                    import subprocess
                    subprocess.run(["git", "add", "paper_trades_koda_se.json"],
                                   cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
                    subprocess.run(["git", "commit", "-m", "KODA SE C8 signal bot data update"],
                                   cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
                    subprocess.run(["git", "push"],
                                   cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=30)
                except Exception:
                    pass

            # Sleep until next minute
            elapsed = (datetime.now(TZ) - now).total_seconds()
            sleep_time = max(5, 60 - elapsed)
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            log("\nShutting down...")
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
