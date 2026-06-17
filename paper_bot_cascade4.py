#!/usr/bin/env python3
"""
KODA Cascade 4 Bot — Verified Confirmation Strategy with Cascade>=4 Filter
Config: 10x|70%MSL|60%TP + Confirmation(0.3%/8bars) + Cascade>=4 + Phase Detection
Based on backtest_combo.py C2 results: 90% WR, $263/14d, PnL/DD 6.85
Phase Detection from backtest_phase_detection_c4.py: turns C4 from -$325 to +$2,741
All 4 critical math fixes from backtest_confirmation_fixed.py applied.
"""

import json
import ssl
import math
import time
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

# ===============================================================
# CONFIGURATION
# ===============================================================

CONFIG = {
    "capital": 50,
    "leverage": 10,
    "min_probability": 60,
    "tp_range_pct": 60,        # 60% EM (from best combo C2)
    "sl_margin_pct": 70,       # 70% MARGIN loss (NOT price-based!)
    "total_budget": 1000,
    "tf_budget_15m": 50,
    "tf_budget_30m": 50,
    "tf_budget_1h": 0,
    "tf_budget_4h": 0,
    "max_open_15m": 20,
    "max_open_30m": 20,
}

CASCADE_MIN = 3               # Minimum 3/4 timeframes aligned (5m removed from BTC cascade)
CONFIRM_PCT = 0.003            # 0.3% confirmation
CONFIRM_BARS = 8               # 8 minutes timeout
TRAIL_PCT = 0.02               # 2% trailing
FEE_RATE = 0.0011              # 0.11% round trip

# Slope Filter Config 2: Reject if SMA10 slope > 1.0%
SLOPE_MAX = 1.0  # max SMA10 slope in % (3-bar change)

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

# SMA history for phase detection convergence/divergence tracking
_sma_history = {}  # coin -> {tf -> {"sma10": [last 4], "sma20": [last 4], "sma50": last}}
_phase_cache = {}  # coin -> {"ts": timestamp, "phases": {tf: (phase, direction)}, "score": float}
PHASE_CACHE_SECONDS = 120  # Cache phase data for 2 minutes

# Drawdown brake
DRAWDOWN_BRAKE_SL_COUNT = 5
_consecutive_sl_count = 0
_drawdown_paused = False
_current_data = None

# Load config overrides from JSON file (written by dashboard settings)
_CFG_OVERRIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_bot_cascade4_config.json")
if os.path.exists(_CFG_OVERRIDE):
    try:
        with open(_CFG_OVERRIDE, "r") as _f:
            CONFIG.update(json.load(_f))
    except Exception:
        pass

COINS = [
    "GLM", "AVAX", "KAS", "MINA", "XRP", "FLOW",
    "AXL", "CELR", "CYS", "IOST", "CAKE", "KAITO",
    "TRX", "SUN", "GRT", "DUSK", "BAT", "SYN",
    "TON", "HBAR", "DOT", "LTC", "LINK", "SOL",
]

TZ = timezone(timedelta(hours=-4))
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trades_cascade4.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_bot_cascade4.log")

# Confirmation stage
CONFIRM_PCT_VAL = CONFIRM_PCT
_pending_signals = []

# SSL context for Binance API
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ===============================================================
# LOGGING
# ===============================================================

def log(msg):
    """Print timestamped log message."""
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    line = f"[{ts}] [CASCADE4] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ===============================================================
# API HELPERS
# ===============================================================

import urllib.request


