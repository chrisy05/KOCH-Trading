#!/usr/bin/env python3
"""
KODA SE Paper Bot — Full Stack 7-Score + Kaskaden (FULL 1min)
Upgraded 12.06.2026: Complete 1min operation — signal, entry, and TP/SL all on 1min klines.
Config: FULL 5x|55%TP|ms2 — 7-Score + Kaskaden + all filters
7-Score: Delta, OB, Funding, Distance, Walls, POC, VA
Filters: Kaskaden-Ampel + MTF Gate + EMA Ribbon + Adaptive SL + TP1/TP2 Trailing
Drawdown-Bremse: 5 SLs in Folge → Pause + Telegram-Alarm
"""

import json
import ssl
import math
import time
import os
import sys
import traceback
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CONFIG = {
    "capital": 50,
    "leverage": 5,             # 5x — best PnL config
    "min_probability": 60,
    "tp_range_pct": 55,       # 55% of expected move (TP1)
    "sl_price_pct": 42,       # 42% PRICE move (NOT margin-based)
    "sl_pct": 0,              # Disable margin-based SL — we use price-based
    "max_open_1m": 20,
    "total_budget": 1000,     # $1k Budget
    "tf_budget_1m": 100,      # 100% für 1m
}

# Drawdown-Bremse
DRAWDOWN_BRAKE_SL_COUNT = 5   # 5 SLs in Folge → Pause
DRAWDOWN_BRAKE_ACTIVE = False  # wird True wenn Bremse greift

# Load config overrides from JSON file (written by dashboard settings)
_CFG_OVERRIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_bot_koda_se_config.json")
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
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trades_koda_se.json")

# SSL context for Binance API
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ═══════════════════════════════════════════════════════════════
# API HELPERS
# ═══════════════════════════════════════════════════════════════

import urllib.request