def api(url, timeout=10):
    """Fetch JSON from URL with error handling."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PaperBot/1.0"})
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
    """Get high/low from recent klines to catch wicks."""
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit={minutes}"
        req = urllib.request.Request(url, headers={"User-Agent": "PaperBot/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            klines = json.loads(resp.read().decode())
        if not klines:
            return None, None
        high = max(float(k[2]) for k in klines)
        low = min(float(k[3]) for k in klines)
        return high, low
    except Exception:
        return None, None


# ===============================================================
# ANALYSIS (replicates kalkulator.html logic)
# ===============================================================

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

    # Value Area
    va_vol = bins[poc_i]["total"]
    va_lo, va_hi = poc_i, poc_i
    target = tv * 0.30
    while va_vol < target and (va_lo > 0 or va_hi < num_bins-1):
        lv = bins[va_lo-1]["total"] if va_lo > 0 else -1
        uv = bins[va_hi+1]["total"] if va_hi < num_bins-1 else -1
        if uv >= lv: va_hi += 1; va_vol += uv
        else: va_lo -= 1; va_vol += lv

    # Absorption zones
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


def calc_ema(values, period):
    """Calculate EMA from a list of values."""
    if len(values) < period:
        return values[-1] if values else 0
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def full_analyze(coin, tf="15m", limit=800):
    """Full coin analysis. Returns dict with direction, probability, entry, tp, expected_move, etc."""
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
    in_va = profile["va_low"] <= price <= profile["va_high"]
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

    # 7 scores
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

    # Coin bias
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
            if btc_dir == "LONG":
                base += 15 + delta_factor * 100 + ob_factor * 100
            elif btc_dir == "SIDE":
                base += 5
            else:
                base -= 15
        elif coin_dir == "SHORT":
            if btc_dir == "SHORT":
                base += 15 + delta_factor * 100 + ob_factor * 100
            elif btc_dir == "SIDE":
                base += 5
            else:
                base -= 15
        else:
            if btc_dir == "LONG":
                base += 5
            elif btc_dir == "SHORT":
                base += 5
            else:
                base -= 10
        if (coin_dir == "SHORT" and dist_down < dist_up) or (coin_dir == "LONG" and dist_up < dist_down):
            base += dist_factor * 100
        return max(5, min(85, round(base)))

    raw_a = calc_prob("LONG")
    raw_c = calc_prob("SHORT")
    raw_total = raw_a + raw_c
    prob_a = round(raw_a / raw_total * 100) if raw_total > 0 else 50
    prob_c = 100 - prob_a

    # ATR and expected move
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

    # Determine direction and probability
    btc = analyze_btc(tf)
    btc_trend = btc.get("trend", "SIDEWAYS")

    if btc_trend == "BULLISH":
        if coin_bias == "LONG":
            direction, probability = "LONG", prob_a
        elif coin_bias == "SHORT":
            direction, probability = "SHORT", prob_c
        else:
            direction, probability = "LONG", prob_a
    elif btc_trend == "BEARISH":
        if coin_bias == "SHORT":
            direction, probability = "SHORT", prob_c
        elif coin_bias == "LONG":
            direction, probability = "LONG", prob_a
        else:
            direction, probability = "SHORT", prob_c
    else:
        if coin_bias == "LONG":
            direction, probability = "LONG", prob_a
        elif coin_bias == "SHORT":
            direction, probability = "SHORT", prob_c
        else:
            if prob_a >= prob_c:
                direction, probability = "LONG", prob_a
            else:
                direction, probability = "SHORT", prob_c

    # TP based on direction -- 60% of expected move (CONFIG)
    tp_pct = CONFIG["tp_range_pct"] / 100.0
    if direction == "LONG":
        tp_price = price + abs(target_up - price) * tp_pct
    else:
        tp_price = price - abs(price - target_down) * tp_pct

    return {
        "coin": coin.upper(),
        "symbol": sym,
        "price": price,
        "direction": direction,
        "probability": probability,
        "coin_bias": coin_bias,
        "btc_trend": btc_trend,
        "scores": scores,
        "long_count": long_c,
        "short_count": short_c,
        "tp": tp_price,
        "expected_move": expected_move,
        "atr14": atr14,
        "target_up": target_up,
        "target_down": target_down,
        "prob_a": prob_a,
        "prob_c": prob_c,
        "klines": klines,
    }


# ===============================================================
# MTF GATE: BTC 1H SMA10 vs SMA20 (relaxed, consistent with cascade)
# ===============================================================

_mtf_cache = {"ts": 0, "sma10": None, "sma20": None}
MTF_CACHE_SECONDS = 300


def check_mtf_gate(direction):
    """Only allow LONG when BTC 1H SMA10 > SMA20, SHORT when SMA10 < SMA20. Relaxed since 16.06."""
    now_ts = time.time()
    if _mtf_cache["sma10"] is not None and (now_ts - _mtf_cache["ts"]) < MTF_CACHE_SECONDS:
        sma10 = _mtf_cache["sma10"]
        sma20 = _mtf_cache["sma20"]
    else:
        klines = fetch_klines("BTCUSDT", "1h", 25)
        if not klines or len(klines) < 20:
            log("  MTF GATE: No BTC 1H data -- blocking trade")
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


# ===============================================================
# KASKADEN-AMPEL (BTC Multi-TF SMA Alignment)
# ===============================================================

_cascade_cache = {"ts": 0, "result": None}
CASCADE_CACHE_SECONDS = 300


def get_cascade_signal():
    """Check BTC SMA10/20/50 on 5 timeframes. Returns (bull_count, bear_count, direction, details)."""
    now_ts = time.time()
    if _cascade_cache["result"] is not None and (now_ts - _cascade_cache["ts"]) < CASCADE_CACHE_SECONDS:
        return _cascade_cache["result"]

    timeframes = ["15m", "30m", "1h", "4h"]  # 5m removed — flips too fast for BTC cascade
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
        sma50 = sum(closes[-50:]) / 50

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

    if bull_count > bear_count:
        direction = "LONG"
    elif bear_count > bull_count:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    result = (bull_count, bear_count, direction, details)
    _cascade_cache["ts"] = now_ts
    _cascade_cache["result"] = result
    return result


# ===============================================================
# SLOPE FILTER (Config 2: SMA10 slope < 1.0%)
# ===============================================================

def check_slope_filter(klines, direction):
    """Reject overextended entries. Returns (passed, details_dict)."""
    if not klines or len(klines) < 15:
        return True, {}  # graceful fallback

    closes = [k["close"] for k in klines]

    # Calculate SMA10 now and 3 bars ago
    sma10_now = sum(closes[-10:]) / 10
    sma10_3ago = sum(closes[-13:-3]) / 10 if len(closes) >= 13 else sma10_now

    # Slope as % change
    sma10_slope = abs(sma10_now - sma10_3ago) / sma10_now * 100

    details = {"sma10_slope": round(sma10_slope, 3)}

    if sma10_slope > SLOPE_MAX:
        return False, details

    return True, details


# ===============================================================
# PHASE DETECTION (from backtest_phase_detection_c4.py)
# ===============================================================

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

    # Phase D = NO LONGER blocks entry — only used for SL tightening
    # Only OPPOSITE direction on 15m/30m/1h blocks entry
    block_tfs = ["15m", "30m", "1h"]

    for tf in PHASE_TFS:
        if tf not in phases:
            details_parts.append(f"{tf}:?")
            continue

        phase, phase_dir = phases[tf]

        if phase_dir == direction:
            score += PHASE_SCORES[phase]
            details_parts.append(f"{tf}:{phase}")
        elif phase_dir is not None and phase_dir != direction:
            # Opposite direction on signal TFs = block
            if tf in block_tfs:
                opposite_count += 1
            details_parts.append(f"{tf}:{phase}(!{phase_dir})")
        else:
            details_parts.append(f"{tf}:X")

    # Only opposite direction blocks, Phase D does NOT block (handled by SL management)
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


# ===============================================================
# CRITICAL FIX 1: SL is MARGIN-based
# ===============================================================

def calc_sl_price(entry, direction, leverage=None, sl_margin_pct=None):
    """
    SL is MARGIN-based (NOT price-based).
    70% margin loss at 10x = 7% price move.
    """
    if leverage is None:
        leverage = CONFIG["leverage"]
    if sl_margin_pct is None:
        sl_margin_pct = CONFIG["sl_margin_pct"]
    sl_price_move = sl_margin_pct / leverage / 100.0  # 70% / 10x / 100 = 0.07
    if direction == "LONG":
        return entry * (1 - sl_price_move)
    else:
        return entry * (1 + sl_price_move)


# ===============================================================
# CRITICAL FIX 2: Fees in PnL
# ===============================================================

def calc_pnl(direction, entry, close_price, size):
    """Calculate PnL with fees deducted."""
    if direction == "LONG":
        raw = (close_price - entry) * size
    else:
        raw = (entry - close_price) * size
    fee = abs(entry * size) * FEE_RATE  # 0.11% of notional
    return raw - fee


# ===============================================================
# CRITICAL FIX 4: BE stop covers fees
# ===============================================================

def calc_be_stop(entry, direction):
    """BE stop = entry + fees + 0.1% buffer."""
    be_buffer = FEE_RATE + 0.001  # 0.0011 + 0.001 = 0.0021
    if direction == "LONG":
        return entry * (1 + be_buffer)
    else:
        return entry * (1 - be_buffer)


# ===============================================================
# TRADE MANAGEMENT
# ===============================================================

KODA_SE_BOT_TOKEN = "8623243424:AAEqo7FlHPqZzZHrpLMQJFBxGnNY382YhW4"
CHRIS_CHAT_ID = "351653518"


def send_tg_channel(text):
    """DISABLED -- Cascade4 bot does NOT post to signal channel."""
    pass


def send_tg_chris(text):
    """Send direct message to Chris (for drawdown alerts only)."""
    try:
        import urllib.parse
        url = f"https://api.telegram.org/bot{KODA_SE_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": CHRIS_CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log(f"TG chris send failed: {e}")


def calc_liquidation(entry, direction, margin, size):
    """Calculate liquidation price."""
    maint = margin * 0.005
    net_margin = margin - maint
    if direction == "LONG":
        return entry - net_margin / size
    else:
        return entry + net_margin / size


def load_data():
    """Load paper trades from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            # Ensure keys exist
            empty_stats = {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                           "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m"}
            for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
                if key not in data:
                    data[key] = []
            for key in ["stats_15m", "stats_30m", "stats_1h", "stats_4h"]:
                if key not in data:
                    data[key] = dict(empty_stats)
            data.setdefault("config", {}).update({
                "capital": CONFIG["capital"], "leverage": CONFIG["leverage"],
                "total_budget": CONFIG.get("total_budget", 1000),
                "tf_budget_15m": CONFIG.get("tf_budget_15m", 50),
                "tf_budget_30m": CONFIG.get("tf_budget_30m", 50),
                "tf_budget_1h": CONFIG.get("tf_budget_1h", 0),
                "tf_budget_4h": CONFIG.get("tf_budget_4h", 0),
                "sl_margin_pct": CONFIG.get("sl_margin_pct", 70),
                "tp_range_pct": CONFIG.get("tp_range_pct", 60),
                "fee_rate": FEE_RATE,
                "cascade_min": CASCADE_MIN,
                "bot_name": "KODA Cascade 4",
            })
            return data
        except Exception:
            pass
    return {
        "config": {
            "capital": CONFIG["capital"],
            "leverage": CONFIG["leverage"],
            "min_probability": CONFIG["min_probability"],
            "min_prob": CONFIG["min_probability"],
            "tp_range_pct": CONFIG["tp_range_pct"],
            "tp_pct": CONFIG["tp_range_pct"],
            "sl_margin_pct": CONFIG.get("sl_margin_pct", 70),
            "fee_rate": FEE_RATE,
            "cascade_min": CASCADE_MIN,
            "total_budget": CONFIG.get("total_budget", 1000),
            "tf_budget_15m": CONFIG.get("tf_budget_15m", 50),
            "tf_budget_30m": CONFIG.get("tf_budget_30m", 50),
            "tf_budget_1h": CONFIG.get("tf_budget_1h", 0),
            "tf_budget_4h": CONFIG.get("tf_budget_4h", 0),
            "start_date": datetime.now(TZ).strftime("%Y-%m-%d"),
            "bot_name": "KODA Cascade 4",
        },
        "trades_15m": [],
        "trades_30m": [],
        "trades_1h": [],
        "trades_4h": [],
        "stats_15m": {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                      "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m"},
        "stats_30m": {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                      "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m"},
        "stats_1h": {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                     "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m"},
        "stats_4h": {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                     "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m"},
    }


def save_data(data):
    """Save paper trades to JSON file."""
    data["_heartbeat"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    data["_drawdown_paused"] = _drawdown_paused
    data["_consecutive_sl"] = _consecutive_sl_count
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"ERROR saving data: {e}")


def next_trade_id(data, tf_key):
    """Get next trade ID -- global across ALL timeframes."""
    all_ids = []
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            all_ids.append(t.get("id", 0))
    if not all_ids:
        return 1
    return max(all_ids) + 1


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


def open_trade(data, tf_key, coin, direction, entry, tp_signal, probability, tf,
               cascade_lights=0, cascade_code="00000", expected_move=0,
               phase_score=0, phase_details=""):
    """
    Open a new paper trade.
    CRITICAL FIX 3: TP is recalculated from the confirmation entry price,
    NOT from the original signal price.
    """
    capital = CONFIG["capital"]
    leverage = CONFIG["leverage"]
    margin = capital
    size = capital * leverage / entry
    liq = calc_liquidation(entry, direction, margin, size)
    sl = calc_sl_price(entry, direction)

    # FIX 3: Recalculate TP from actual entry price (not signal price)
    tp_pct = CONFIG["tp_range_pct"] / 100.0
    if expected_move > 0:
        tp_distance = expected_move * tp_pct
        if direction == "LONG":
            tp = entry + tp_distance
        else:
            tp = entry - tp_distance
    else:
        # Fallback: use the signal-based TP (should not happen normally)
        tp = tp_signal

    # Validate TP direction
    if direction == "LONG" and tp <= entry:
        log(f"  SKIP: {coin} LONG TP {tp:.6f} <= entry {entry:.6f}")
        return None
    if direction == "SHORT" and tp >= entry:
        log(f"  SKIP: {coin} SHORT TP {tp:.6f} >= entry {entry:.6f}")
        return None

    trade = {
        "id": next_trade_id(data, tf_key),
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
        "probability": probability,
        "open_time": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S"),
        "close_time": None,
        "close_price": None,
        "close_reason": None,
        "pnl": None,
        "roi": None,
        "status": "open",
        # TP1/TP2 trailing fields
        "tp1_hit": False,
        "tp1_pnl": None,
        "peak_price": None,
        # BE stop (FIX 4)
        "be_stop": round(calc_be_stop(entry, direction), 8),
        # Cascade tracking
        "cascade_lights": cascade_lights,
        "cascade_code": cascade_code,
        # Phase detection tracking
        "phase_score": phase_score,
        "phase_details": phase_details,
        "phase_sl_level": 0,  # 0=normal, 1=5m_D, 2=15m_D, 3=30m_D(close)
    }

    data[tf_key].append(trade)
    sl_pct = CONFIG["sl_margin_pct"] / CONFIG["leverage"]
    log(f"  OPENED {direction} {coin} @ {entry:.6f} | TP: {tp:.6f} | SL: {sl:.6f} ({sl_pct:.1f}% price = {CONFIG['sl_margin_pct']}% margin) | Prob: {probability}% | TF: {tf} | Cascade: {cascade_lights} | Phase: {phase_score:.1f}")
    return trade