def api(url, timeout=10):
    """Fetch JSON from URL with error handling."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PaperBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def fetch_klines(symbol, interval="1m", limit=1000):
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


# ═══════════════════════════════════════════════════════════════
# ANALYSIS (replicates kalkulator.html logic)
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


# ═══════════════════════════════════════════════════════════════
# MTF GATE: BTC 1H SMA20 vs SMA50
# ═══════════════════════════════════════════════════════════════

_mtf_cache = {"ts": 0, "sma20": None, "sma50": None}
MTF_CACHE_SECONDS = 300  # 5 minutes


def check_mtf_gate(direction):
    """
    Multi-TF gate: Only allow LONG when BTC 1H SMA20 > SMA50,
    only allow SHORT when BTC 1H SMA20 < SMA50.
    Returns True if trade is allowed, False if blocked.
    """
    now_ts = time.time()
    if _mtf_cache["sma20"] is not None and (now_ts - _mtf_cache["ts"]) < MTF_CACHE_SECONDS:
        sma20 = _mtf_cache["sma20"]
        sma50 = _mtf_cache["sma50"]
    else:
        klines = fetch_klines("BTCUSDT", "1h", 55)
        if not klines or len(klines) < 50:
            log("  MTF GATE: No BTC 1H data — blocking trade")
            return False
        closes = [k["close"] for k in klines]
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50
        _mtf_cache["ts"] = now_ts
        _mtf_cache["sma20"] = sma20
        _mtf_cache["sma50"] = sma50
        time.sleep(0.1)

    if direction == "LONG" and sma20 > sma50:
        return True
    if direction == "SHORT" and sma20 < sma50:
        return True

    log(f"  MTF GATE BLOCKED: {direction} but BTC 1H SMA20={sma20:.0f} SMA50={sma50:.0f} ({'SMA20>SMA50' if sma20 > sma50 else 'SMA20<SMA50'})")
    return False


# ═══════════════════════════════════════════════════════════════
# ADAPTIVE SL: EMA Ribbon Width
# ═══════════════════════════════════════════════════════════════

def calc_ema(values, period):
    """Calculate EMA from a list of values."""
    if len(values) < period:
        return values[-1] if values else 0
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def get_ema_ribbon_width(klines):
    """
    Calculate EMA ribbon width (distance between fastest and slowest EMA).
    Uses EMA 8, 13, 21, 34. Returns width as % of price.
    When ribbon narrows, trend is weakening.
    """
    if len(klines) < 34:
        return None
    closes = [k["close"] for k in klines]
    price = closes[-1]
    ema8 = calc_ema(closes, 8)
    ema13 = calc_ema(closes, 13)
    ema21 = calc_ema(closes, 21)
    ema34 = calc_ema(closes, 34)
    emas = [ema8, ema13, ema21, ema34]
    width = (max(emas) - min(emas)) / price * 100
    return width


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


def full_analyze(coin, tf="1m", limit=1000):
    """
    Full coin analysis replicating kalkulator.html logic.
    Returns: dict with coin_bias, probability, direction, entry, tp, expected_move, etc.
    """
    sym = f"{coin.upper()}USDT"

    # 1. Fetch klines
    klines = fetch_klines(sym, tf, limit)
    if not klines or len(klines) < 20:
        return None
    time.sleep(0.1)

    # 2. Build volume profile
    profile = build_profile(klines)
    if not profile:
        return None
    price = profile["price"]
    time.sleep(0.1)

    # 3. Orderbook depth
    depth = get_depth(sym)
    depth_info = analyze_depth(depth, price)
    time.sleep(0.1)

    # 4. OI + Funding
    oi_fund = get_oi_funding(sym)
    fund_pct = oi_fund.get("funding_pct", 0)
    time.sleep(0.1)

    # 5. Recent high/low for distance
    recent_high = max(k["high"] for k in klines[-100:])
    recent_low = min(k["low"] for k in klines[-100:])
    dist_up = (recent_high - price) / price * 100
    dist_down = (price - recent_low) / price * 100

    # 6. Support/Resistance zones
    support_zones = [z for z in profile["zones"] if z["type"] == "SUPPORT"]
    resist_zones = [z for z in profile["zones"] if z["type"] == "RESISTANCE"]

    # 7. POC and VA position
    poc_dist = (profile["poc"] - price) / price * 100
    in_va = profile["va_low"] <= price <= profile["va_high"]
    above_va = price > profile["va_high"]
    below_va = price < profile["va_low"]

    # 8. Delta ratio
    total_vol = profile["total_buy"] + profile["total_sell"]
    delta_ratio = abs(profile["delta"]) / max(total_vol, 1) * 100

    # OB ratio
    ob_ratio = depth_info.get("bid_ask_ratio", 1)
    ob_bias = depth_info.get("ob_bias", "NEUTRAL")

    # Funding bias
    if fund_pct > 0.005:
        fund_bias = "SHORT"
    elif fund_pct < -0.005:
        fund_bias = "LONG"
    else:
        fund_bias = "NEUTRAL"

    # 9. Calculate 7 scores (matching JS exactly)
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

    # ── Score gate: need >= 2/7 (ms2 from optimization) ──
    max_score = max(long_c, short_c)
    if max_score < 2:
        return None  # Not enough score alignment

    # 10. Coin bias
    if long_c > short_c + 1:
        coin_bias = "LONG"
    elif short_c > long_c + 1:
        coin_bias = "SHORT"
    else:
        coin_bias = "NEUTRAL"

    # 11. Probability calculation (exact JS match)
    coin_strength = max(long_c, short_c) / 7  # 0..1
    neutral_penalty = (7 - long_c - short_c) / 7 * 0.15

    dist_factor = (min(dist_down / (dist_down + dist_up) * 0.1, 0.05) if dist_down < dist_up
                   else min(dist_up / (dist_down + dist_up) * 0.1, 0.05)) if (dist_down + dist_up) > 0 else 0

    delta_factor = min(delta_ratio / 100 * 0.1, 0.08)
    ob_factor = abs(ob_ratio - 1) * 0.05

    def calc_prob(btc_dir):
        coin_dir = "LONG" if long_c > short_c else ("SHORT" if short_c > long_c else "NEUTRAL")
        base = 35.0

        # Coin score contribution (up to +25%)
        base += coin_strength * 25
        base -= neutral_penalty * 100

        # BTC alignment bonus/penalty
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

        # Distance bonus for matching direction
        if (coin_dir == "SHORT" and dist_down < dist_up) or (coin_dir == "LONG" and dist_up < dist_down):
            base += dist_factor * 100

        return max(5, min(85, round(base)))

    raw_a = calc_prob("LONG")
    raw_c = calc_prob("SHORT")
    raw_total = raw_a + raw_c
    prob_a = round(raw_a / raw_total * 100) if raw_total > 0 else 50
    prob_c = 100 - prob_a

    # 12. ATR and expected move
    atr14 = calc_atr(klines, 14)
    # Expected Move: ATR14 * sqrt(96) * 0.5 on 15m
    # For 30m: sqrt(48) * 0.5
    tf_mins = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
    bars_per_day = 1440 / tf_mins.get(tf, 15)
    expected_move = atr14 * math.sqrt(bars_per_day) * 0.5  # dampened

    # Range boundaries adjusted with absorption zones
    range_high = price + expected_move
    range_low = price - expected_move
    if resist_zones and resist_zones[0]["mid"] < range_high:
        range_high = min(range_high, resist_zones[0]["mid"] + atr14)
    if support_zones and support_zones[0]["mid"] > range_low:
        range_low = max(range_low, support_zones[0]["mid"] - atr14)

    # Bias-adjusted targets
    if coin_bias == "LONG":
        target_up = range_high
        target_down = price - expected_move * 0.5
    elif coin_bias == "SHORT":
        target_up = price + expected_move * 0.5
        target_down = range_low
    else:
        target_up = range_high
        target_down = range_low

    # 13. Determine trade direction and probability
    btc = analyze_btc(tf)
    btc_trend = btc.get("trend", "SIDEWAYS")

    if btc_trend == "BULLISH":
        if coin_bias == "LONG":
            direction = "LONG"
            probability = prob_a
        elif coin_bias == "SHORT":
            direction = "SHORT"
            probability = prob_c
        else:
            direction = "LONG"
            probability = prob_a
    elif btc_trend == "BEARISH":
        if coin_bias == "SHORT":
            direction = "SHORT"
            probability = prob_c
        elif coin_bias == "LONG":
            direction = "LONG"
            probability = prob_a
        else:
            direction = "SHORT"
            probability = prob_c
    else:
        if coin_bias == "LONG":
            direction = "LONG"
            probability = prob_a
        elif coin_bias == "SHORT":
            direction = "SHORT"
            probability = prob_c
        else:
            if prob_a >= prob_c:
                direction = "LONG"
                probability = prob_a
            else:
                direction = "SHORT"
                probability = prob_c

    # Calculate TP based on direction — 55% of expected move
    if direction == "LONG":
        tp_price = price + abs(target_up - price) * (CONFIG["tp_range_pct"] / 100)
    else:
        tp_price = price - abs(price - target_down) * (CONFIG["tp_range_pct"] / 100)

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
        "klines": klines,  # Pass klines for EMA ribbon check
    }


# ═══════════════════════════════════════════════════════════════
# TRADE MANAGEMENT
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# KASKADEN-AMPEL (BTC Multi-TF SMA Alignment)
# ═══════════════════════════════════════════════════════════════

_cascade_cache = {"ts": 0, "result": None}
CASCADE_CACHE_SECONDS = 300  # 5 minutes


def get_cascade_signal():
    """Check BTC SMA10/20/50 on 5 timeframes. Returns (bull_count, bear_count, direction, details)."""
    now_ts = time.time()
    if _cascade_cache["result"] is not None and (now_ts - _cascade_cache["ts"]) < CASCADE_CACHE_SECONDS:
        return _cascade_cache["result"]

    timeframes = ["5m", "15m", "30m", "1h", "4h"]
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

        if sma10 > sma20 > sma50:
            bull_count += 1
            details[tf] = "BULL"
        elif sma10 < sma20 < sma50:
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


def load_data():
    """Load paper trades from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            # Ensure 1m keys exist
            empty_stats = {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                           "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m"}
            if "trades_1m" not in data:
                data["trades_1m"] = []
            if "stats_1m" not in data:
                data["stats_1m"] = dict(empty_stats)
            # Config aktuell halten
            data.setdefault("config", {}).update({
                "capital": CONFIG["capital"], "leverage": CONFIG["leverage"],
                "total_budget": CONFIG.get("total_budget", 1000),
                "tf_budget_1m": CONFIG.get("tf_budget_1m", 100),
                "sl_price_pct": CONFIG.get("sl_price_pct", 42),
                "bot_name": "KODA SE 1m",
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
            "sl_price_pct": CONFIG.get("sl_price_pct", 42),
            "total_budget": CONFIG.get("total_budget", 1000),
            "tf_budget_15m": CONFIG.get("tf_budget_15m", 50),
            "tf_budget_30m": CONFIG.get("tf_budget_30m", 50),
            "tf_budget_1h": CONFIG.get("tf_budget_1h", 0),
            "tf_budget_4h": CONFIG.get("tf_budget_4h", 0),
            "start_date": datetime.now(TZ).strftime("%Y-%m-%d"),
            "bot_name": "KODA Optimal",
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
    """Get next trade ID — global across ALL timeframes."""
    all_ids = []
    for key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(key, []):
            all_ids.append(t.get("id", 0))
    if not all_ids:
        return 1
    return max(all_ids) + 1


def calc_liquidation(entry, direction, margin, size):
    """Calculate liquidation price.
    At 2x leverage, liquidation is at ~49.75% price move.
    LONG: entry - (margin - margin*0.005) / size
    SHORT: entry + (margin - margin*0.005) / size
    """
    maint = margin * 0.005
    net_margin = margin - maint
    if direction == "LONG":
        return entry - net_margin / size
    else:
        return entry + net_margin / size


def calc_sl_price(entry, direction):
    """
    Calculate SL price based on 42% PRICE move (NOT margin-based).
    At 2x leverage, 42% price move = 84% margin loss.
    Liquidation is at ~49.75% price move, so SL triggers well before liq.
    """
    sl_price_pct = CONFIG.get("sl_price_pct", 42) / 100.0  # 0.42
    if direction == "LONG":
        return entry * (1 - sl_price_pct)
    else:
        return entry * (1 + sl_price_pct)


def calc_pnl(direction, entry, close_price, size):
    """Calculate PnL for a trade."""
    if direction == "LONG":
        return (close_price - entry) * size
    else:
        return (entry - close_price) * size


KODA_SE_BOT_TOKEN = "8203429320:AAE3L0PZoguVsY_IEwcM_uPDaJNWUXjvHXI"  # @koda_signal_bot
KODA_SE_CHANNEL_ID = "-1003770314055"  # KODA SE Signal Kanal
CHRIS_CHAT_ID = "351653518"  # für Drawdown-Alarm direkt an Chris
TRADING_BOT_TOKEN = os.environ.get("TRADING_BOT_TOKEN", "")  # für Drawdown-Alarm
_signal_counter = 0

def _load_signal_counter():
    global _signal_counter
    try:
        with open("/tmp/koda_se_signal_counter.txt", "r") as f:
            _signal_counter = int(f.read().strip())
    except:
        _signal_counter = 100  # start after old signals

def _save_signal_counter():
    try:
        with open("/tmp/koda_se_signal_counter.txt", "w") as f:
            f.write(str(_signal_counter))
    except:
        pass

_load_signal_counter()

def send_tg_channel(text):
    """Send signal to KODA SE channel via @koda_signal_bot."""
    try:
        import urllib.parse
        url = f"https://api.telegram.org/bot{KODA_SE_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": KODA_SE_CHANNEL_ID, "text": text}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log(f"TG channel send failed: {e}")

def send_tg_chris(text):
    """Send direct message to Chris (for alerts)."""
    try:
        import urllib.parse
        url = f"https://api.telegram.org/bot{TRADING_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": CHRIS_CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log(f"TG chris send failed: {e}")


def notify_trade_opened(trade):
    """Post signal to Telegram when bot opens a trade."""
    global _signal_counter
    _signal_counter += 1
    _save_signal_counter()

    coin = trade["coin"]
    d = trade["direction"]
    entry = trade["entry"]
    tp = trade["tp"]
    sl = trade["sl"]
    prob = trade["probability"]
    lev = trade["leverage"]
    tf = trade["tf"]
    cascade = trade.get("cascade_code", "")

    arrow = "🟢" if d == "LONG" else "🔴"
    tp_pct = abs(tp / entry - 1) * 100
    sl_pct = abs(sl / entry - 1) * 100

    fmt = lambda x: f"${x:,.2f}" if x > 100 else (f"${x:.4f}" if x > 1 else f"${x:.6f}")

    msg = f"""[KODA SE] {arrow} #{_signal_counter} — {coin} {d}
━━━━━━━━━━━━━━━━━━━━━━
Prob: {prob}% | TF: {tf} | Kaskade: {cascade}

Entry: {fmt(entry)}
TP1:   {fmt(tp)} ({'+' if d=='LONG' else '-'}{tp_pct:.1f}%) → 50% close, SL→Entry
TP2:   Trailing 3% vom Peak
SL:    {fmt(sl)} ({'-' if d=='LONG' else '+'}{sl_pct:.1f}%)

Hebel: {lev}x | Margin: ${trade['margin']}

Factory7Signal© Full Stack
━━━━━━━━━━━━━━━━━━━━━━
KODA SE | 5x|55%TP|ms2 · {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')} ET"""

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
    """Post result to KODA SE channel when bot closes a trade."""
    coin = trade["coin"]
    d = trade["direction"]
    pnl = trade["pnl"]
    roi = trade["roi"]
    reason = trade["close_reason"]
    entry = trade["entry"]
    close = trade["close_price"]

    emoji = "✅" if pnl > 0 else "❌"
    fmt = lambda x: f"${x:,.2f}" if x > 100 else (f"${x:.4f}" if x > 1 else f"${x:.6f}")

    # Footer with overall stats
    footer = ""
    if data:
        total, wins, wr, total_pnl = get_overall_stats(data)
        pnl_sign = "+" if total_pnl >= 0 else ""
        footer = f"\n· {total} Signale | {wins} positiv | WR: {wr:.0f}% | Gesamt: {pnl_sign}${total_pnl:.2f}"

    # Visually distinct closing message
    # Simple: positive PnL = WIN, negative = LOSS. No exceptions.
    if pnl > 0:
        header = f"🏆💰 TRADE CLOSED — WIN 💰🏆"
        result_line = f"✅ +${pnl:.2f} ({roi:+.1f}%) ✅"
    else:
        header = f"🔴 TRADE CLOSED — LOSS 🔴"
        result_line = f"❌ ${pnl:.2f} ({roi:+.1f}%)"

    duration = ""
    if trade.get("open_time") and trade.get("close_time"):
        try:
            from datetime import datetime as dt2
            t1 = dt2.fromisoformat(trade["open_time"])
            t2 = dt2.fromisoformat(trade["close_time"])
            mins = int((t2 - t1).total_seconds() / 60)
            if mins < 60: duration = f"{mins}m"
            elif mins < 1440: duration = f"{mins//60}h {mins%60}m"
            else: duration = f"{mins//1440}d {(mins%1440)//60}h"
        except: pass

    msg = f"""[KODA SE] {header}
{'═'*30}
{coin} {d} | {trade.get('tf','')} | {reason}

Entry:  {fmt(entry)}
Exit:   {fmt(close)}
Dauer:  {duration}

{result_line}
{'═'*30}{footer}
KODA SE | 5x|55%TP|ms2 · {datetime.now(TZ).strftime('%H:%M')} ET"""

    send_tg_channel(msg)


def open_trade(data, tf_key, coin, direction, entry, tp, probability, tf, cascade_lights=0, cascade_code="00000"):
    """Open a new paper trade."""
    capital = CONFIG["capital"]
    leverage = CONFIG["leverage"]
    margin = capital
    size = capital * leverage / entry
    liq = calc_liquidation(entry, direction, margin, size)
    sl = calc_sl_price(entry, direction)

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
        # Adaptive SL
        "sl_tightened": False,
        # Cascade tracking
        "cascade_lights": cascade_lights,
        "cascade_code": cascade_code,
    }

    data[tf_key].append(trade)
    log(f"  OPENED {direction} {coin} @ {entry:.6f} | TP: {tp:.6f} | SL: {sl:.6f} (42% price) | Liq: {liq:.6f} | Prob: {probability}% | TF: {tf} | Cascade: {cascade_lights}")

    # Notify Telegram
    notify_trade_opened(trade)
    return trade