def send_drawdown_alert(sl_count):
    """Send Telegram alert when drawdown brake activates."""
    text = (f"DRAWDOWN-BREMSE AKTIV -- KODA Cascade 4 Bot\n\n"
            f"{sl_count} Verluste in Folge!\n"
            f"Bot ist PAUSIERT. Keine neuen Trades.\n"
            f"Offene Trades laufen weiter (TP/SL aktiv).\n\n"
            f"Bitte D/W Analyse durchfuehren.\n"
            f"Zum Fortfahren: Bot manuell neu starten.")
    send_tg_chris(text)
    log(f"DRAWDOWN ALERT sent ({sl_count} SLs in row)")


def close_trade(trade, close_price, reason):
    """Close a paper trade with fee-adjusted PnL."""
    global _consecutive_sl_count, _drawdown_paused

    pnl = calc_pnl(trade["direction"], trade["entry"], close_price, trade["size"])
    roi = pnl / trade["margin"] * 100

    trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    trade["close_price"] = round(close_price, 8)
    trade["close_reason"] = reason
    trade["pnl"] = round(pnl, 2)
    trade["roi"] = round(roi, 2)
    trade["status"] = "closed"

    emoji = "WIN" if pnl > 0 else "LOSS"
    log(f"  CLOSED {trade['direction']} {trade['coin']} @ {close_price:.6f} | {reason} | PnL: ${pnl:.2f} ({roi:.1f}%) | {emoji}")

    # Drawdown brake tracking
    if pnl <= 0:
        _consecutive_sl_count += 1
        log(f"  Consecutive losses: {_consecutive_sl_count}/{DRAWDOWN_BRAKE_SL_COUNT}")
        if _consecutive_sl_count >= DRAWDOWN_BRAKE_SL_COUNT and not _drawdown_paused:
            _drawdown_paused = True
            log(f"DRAWDOWN BRAKE ACTIVATED -- {_consecutive_sl_count} losses in a row!")
            send_drawdown_alert(_consecutive_sl_count)
    else:
        if _consecutive_sl_count > 0:
            log(f"  Win resets consecutive loss counter (was {_consecutive_sl_count})")
        _consecutive_sl_count = 0

    return trade