_consecutive_sl_count = 0
_drawdown_paused = False
_current_data = None  # global ref for TG notifications

def send_drawdown_alert(sl_count):
    """Send Telegram alert when drawdown brake activates — direct to Chris + channel."""
    text = (f"⚠️ DRAWDOWN-BREMSE AKTIV — KODA SE Bot\n\n"
            f"{sl_count} Verluste in Folge!\n"
            f"Bot ist PAUSIERT. Keine neuen Trades.\n"
            f"Offene Trades laufen weiter (TP/SL aktiv).\n\n"
            f"Bitte D/W Analyse durchführen.\n"
            f"Zum Fortfahren: Bot manuell neu starten.")
    send_tg_chris(text)    # direkt an Chris
    send_tg_channel(text)  # auch im Kanal
    log(f"DRAWDOWN ALERT sent ({sl_count} SLs in row)")


def close_trade(trade, close_price, reason):
    """Close a paper trade. Tracks consecutive SLs for drawdown brake."""
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

    # Notify Telegram
    notify_trade_closed(trade, _current_data)

    # Drawdown brake tracking
    if pnl <= 0:
        _consecutive_sl_count += 1
        log(f"  Consecutive losses: {_consecutive_sl_count}/{DRAWDOWN_BRAKE_SL_COUNT}")
        if _consecutive_sl_count >= DRAWDOWN_BRAKE_SL_COUNT and not _drawdown_paused:
            _drawdown_paused = True
            log(f"⚠️ DRAWDOWN BRAKE ACTIVATED — {_consecutive_sl_count} losses in a row! No new trades.")
            send_drawdown_alert(_consecutive_sl_count)
    else:
        if _consecutive_sl_count > 0:
            log(f"  Win resets consecutive loss counter (was {_consecutive_sl_count})")
        _consecutive_sl_count = 0

    return trade