# ===============================================================
# CHECK OPEN TRADES
# ===============================================================

def check_open_trades(data):
    """Check all open trades for TP, SL, trailing, 24h timeout, collective profit exit.
    Iterates trades_15m + trades_30m (FIX 7)."""

    for tf_key in ["trades_15m", "trades_30m"]:
        open_trades = [t for t in data[tf_key] if t["status"] == "open"]
        if not open_trades:
            continue

        # Group by coin to minimize API calls
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

            # Update live price + unrealized PnL for dashboard
            trade["current_price"] = round(current_price, 8)
            unrealized = calc_pnl(trade["direction"], trade["entry"], current_price, trade["size"])
            if trade.get("tp1_hit") and trade.get("tp1_pnl"):
                unrealized = unrealized * 0.5 + trade["tp1_pnl"]
            trade["pnl"] = round(unrealized, 2)
            trade["roi"] = round(unrealized / trade["margin"] * 100, 2) if trade["margin"] else 0

            sl_price = trade.get("sl")
            if sl_price is None:
                sl_price = calc_sl_price(trade["entry"], trade["direction"])
                trade["sl"] = round(sl_price, 8)

            be_stop = trade.get("be_stop")
            if be_stop is None:
                be_stop = calc_be_stop(trade["entry"], trade["direction"])
                trade["be_stop"] = round(be_stop, 8)

            # FIX 6: 24h Force Close for trades without TP1
            if not trade.get("tp1_hit", False) and trade.get("open_time"):
                try:
                    open_dt = datetime.fromisoformat(trade["open_time"])
                    if open_dt.tzinfo is None:
                        open_dt = open_dt.replace(tzinfo=TZ)
                    age_hours = (datetime.now(TZ) - open_dt).total_seconds() / 3600
                    if age_hours >= 24:
                        close_trade(trade, current_price, "24H_TIMEOUT")
                        log(f"  24H TIMEOUT: {coin} {trade['direction']} | No TP1 after {age_hours:.1f}h | PnL: ${trade['pnl']:.2f}")
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
                    # Phase 1: waiting for TP1
                    if recent_high >= trade["tp"]:
                        # TP1 hit -- close 50% at TP, SL -> BE
                        tp1_pnl = calc_pnl("LONG", trade["entry"], trade["tp"], trade["size"]) * 0.5
                        trade["tp1_hit"] = True
                        trade["tp1_pnl"] = round(tp1_pnl, 2)
                        trade["peak_price"] = trade["tp"]
                        log(f"  TP1 HIT: {coin} LONG @ {trade['tp']:.6f} | Partial PnL: ${tp1_pnl:.2f} | SL->BE, trailing starts")
                    elif recent_low <= sl_price:
                        close_trade(trade, sl_price, "SL")
                    elif recent_low <= trade["liq"]:
                        close_trade(trade, trade["liq"], "LIQ")
                else:
                    # Phase 2: TP1 hit, trailing remaining 50%
                    peak = trade.get("peak_price", trade["tp"])
                    if recent_high > peak:
                        trade["peak_price"] = recent_high
                        peak = recent_high

                    trail_stop = peak * (1 - TRAIL_PCT)

                    if recent_low <= be_stop:
                        # Back to BE -- close remaining at BE
                        tp2_pnl = 0.0
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(be_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+BE"
                        trade["status"] = "closed"
                        log(f"  TP2 BE: {coin} LONG | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: $0.00 = ${total_pnl:.2f}")
                    elif recent_low <= trail_stop and trail_stop > be_stop:
                        tp2_pnl = calc_pnl("LONG", trade["entry"], trail_stop, trade["size"]) * 0.5
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(trail_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+TRAIL"
                        trade["status"] = "closed"
                        log(f"  TP2 TRAIL: {coin} LONG | Peak {peak:.6f} -> Trail {trail_stop:.6f} | Total: ${total_pnl:.2f}")

            else:  # SHORT
                if not tp1_hit:
                    if recent_low <= trade["tp"]:
                        tp1_pnl = calc_pnl("SHORT", trade["entry"], trade["tp"], trade["size"]) * 0.5
                        trade["tp1_hit"] = True
                        trade["tp1_pnl"] = round(tp1_pnl, 2)
                        trade["peak_price"] = trade["tp"]
                        log(f"  TP1 HIT: {coin} SHORT @ {trade['tp']:.6f} | Partial PnL: ${tp1_pnl:.2f} | SL->BE, trailing starts")
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

                    if recent_high >= be_stop:
                        tp2_pnl = 0.0
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(be_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+BE"
                        trade["status"] = "closed"
                        log(f"  TP2 BE: {coin} SHORT | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: $0.00 = ${total_pnl:.2f}")
                    elif recent_high >= trail_stop and trail_stop < be_stop:
                        tp2_pnl = calc_pnl("SHORT", trade["entry"], trail_stop, trade["size"]) * 0.5
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(trail_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+TRAIL"
                        trade["status"] = "closed"
                        log(f"  TP2 TRAIL: {coin} SHORT | Peak {peak:.6f} -> Trail {trail_stop:.6f} | Total: ${total_pnl:.2f}")

    # FIX 8: Collective Profit Exit
    # If any single trade ROI > 30% AND sum of all profitable tp1_hit trades >= 100% ROI -> close all
    check_collective_profit_exit(data)


def check_collective_profit_exit(data):
    """
    FIX 8: Collective Profit Exit.
    If any single open trade has ROI > 30% AND the sum of ROI of all profitable
    tp1_hit open trades >= 100%, close all profitable tp1_hit trades.
    """
    all_open = []
    for tf_key in ["trades_15m", "trades_30m"]:
        all_open.extend([t for t in data[tf_key] if t["status"] == "open"])

    if not all_open:
        return

    # Check if any trade has ROI > 30%
    has_high_roi = any((t.get("roi") or 0) > 30 for t in all_open)
    if not has_high_roi:
        return

    # Collect profitable tp1_hit trades
    profitable_tp1 = [t for t in all_open if t.get("tp1_hit") and (t.get("roi") or 0) > 0]
    if not profitable_tp1:
        return

    sum_roi = sum(t.get("roi", 0) for t in profitable_tp1)
    if sum_roi >= 100:
        log(f"  COLLECTIVE PROFIT EXIT: Sum ROI of {len(profitable_tp1)} profitable TP1 trades = {sum_roi:.1f}% >= 100%")
        for trade in profitable_tp1:
            current = trade.get("current_price", trade["entry"])
            # Calculate final PnL for remaining 50%
            if trade["direction"] == "LONG":
                tp2_pnl = calc_pnl("LONG", trade["entry"], current, trade["size"]) * 0.5
            else:
                tp2_pnl = calc_pnl("SHORT", trade["entry"], current, trade["size"]) * 0.5
            total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
            trade["pnl"] = round(total_pnl, 2)
            trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
            trade["close_price"] = round(current, 8)
            trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
            trade["close_reason"] = "COLLECTIVE_EXIT"
            trade["status"] = "closed"
            log(f"    Closed {trade['direction']} {trade['coin']} @ {current:.6f} | PnL: ${total_pnl:.2f}")


def update_stats(data):
    """Update statistics for both timeframes."""
    for tf_key, stats_key in [("trades_15m", "stats_15m"), ("trades_30m", "stats_30m"),
                               ("trades_1h", "stats_1h"), ("trades_4h", "stats_4h")]:
        closed = [t for t in data[tf_key] if t["status"] == "closed"]
        if not closed:
            data[stats_key] = {
                "total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m",
                "open": len([t for t in data[tf_key] if t["status"] == "open"]),
            }
            continue

        wins = [t for t in closed if t.get("close_reason") in ("TP", "TP1+TRAIL", "TP1+BE", "COLLECTIVE_EXIT")]
        losses = [t for t in closed if t.get("close_reason") in ("SL", "LIQ", "PHASE_EXIT")]
        total_pnl = sum(t["pnl"] for t in closed if t["pnl"])

        durations = []
        for t in closed:
            if t["open_time"] and t["close_time"]:
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
            "open": len([t for t in data[tf_key] if t["status"] == "open"]),
        }


# ===============================================================
# SCAN & TRADE
# ===============================================================

def check_btc_spike():
    """Check if BTC moved >1% in last 15min -- fakeout protection."""
    try:
        url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=3"
        req = urllib.request.Request(url, headers={"User-Agent": "PaperBot/1.0"})
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
    """Scan all coins and open trades where conditions are met.
    FIX 5: Require cascade lights_in_dir >= 4 (CASCADE_MIN)."""
    now = datetime.now(TZ)
    log(f"\n{'='*60}")
    log(f"SCAN {tf.upper()} | {now.strftime('%Y-%m-%d %H:%M ET')}")
    log(f"{'='*60}")

    if _drawdown_paused:
        log(f"  DRAWDOWN BRAKE ACTIVE -- {_consecutive_sl_count} SLs. No new trades.")
        return

    if check_btc_spike():
        log(f"  BTC SPIKE (>1% in 15min) -- skipping scan.")
        return

    open_trades = [t for t in data[tf_key] if t["status"] == "open"]
    all_open = [t for tfk in ["trades_15m", "trades_30m"]
                for t in data.get(tfk, []) if t["status"] == "open"]
    open_coins = set(t["coin"] for t in all_open)

    max_key = f"max_open_{tf}"
    max_open = CONFIG.get(max_key, 20)
    if len(open_trades) >= max_open:
        log(f"  Max open {tf} trades reached ({max_open}). Skipping scan.")
        return

    tf_budget_key = f"tf_budget_{tf}"
    tf_budget_pct = CONFIG.get(tf_budget_key, 50)
    tf_budget_limit = CONFIG.get("total_budget", 1000) * (tf_budget_pct / 100.0)
    tf_margin_used = sum(t.get("margin", CONFIG["capital"]) for t in open_trades)
    if tf_budget_pct == 0:
        log(f"  TF Budget {tf}: disabled (0%).")
        return
    if tf_margin_used >= tf_budget_limit:
        log(f"  TF Budget {tf}: ${tf_margin_used:.0f} / ${tf_budget_limit:.0f} -- full.")
        return

    # Budget check
    all_closed_trades = []
    for tfk in ["trades_15m", "trades_30m"]:
        all_closed_trades.extend([t for t in data.get(tfk, []) if t["status"] == "closed"])
    realized_pnl = sum(t.get("pnl", 0) or 0 for t in all_closed_trades)
    margin_used = sum(t.get("margin", CONFIG["capital"]) for t in all_open)
    current_budget = CONFIG.get("total_budget", 1000) + realized_pnl
    budget_available = current_budget - margin_used
    if budget_available < CONFIG["capital"]:
        log(f"  Budget: ${current_budget:.0f} | Margin: ${margin_used:.0f} | Free: ${budget_available:.0f} -- no room.")
        return

    signals_found = 0
    pending_added = 0

    # Recent LIQ cooldown
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
            tp = result["tp"]

            if direction == "LONG" and tp <= entry:
                continue
            if direction == "SHORT" and tp >= entry:
                continue

            # Cooldown after LIQ
            if coin in recent_liqs:
                if direction == recent_liqs[coin]["direction"]:
                    log(f"  COOLDOWN: {coin} {direction} -- same dir as recent LIQ. Skip.")
                    continue

            if probability >= CONFIG["min_probability"]:
                signals_found += 1

                # MTF Gate
                if not check_mtf_gate(direction):
                    continue

                # Cascade filter -- FIX 5: require >= CASCADE_MIN (4)
                bull_lights, bear_lights, cascade_dir, cascade_details = get_cascade_signal()
                if direction == "LONG":
                    lights_in_dir = bull_lights
                else:
                    lights_in_dir = bear_lights

                log(f"  SIGNAL: {coin} {direction} | Prob: {probability}% | Bias: {result['coin_bias']} | BTC: {result['btc_trend']}")
                log(f"          Scores: {result['scores']} | L:{result['long_count']} S:{result['short_count']}")
                log(f"          Cascade: {bull_lights}B/{bear_lights}S -> {cascade_dir} | In dir: {lights_in_dir} | {cascade_details}")

                # FIX 5: Cascade gate >= 4 (not >= 2)
                if lights_in_dir < CASCADE_MIN:
                    log(f"  CASCADE SKIP: {coin} {direction} -- only {lights_in_dir} lights. Need >={CASCADE_MIN}.")
                    continue

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

                # Slope filter (Config 2: reject if SMA10 slope > 1.0%)
                klines_for_slope = result.get("klines", [])
                slope_ok, slope_details = check_slope_filter(klines_for_slope, direction)
                if not slope_ok:
                    log(f"  SLOPE SKIP: {coin} {direction} — SMA10 slope {slope_details['sma10_slope']:.2f}% > {SLOPE_MAX}%")
                    continue

                # Check max open limit again
                current_open = len([t for t in data[tf_key] if t["status"] == "open"])
                if current_open >= max_open:
                    log(f"  Max open {tf} trades reached. Stopping scan.")
                    break

                # Build cascade code
                code_map = {"BULL": "1", "BEAR": "2", "SIDE": "0", "NO_DATA": "0"}
                c_code = "".join(code_map.get(cascade_details.get(tf_c, "0"), "0") for tf_c in ["5m", "15m", "30m", "1h", "4h"])

                # Add to pending confirmation queue
                _pending_signals.append({
                    "coin": coin, "direction": direction, "signal_price": entry,
                    "tp": tp, "probability": probability, "tf": tf, "tf_key": tf_key,
                    "cascade_lights": lights_in_dir, "cascade_code": c_code,
                    "expected_move": result["expected_move"],
                    "phase_score": phase_score,
                    "phase_details": phase_details,
                    "signal_time": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S"),
                    "checks_remaining": CONFIRM_BARS,
                })
                log(f"  PENDING: {direction} {coin} @ {entry:.6f} | Waiting for {CONFIRM_PCT*100:.1f}% confirm in {CONFIRM_BARS} bars")
                pending_added += 1

        except Exception as e:
            log(f"  ERROR analyzing {coin}: {e}")
            continue

        time.sleep(0.1)

    log(f"\nScan complete: {signals_found} signals, {pending_added} pending confirmation")
    open_count = len([t for t in data[tf_key] if t["status"] == "open"])
    log(f"Open {tf} trades: {open_count}")


# ===============================================================
# CONFIRMATION CHECK
# ===============================================================

def check_pending_confirmations(data):
    """Check pending signals for price confirmation. Open trade if confirmed, discard if timeout.
    FIX 3: TP recalculated from confirmation entry price."""
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

        # Check confirmation: price moved CONFIRM_PCT in predicted direction
        if d == "LONG":
            target = signal_price * (1 + CONFIRM_PCT)
            confirmed = price >= target
        else:
            target = signal_price * (1 - CONFIRM_PCT)
            confirmed = price <= target

        if confirmed:
            # FIX 3: Entry at confirmed price, TP recalculated from this price
            log(f"  CONFIRMED: {d} {coin} | Signal: {signal_price:.6f} -> Now: {price:.6f} ({CONFIRM_PCT*100:.1f}% reached)")
            open_trade(data, sig["tf_key"], coin, d, price, sig["tp"], sig["probability"], sig["tf"],
                       cascade_lights=sig["cascade_lights"], cascade_code=sig["cascade_code"],
                       expected_move=sig.get("expected_move", 0),
                       phase_score=sig.get("phase_score", 0),
                       phase_details=sig.get("phase_details", ""))
        elif sig["checks_remaining"] <= 0:
            log(f"  EXPIRED: {d} {coin} | Signal: {signal_price:.6f} -> Now: {price:.6f} (not confirmed in {CONFIRM_BARS} checks)")
        else:
            still_pending.append(sig)

    expired = len(_pending_signals) - len(still_pending)
    _pending_signals = still_pending
    if expired > 0 or still_pending:
        log(f"  Pending: {len(still_pending)} | Confirmed/expired: {expired}")


# ===============================================================
# STATUS
# ===============================================================

def print_status(data):
    """Print current status summary."""
    for tf_key, label in [("trades_15m", "15m"), ("trades_30m", "30m")]:
        open_trades = [t for t in data[tf_key] if t["status"] == "open"]
        closed = [t for t in data[tf_key] if t["status"] == "closed"]
        total_pnl = sum(t["pnl"] for t in closed if t["pnl"])
        wins = len([t for t in closed if t["pnl"] and t["pnl"] > 0])
        losses = len([t for t in closed if t["pnl"] and t["pnl"] <= 0])
        wr = round(wins / len(closed) * 100, 1) if closed else 0
        log(f"  [{label}] Open: {len(open_trades)} | Closed: {len(closed)} | W/L: {wins}/{losses} ({wr}%) | PnL: ${total_pnl:.2f}")


# ===============================================================
# MAIN LOOP
# ===============================================================

def main():
    log("KODA Cascade 4 Bot starting...")
    log(f"Strategy: {CONFIG['leverage']}x Leverage, ${CONFIG['capital']}/trade, ${CONFIG['total_budget']} Budget")
    log(f"SL: {CONFIG['sl_margin_pct']}% MARGIN ({CONFIG['sl_margin_pct']/CONFIG['leverage']:.1f}% price) | TP: {CONFIG['tp_range_pct']}% Expected Move")
    log(f"CONFIRMATION: {CONFIRM_PCT*100:.1f}% in {CONFIRM_BARS} bars required before entry")
    log(f"CASCADE FILTER: >= {CASCADE_MIN}/5 timeframes aligned")
    log(f"Fees: {FEE_RATE*100:.2f}% round trip deducted from PnL")
    log(f"BE Stop: entry + fees + 0.1% buffer = entry * {1 + FEE_RATE + 0.001:.4f}")
    log(f"Drawdown brake: {DRAWDOWN_BRAKE_SL_COUNT} SLs in row -> pause + Telegram")
    log(f"TP1/TP2: 50% close at TP1, SL->BE (fee-covered), {TRAIL_PCT*100:.0f}% trail from peak")
    log(f"24h Force Close: trades without TP1 after 24h")
    log(f"Collective Exit: ROI>30% single + sum>=100% -> close all profitable TP1 trades")
    log(f"PHASE DETECTION: Entry score >= {PHASE_ENTRY_MIN_SCORE} required, no Phase D")
    log(f"PHASE SL: 5m_D->50%margin | 15m_D->30%margin | 30m_D->CLOSE")
    log(f"Coins: {len(COINS)} | Data file: {DATA_FILE}")
    log(f"Timeframes: 15m ({CONFIG['tf_budget_15m']}%) + 30m ({CONFIG['tf_budget_30m']}%)")
    log("")

    global _current_data
    data = load_data()
    _current_data = data

    last_15m_scan = -1
    last_30m_scan = -1

    while True:
        try:
            now = datetime.now(TZ)
            minute = now.minute
            hour = now.hour

            # Check open trades for TP/SL/Liq every minute
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

            # Auto-push to GitHub every 5 minutes
            if minute % 5 == 0:
                try:
                    import subprocess
                    subprocess.run(["git", "add", "paper_trades_cascade4.json"],
                                   cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
                    subprocess.run(["git", "commit", "-m", "KODA Cascade4 paper bot data update"],
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