def get_recent_highlow(sym, minutes=1):
    """Get high/low from last 1min candle(s) for precise 1min execution."""
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit={max(minutes, 2)}"
        req = urllib.request.Request(url, headers={"User-Agent": "PaperBot/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            klines = json.loads(resp.read().decode())
        if not klines:
            return None, None
        high = max(float(k[2]) for k in klines)
        low = min(float(k[3]) for k in klines)
        return high, low
    except:
        return None, None


def check_1min_confirmation(sym, direction):
    """
    1min entry confirmation: last closed 1min candle must agree with direction.
    LONG: 1min close > open (green candle)
    SHORT: 1min close < open (red candle)
    Returns (confirmed: bool, entry_price: float or None)
    """
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit=3"
        req = urllib.request.Request(url, headers={"User-Agent": "PaperBot/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            klines = json.loads(resp.read().decode())
        if not klines or len(klines) < 2:
            return False, None
        # Use second-to-last candle (last CLOSED 1min candle)
        last_closed = klines[-2]
        o, c = float(last_closed[1]), float(last_closed[4])
        current = float(klines[-1][4])  # current price from latest (open) candle
        if direction == "LONG" and c > o:
            return True, current
        elif direction == "SHORT" and c < o:
            return True, current
        return False, current
    except:
        return False, None


def check_adaptive_sl(trade, klines_for_coin):
    """
    Adaptive SL: Check EMA ribbon width. If ribbon narrows significantly,
    tighten SL to reduce risk.
    Returns adjusted SL price or None if no change needed.
    """
    if not klines_for_coin or len(klines_for_coin) < 34:
        return None

    width = get_ema_ribbon_width(klines_for_coin)
    if width is None:
        return None

    # If ribbon is very narrow (< 0.3%), tighten SL to 25% price move
    # If ribbon is moderately narrow (< 0.6%), tighten to 33% price move
    entry = trade["entry"]
    direction = trade["direction"]

    if width < 0.3:
        # Very narrow ribbon — trend weakening fast
        tightened_pct = 0.25
    elif width < 0.6:
        # Moderately narrow
        tightened_pct = 0.33
    else:
        return None  # No tightening needed

    if direction == "LONG":
        new_sl = entry * (1 - tightened_pct)
        current_sl = trade.get("sl", entry * (1 - 0.42))
        # Only tighten, never widen
        if new_sl > current_sl:
            return new_sl
    else:
        new_sl = entry * (1 + tightened_pct)
        current_sl = trade.get("sl", entry * (1 + 0.42))
        if new_sl < current_sl:
            return new_sl

    return None


def check_open_trades(data):
    """Check all open trades for TP, SL, or liquidation hits using kline highs/lows."""
    for tf_key in ["trades_15m", "trades_30m"]:  # Only 15m and 30m active
        open_trades = [t for t in data[tf_key] if t["status"] == "open"]
        if not open_trades:
            continue

        # Group by coin to minimize API calls
        coins_needed = set(t["coin"] for t in open_trades)
        price_data = {}  # coin -> (current, high, low)
        klines_cache = {}  # coin -> klines (for adaptive SL)
        for coin in coins_needed:
            sym = f"{coin}USDT"
            p = get_current_price(sym)
            h, l = get_recent_highlow(sym, 2)  # last 2x 1min candles
            if p is not None:
                price_data[coin] = (p, h or p, l or p)
            # Fetch 1min klines for adaptive SL check (1min execution)
            kl = fetch_klines(sym, "1m", 40)
            if kl:
                klines_cache[coin] = kl
            time.sleep(0.05)

        for trade in open_trades:
            coin = trade["coin"]
            if coin not in price_data:
                continue
            current_price, recent_high, recent_low = price_data[coin]

            # Update live price + unrealized PnL for dashboard display
            trade["current_price"] = round(current_price, 8)
            unrealized = calc_pnl(trade["direction"], trade["entry"], current_price, trade["size"])
            if trade.get("tp1_hit") and trade.get("tp1_pnl"):
                unrealized = unrealized * 0.5 + trade["tp1_pnl"]  # remaining 50% + realized TP1
            trade["pnl"] = round(unrealized, 2)
            trade["roi"] = round(unrealized / trade["margin"] * 100, 2) if trade["margin"] else 0

            # Get SL price (stored per trade, 42% price move)
            sl_price = trade.get("sl")
            if sl_price is None:
                sl_price = calc_sl_price(trade["entry"], trade["direction"])
                trade["sl"] = round(sl_price, 8)

            # Adaptive SL: check EMA ribbon and potentially tighten
            if coin in klines_cache and not trade.get("tp1_hit", False):
                adaptive_sl = check_adaptive_sl(trade, klines_cache[coin])
                if adaptive_sl is not None:
                    old_sl = sl_price
                    sl_price = adaptive_sl
                    trade["sl"] = round(sl_price, 8)
                    if not trade.get("sl_tightened"):
                        trade["sl_tightened"] = True
                        log(f"  ADAPTIVE SL: {coin} {trade['direction']} | SL tightened {old_sl:.6f} → {sl_price:.6f}")

            # ── TP1/TP2 Trailing Stop Logic ──
            tp1_hit = trade.get("tp1_hit", False)

            if trade["direction"] == "LONG":
                if not tp1_hit:
                    # Phase 1: waiting for TP1
                    if recent_high >= trade["tp"]:
                        # TP1 hit — record partial PnL, move SL to entry, start trailing
                        tp1_pnl = calc_pnl("LONG", trade["entry"], trade["tp"], trade["size"]) * 0.5
                        trade["tp1_hit"] = True
                        trade["tp1_pnl"] = round(tp1_pnl, 2)
                        trade["peak_price"] = trade["tp"]
                        log(f"  TP1 HIT: {coin} LONG @ {trade['tp']:.6f} | Partial PnL: ${tp1_pnl:.2f} | SL→Entry, trailing starts")
                    elif recent_low <= sl_price:
                        close_trade(trade, sl_price, "SL")
                        log(f"  SL HIT: {coin} LONG | Low {recent_low:.6f} <= SL {sl_price:.6f} (42% price)")
                    elif recent_low <= trade["liq"]:
                        close_trade(trade, trade["liq"], "LIQ")
                        log(f"  LIQ HIT: {coin} LONG | Low {recent_low:.6f} <= Liq {trade['liq']:.6f}")
                else:
                    # Phase 2: TP1 hit, trailing for TP2
                    peak = trade.get("peak_price", trade["tp"])
                    if recent_high > peak:
                        trade["peak_price"] = recent_high
                        peak = recent_high

                    # Trailing stop: 3% retrace from peak
                    trail_stop = peak * (1 - 0.03)
                    # BE stop: entry + fees buffer (0.15% covers round-trip fees at 5x)
                    be_stop = trade["entry"] * 1.0015

                    if recent_low <= be_stop:
                        tp2_pnl = 0.0
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(be_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+BE"
                        trade["status"] = "closed"
                        log(f"  TP2 BE: {coin} LONG | Back to entry | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: $0.00 = ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)
                    elif recent_low <= trail_stop:
                        tp2_pnl = calc_pnl("LONG", trade["entry"], trail_stop, trade["size"]) * 0.5
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(trail_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+TRAIL"
                        trade["status"] = "closed"
                        notify_trade_closed(trade, _current_data)
                        log(f"  TP2 TRAIL: {coin} LONG | Peak {peak:.6f} → Trail {trail_stop:.6f} | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: ${tp2_pnl:.2f} = ${total_pnl:.2f}")

            else:  # SHORT
                if not tp1_hit:
                    if recent_low <= trade["tp"]:
                        tp1_pnl = calc_pnl("SHORT", trade["entry"], trade["tp"], trade["size"]) * 0.5
                        trade["tp1_hit"] = True
                        trade["tp1_pnl"] = round(tp1_pnl, 2)
                        trade["peak_price"] = trade["tp"]
                        log(f"  TP1 HIT: {coin} SHORT @ {trade['tp']:.6f} | Partial PnL: ${tp1_pnl:.2f} | SL→Entry, trailing starts")
                    elif recent_high >= sl_price:
                        close_trade(trade, sl_price, "SL")
                        log(f"  SL HIT: {coin} SHORT | High {recent_high:.6f} >= SL {sl_price:.6f} (42% price)")
                    elif recent_high >= trade["liq"]:
                        close_trade(trade, trade["liq"], "LIQ")
                        log(f"  LIQ HIT: {coin} SHORT | High {recent_high:.6f} >= Liq {trade['liq']:.6f}")
                else:
                    peak = trade.get("peak_price", trade["tp"])
                    if recent_low < peak:
                        trade["peak_price"] = recent_low
                        peak = recent_low

                    trail_stop = peak * (1 + 0.03)
                    # BE stop: entry - fees buffer (0.15% covers round-trip fees at 5x)
                    be_stop = trade["entry"] * 0.9985

                    if recent_high >= be_stop:
                        tp2_pnl = 0.0
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(be_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+BE"
                        trade["status"] = "closed"
                        log(f"  TP2 BE: {coin} SHORT | Back to entry | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: $0.00 = ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)
                    elif recent_high >= trail_stop:
                        tp2_pnl = calc_pnl("SHORT", trade["entry"], trail_stop, trade["size"]) * 0.5
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(trail_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+TRAIL"
                        trade["status"] = "closed"
                        log(f"  TP2 TRAIL: {coin} SHORT | Peak {peak:.6f} → Trail {trail_stop:.6f} | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: ${tp2_pnl:.2f} = ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)


def update_stats(data):
    """Update statistics for both timeframes."""
    for tf_key, stats_key in [("trades_15m", "stats_15m"), ("trades_30m", "stats_30m"), ("trades_1h", "stats_1h"), ("trades_4h", "stats_4h")]:
        closed = [t for t in data[tf_key] if t["status"] == "closed"]
        if not closed:
            data[stats_key] = {
                "total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m",
                "open": len([t for t in data[tf_key] if t["status"] == "open"]),
            }
            continue

        wins = [t for t in closed if t.get("close_reason") in ("TP", "TP1+TRAIL", "TP1+BE")]
        losses = [t for t in closed if t.get("close_reason") in ("SL", "LIQ", "SMA_RISK", "SW_RISK")]
        total_pnl = sum(t["pnl"] for t in closed if t["pnl"])

        # Average duration
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


# ═══════════════════════════════════════════════════════════════
# SCAN & TRADE
# ═══════════════════════════════════════════════════════════════

def check_btc_spike():
    """Check if BTC moved >1% in last 15min — if so, pause new trades (fakeout protection)."""
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
        if move_pct > 1.0:
            return True
        return False
    except:
        return False


def scan_and_trade(data, tf, limit, tf_key):
    """Scan all coins and open trades where probability >= threshold."""
    now = datetime.now(TZ)
    log(f"\n{'='*60}")
    log(f"SCAN {tf.upper()} | {now.strftime('%Y-%m-%d %H:%M ET')}")
    log(f"{'='*60}")

    # Drawdown-Bremse
    if _drawdown_paused:
        log(f"  ⚠️ DRAWDOWN BRAKE ACTIVE — {_consecutive_sl_count} SLs in Folge. Keine neuen Trades.")
        log(f"  Offene Trades werden weiter überwacht. Manueller Restart nötig.")
        return

    # Fakeout-Bremse
    if check_btc_spike():
        log(f"  BTC SPIKE erkannt (>1% in 15min) — Bremse aktiv, kein neuer Trade.")
        return

    open_trades = [t for t in data[tf_key] if t["status"] == "open"]
    # Check ALL open trades across ALL timeframes — 1 coin = 1 trade max
    all_open = [t for tfk in ["trades_15m", "trades_30m"]
                for t in data.get(tfk, []) if t["status"] == "open"]
    open_coins = set(t["coin"] for t in all_open)

    # Max open trades limit
    max_key = f"max_open_{tf.replace('m','m').replace('h','h')}"
    max_open = CONFIG.get(max_key, 20)
    if len(open_trades) >= max_open:
        log(f"  Max open {tf} trades reached ({max_open}). Skipping scan.")
        return

    # Per-TF budget allocation
    tf_budget_key = f"tf_budget_{tf}"
    tf_budget_pct = CONFIG.get(tf_budget_key, 50)
    tf_budget_limit = CONFIG.get("total_budget", 1000) * (tf_budget_pct / 100.0)
    tf_margin_used = sum(t.get("margin", CONFIG["capital"]) for t in open_trades)
    if tf_budget_pct == 0:
        log(f"  TF Budget {tf}: deaktiviert (0%).")
        return
    if tf_margin_used >= tf_budget_limit:
        log(f"  TF Budget {tf}: ${tf_margin_used:.0f} / ${tf_budget_limit:.0f} ({tf_budget_pct}%) — voll.")
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
        log(f"  Budget: ${current_budget:.0f} (Start ${CONFIG['total_budget']:.0f} + PnL ${realized_pnl:+.0f}) | Margin used: ${margin_used:.0f} | Frei: ${budget_available:.0f} — kein Platz.")
        return

    signals_found = 0
    trades_opened = 0

    # Build recent LIQ history for cooldown check
    recent_liqs = {}
    for tfk in ["trades_15m", "trades_30m"]:
        for t in data.get(tfk, []):
            if t.get("close_reason") == "LIQ" and t.get("close_time"):
                try:
                    ct = datetime.fromisoformat(t["close_time"])
                    age_min = (now - ct.replace(tzinfo=TZ if ct.tzinfo is None else ct.tzinfo)).total_seconds() / 60
                    if age_min < 30:
                        key = t["coin"]
                        if key not in recent_liqs or t["close_time"] > recent_liqs[key]["close_time"]:
                            recent_liqs[key] = {"direction": t["direction"], "close_time": t["close_time"]}
                except:
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

            # Validate TP makes sense
            if direction == "LONG" and tp <= entry:
                continue
            if direction == "SHORT" and tp >= entry:
                continue

            # Cooldown after LIQ
            if coin in recent_liqs:
                liq_dir = recent_liqs[coin]["direction"]
                if direction == liq_dir:
                    log(f"  COOLDOWN: {coin} {direction} — gleiche Richtung wie LIQ vor <30min. Uebersprungen.")
                    continue

            if probability >= CONFIG["min_probability"]:
                signals_found += 1

                # ── MTF GATE: BTC 1H SMA20 vs SMA50 ──
                if not check_mtf_gate(direction):
                    continue

                # ── Kaskaden-Ampel Filter ──
                bull_lights, bear_lights, cascade_dir, cascade_details = get_cascade_signal()
                if direction == "LONG":
                    lights_in_dir = bull_lights
                else:
                    lights_in_dir = bear_lights

                log(f"  SIGNAL: {coin} {direction} | Prob: {probability}% | Bias: {result['coin_bias']} | BTC: {result['btc_trend']}")
                log(f"          Scores: {result['scores']} | L:{result['long_count']} S:{result['short_count']}")
                log(f"          Cascade: {bull_lights}B/{bear_lights}S → {cascade_dir} | Lights in dir: {lights_in_dir} | {cascade_details}")

                # Cascade gate: 0-1 lights → SKIP
                if lights_in_dir <= 1:
                    log(f"  CASCADE SKIP: {coin} {direction} — only {lights_in_dir} lights. Need >=2.")
                    continue

                # Adjust TP based on cascade lights
                tp_adjusted = tp
                if lights_in_dir == 2:
                    em = result["expected_move"]
                    if direction == "LONG":
                        tp_adjusted = entry + em * 0.50
                    else:
                        tp_adjusted = entry - em * 0.50
                    log(f"  CASCADE 2-LIGHT: TP reduced to 50% EM → {tp_adjusted:.6f}")
                elif lights_in_dir == 5:
                    log(f"  CASCADE 5-LIGHT: Full alignment — extended trailing flagged")

                tp = tp_adjusted

                # Validate TP still makes sense after adjustment
                if direction == "LONG" and tp <= entry:
                    continue
                if direction == "SHORT" and tp >= entry:
                    continue

                # Check max open limit again
                current_open = len([t for t in data[tf_key] if t["status"] == "open"])
                if current_open >= max_open:
                    log(f"  Max open {tf} trades reached. Stopping scan.")
                    break

                # ── 1min Entry Confirmation ──
                sym_check = f"{coin.upper()}USDT"
                confirmed, entry_1m = check_1min_confirmation(sym_check, direction)
                if not confirmed:
                    log(f"  1min CONFIRM FAIL: {coin} {direction} — last 1min candle doesn't confirm. Queued for retry.")
                    # Add to pending signals for retry in next 1min check
                    if not hasattr(scan_and_trade, '_pending'):
                        scan_and_trade._pending = []
                    scan_and_trade._pending.append({
                        "coin": coin, "direction": direction, "tp": tp, "probability": probability,
                        "tf": tf, "tf_key": tf_key, "lights_in_dir": lights_in_dir,
                        "cascade_details": cascade_details, "retries": 0, "max_retries": 5,
                    })
                    continue

                # Use 1min price for better entry
                if entry_1m is not None:
                    # Recalculate TP with 1min entry price
                    price_diff_pct = abs(entry_1m - entry) / entry * 100
                    if price_diff_pct < 1.0:  # Max 1% difference allowed
                        old_entry = entry
                        entry = entry_1m
                        # Adjust TP proportionally
                        if direction == "LONG":
                            tp = entry + abs(tp - old_entry)
                        else:
                            tp = entry - abs(old_entry - tp)
                        log(f"  1min ENTRY: {coin} {direction} | Signal price {old_entry:.6f} → 1min entry {entry:.6f}")

                # Build cascade code
                code_map = {"BULL": "1", "BEAR": "2", "SIDE": "0", "NO_DATA": "0"}
                c_code = "".join(code_map.get(cascade_details.get(tf_c, "0"), "0") for tf_c in ["5m", "15m", "30m", "1h", "4h"])
                open_trade(data, tf_key, coin, direction, entry, tp, probability, tf, cascade_lights=lights_in_dir, cascade_code=c_code)
                trades_opened += 1

        except Exception as e:
            log(f"  ERROR analyzing {coin}: {e}")
            continue

        time.sleep(0.1)

    log(f"\nScan complete: {signals_found} signals found, {trades_opened} trades opened")
    open_count = len([t for t in data[tf_key] if t["status"] == "open"])
    log(f"Open {tf} trades: {open_count}")


# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

def log(msg):
    """Print timestamped log message."""
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    print(f"[{ts}] [KODA-SE] {msg}", flush=True)


# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def check_pending_1min_entries(data):
    """Check pending signals that failed 1min confirmation — retry on next 1min candle."""
    if not hasattr(scan_and_trade, '_pending') or not scan_and_trade._pending:
        return

    still_pending = []
    for sig in scan_and_trade._pending:
        sig["retries"] += 1
        if sig["retries"] > sig["max_retries"]:
            log(f"  1min EXPIRED: {sig['coin']} {sig['direction']} — {sig['max_retries']} retries, signal dropped.")
            continue

        coin = sig["coin"]
        direction = sig["direction"]
        sym = f"{coin}USDT"

        # Check if coin already has open trade
        all_open = [t for tfk in ["trades_15m", "trades_30m"]
                    for t in data.get(tfk, []) if t["status"] == "open"]
        if coin in set(t["coin"] for t in all_open):
            continue

        confirmed, entry_1m = check_1min_confirmation(sym, direction)
        if not confirmed:
            still_pending.append(sig)
            continue

        # Confirmed — open trade with 1min price
        entry = entry_1m if entry_1m else get_current_price(sym)
        if entry is None:
            still_pending.append(sig)
            continue

        tp = sig["tp"]
        # Adjust TP to new entry
        if direction == "LONG":
            tp_dist = abs(tp - entry)
            tp = entry + tp_dist
            if tp <= entry:
                continue
        else:
            tp_dist = abs(entry - tp)
            tp = entry - tp_dist
            if tp >= entry:
                continue

        code_map = {"BULL": "1", "BEAR": "2", "SIDE": "0", "NO_DATA": "0"}
        c_details = sig.get("cascade_details", {})
        c_code = "".join(code_map.get(c_details.get(tf_c, "0"), "0") for tf_c in ["5m", "15m", "30m", "1h", "4h"])

        log(f"  1min CONFIRM OK (retry #{sig['retries']}): {coin} {direction} @ {entry:.6f}")
        open_trade(data, sig["tf_key"], coin, direction, entry, tp, sig["probability"], sig["tf"],
                   cascade_lights=sig["lights_in_dir"], cascade_code=c_code)

    scan_and_trade._pending = still_pending


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


def main():
    log("KODA SE Paper Bot — Full Stack 7-Score + Kaskaden starting...")
    log(f"Strategy: 5x Leverage, $50/trade, $1000 Budget")
    log(f"SL: 42% PRICE move (= 210% margin loss at 5x — LIQ at 20% protects)")
    log(f"TP: 55% Expected Move | MTF Gate: BTC 1H SMA20 vs SMA50")
    log(f"Drawdown-Bremse: {DRAWDOWN_BRAKE_SL_COUNT} SLs in Folge → Pause + Telegram")
    log(f"Score gate: >= 2/7 | Min probability: {CONFIG['min_probability']}%")
    log(f"TP1/TP2: 50% close at TP1, SL→Entry, 3% trail from peak")
    log(f"Adaptive SL: Tighten when EMA ribbon narrows")
    log(f"Max simultaneous: {CONFIG['total_budget'] // CONFIG['capital']} trades")
    log(f"Coins: {len(COINS)} | Data file: {DATA_FILE}")
    log(f"Timeframes: 15m (50%) + 30m (50%)")
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

            # 15min scan: run at 0, 15, 30, 45
            current_15m_slot = (hour * 60 + minute) // 15
            if minute % 15 == 0 and current_15m_slot != last_15m_scan:
                last_15m_scan = current_15m_slot
                scan_and_trade(data, "15m", 800, "trades_15m")
                update_stats(data)
                save_data(data)
                print_status(data)

            # 30m scan: run at 0, 30
            current_30m_slot = (hour * 60 + minute) // 30
            if minute % 30 == 0 and current_30m_slot != last_30m_scan:
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
                    subprocess.run(["git", "add", "paper_trades_optimal.json"], cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
                    subprocess.run(["git", "commit", "-m", "KODA Optimal paper bot data update"], cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
                    subprocess.run(["git", "push"], cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=30)
                except:
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
