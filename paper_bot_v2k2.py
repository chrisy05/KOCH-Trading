#!/usr/bin/env python3
"""
Paper Trading Bot V2K2 — GLM Solo
V2 Strategie NUR auf GLM (100% WR, 23 Trades in 14d).
$2.000 Kapital, $200/Trade, 10x Hebel.
Start: 07.06.2026
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
    "capital": 200,
    "leverage": 10,
    "min_probability": 60,
    "tp_range_pct": 70,       # 70% of expected move
    "sl_pct": 40,             # V2: SL bei 40% Verlust der Margin
    "max_open_15m": 50,
    "max_trades_per_coin_1h": 1,  # per day
    "max_open_4h": 3,
    "total_budget": 2000,     # Gesamtkapital $2.000
    "tf_budget_15m": 50,      # % of total budget for 15m trades
    "tf_budget_30m": 30,      # % of total budget for 30m trades
    "tf_budget_1h": 20,       # % of total budget for 1h trades
    "tf_budget_4h": 0,        # % of total budget for 4h trades (0 = disabled)
}

# Load config overrides from JSON file (written by dashboard settings)
_CFG_OVERRIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_bot_v2k2_config.json")
if os.path.exists(_CFG_OVERRIDE):
    try:
        with open(_CFG_OVERRIDE, "r") as _f:
            CONFIG.update(json.load(_f))
    except Exception:
        pass

# V2K2: NUR GLM — Scanner-Gewinner mit 100% WR über 14 Tage, 23 Trades
COINS = ["GLM"]

TZ = timezone(timedelta(hours=-4))
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trades_v2k2.json")

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


def full_analyze(coin, tf="15m", limit=800):
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
            # Neutral coin — BTC decides
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

    raw_a = calc_prob("LONG")   # BTC Long scenario
    raw_c = calc_prob("SHORT")  # BTC Short scenario
    raw_total = raw_a + raw_c
    prob_a = round(raw_a / raw_total * 100) if raw_total > 0 else 50
    prob_c = 100 - prob_a

    # 12. ATR and expected move
    atr14 = calc_atr(klines, 14)
    tf_mins = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
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
    # prob_a = confidence for "BTC Long" scenario
    # prob_c = confidence for "BTC Short" scenario
    # Use actual BTC trend to pick which scenario is active
    btc = analyze_btc(tf)
    btc_trend = btc.get("trend", "SIDEWAYS")

    # Determine direction from coin bias + BTC context
    # When BTC is bullish: LONG scenarios get prob_a, SHORT scenarios get prob_c
    # When BTC is bearish: SHORT scenarios get prob_c (higher), LONG scenarios get prob_a (lower)
    if btc_trend == "BULLISH":
        # BTC Long scenario is active -> prob_a applies
        # Direction preference: LONG (aligned with BTC)
        if coin_bias == "LONG":
            direction = "LONG"
            probability = prob_a  # high: coin + BTC aligned
        elif coin_bias == "SHORT":
            direction = "SHORT"
            probability = prob_c  # low: coin vs BTC
        else:
            # Neutral coin, BTC gives direction
            direction = "LONG"
            probability = prob_a
    elif btc_trend == "BEARISH":
        # BTC Short scenario is active -> prob_c applies
        if coin_bias == "SHORT":
            direction = "SHORT"
            probability = prob_c  # high: coin + BTC aligned
        elif coin_bias == "LONG":
            direction = "LONG"
            probability = prob_a  # low: coin vs BTC
        else:
            # Neutral coin, BTC gives direction
            direction = "SHORT"
            probability = prob_c
    else:
        # BTC sideways: use coin bias, take the higher probability
        if coin_bias == "LONG":
            direction = "LONG"
            probability = prob_a
        elif coin_bias == "SHORT":
            direction = "SHORT"
            probability = prob_c
        else:
            # Both neutral — pick higher prob
            if prob_a >= prob_c:
                direction = "LONG"
                probability = prob_a
            else:
                direction = "SHORT"
                probability = prob_c

    # Calculate TP based on direction
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
    }


# ═══════════════════════════════════════════════════════════════
# TRADE MANAGEMENT
# ═══════════════════════════════════════════════════════════════

def load_data():
    """Load paper trades from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            # Ensure new TF keys exist (backward compat)
            empty_stats = {"total": 0, "wins": 0, "losses": 0, "winrate": 0.0,
                           "total_pnl": 0.0, "avg_pnl": 0.0, "avg_duration": "0m"}
            for key in ["trades_30m", "trades_4h"]:
                if key not in data:
                    data[key] = []
            for key in ["stats_30m", "stats_4h"]:
                if key not in data:
                    data[key] = dict(empty_stats)
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
            "sl_pct": CONFIG.get("sl_pct", 0),
            "start_date": datetime.now(TZ).strftime("%Y-%m-%d"),
            "end_date": (datetime.now(TZ) + timedelta(days=7)).strftime("%Y-%m-%d"),
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
    data["_heartbeat"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    """Save paper trades to JSON file."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"ERROR saving data: {e}")


def next_trade_id(data, tf_key):
    """Get next trade ID for a given timeframe."""
    trades = data.get(tf_key, [])
    if not trades:
        return 1
    return max(t.get("id", 0) for t in trades) + 1


def calc_liquidation(entry, direction, margin, size):
    """Calculate liquidation price.
    LONG: entry - (margin - margin*0.005) / size
    SHORT: entry + (margin - margin*0.005) / size
    """
    maint = margin * 0.005
    net_margin = margin - maint
    if direction == "LONG":
        return entry - net_margin / size
    else:
        return entry + net_margin / size


def calc_pnl(direction, entry, close_price, size):
    """Calculate PnL for a trade."""
    if direction == "LONG":
        return (close_price - entry) * size
    else:
        return (entry - close_price) * size


def get_btc_sma_data():
    """Holt BTC 1H Klines und berechnet alle SMA-Werte.
    Returns: dict mit price, sma10, sma20, sma50, sma100 oder None bei Fehler.
    """
    try:
        url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=100"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            klines = json.loads(r.read())
        if len(klines) < 100:
            return None
        closes = [float(k[4]) for k in klines]
        return {
            "price": closes[-1],
            "sma10": sum(closes[-10:]) / 10,
            "sma20": sum(closes[-20:]) / 20,
            "sma50": sum(closes[-50:]) / 50,
            "sma100": sum(closes[-100:]) / 100,
        }
    except:
        return None


def get_btc_sma_alignment(direction):
    """SMA-Cross Alignment für Hebel-Bestimmung.

    Für SHORT: Bärisches Alignment = SMA10 < SMA20 < SMA50 < SMA100
    Für LONG:  Bullisches Alignment = SMA10 > SMA20 > SMA50 > SMA100

    Stufen (Anzahl aligned SMA-Crosses):
    - 0 Crosses aligned (Gegentrend)     → 0x (SKIP)
    - 1 Cross aligned (SMA10/20)          → 5x
    - 2 Crosses aligned (+ SMA20/50)      → 7x
    - 3 Crosses aligned (+ SMA50/100)     → 10x (Trend bestätigt)
    - 3 Crosses + Preis aligned           → 12x (Vollgas, nur 15m)

    Zusätzlich: Preis-vs-SMA Check als Sicherheit.
    """
    btc = get_btc_sma_data()
    if not btc:
        return CONFIG["leverage"], 0, "Keine Daten"

    c = btc["price"]
    sma10, sma20, sma50, sma100 = btc["sma10"], btc["sma20"], btc["sma50"], btc["sma100"]

    if direction == "SHORT":
        # Preis-Check: Über SMA100 = komplett SKIP
        if c > sma100:
            return 0, 0, f"SKIP: BTC ${c:.0f} > SMA100 ${sma100:.0f}"
        # Zwischen SMA50 und SMA100 = erlaubt mit 5x (Übergangszone)
        if c > sma50:
            return 5, 0, f"Übergang: BTC ${c:.0f} > SMA50 ${sma50:.0f} — 5x erlaubt"

        # Cross-Alignment zählen (bärisch = kleiner SMA unter größerem)
        crosses = 0
        if sma10 < sma20: crosses += 1
        if sma20 < sma50: crosses += 1
        if sma50 < sma100: crosses += 1

        # Preis unter SMA10 = Extra-Bestätigung
        price_aligned = c < sma10

        if crosses == 0:
            return 0, 0, f"SKIP: Kein bärisches Cross (SMA10>${sma10:.0f} SMA20>${sma20:.0f})"
        elif crosses == 1:
            return 5, 1, f"1 Cross (SMA10<SMA20)"
        elif crosses == 2:
            return 7, 2, f"2 Crosses (10<20<50)"
        else:  # 3
            if price_aligned:
                return 12, 4, f"VOLLGAS: Alle Crosses + Preis aligned"
            return 10, 3, f"3 Crosses aligned (10<20<50<100)"

    else:  # LONG
        # Über SMA100 = komplett SKIP
        if c < sma100:
            # Zwischen SMA50 und SMA100 = erlaubt mit 5x (Übergangszone)
            if c > sma50:
                return 5, 0, f"Übergang: BTC ${c:.0f} zwischen SMA50 ${sma50:.0f} und SMA100 ${sma100:.0f} — 5x"
            return 0, 0, f"SKIP: BTC ${c:.0f} < SMA50 ${sma50:.0f}"

        crosses = 0
        if sma10 > sma20: crosses += 1
        if sma20 > sma50: crosses += 1
        if sma50 > sma100: crosses += 1

        price_aligned = c > sma10

        if crosses == 0:
            return 0, 0, f"SKIP: Kein bullisches Cross"
        elif crosses == 1:
            return 5, 1, f"1 Cross (SMA10>SMA20)"
        elif crosses == 2:
            return 7, 2, f"2 Crosses (10>20>50)"
        else:
            if price_aligned:
                return 12, 4, f"VOLLGAS: Alle Crosses + Preis aligned"
            return 10, 3, f"3 Crosses aligned (10>20>50>100)"


# Cache für BTC SMA um API-Calls zu reduzieren (max 1x pro Minute)
_btc_sma_cache = {"data": None, "ts": 0}

def get_btc_sma_cached():
    """BTC SMA Daten mit 60s Cache."""
    now = time.time()
    if _btc_sma_cache["data"] and now - _btc_sma_cache["ts"] < 60:
        return _btc_sma_cache["data"]
    data = get_btc_sma_data()
    if data:
        _btc_sma_cache["data"] = data
        _btc_sma_cache["ts"] = now
    return data


def get_btc_sma_leverage(direction):
    """Wrapper für Abwärtskompatibilität — nutzt jetzt SMA-Cross Alignment."""
    leverage, alignment, reason = get_btc_sma_alignment(direction)
    return leverage


# Cooldown-Tracker für SMA-Risk: {direction: {"triggered_at": timestamp, "count": int}}
_sma_risk_cooldown = {}

def manage_open_risk(data, price_cache=None):
    """Dynamischer Risk Manager — schließt offene Trades bei BTC-Trendwechsel.

    Nutzt price_cache aus check_open_trades um doppelte API-Calls zu vermeiden.
    Sortiert NUR nach Verlustgröße (kleinste zuerst).
    5-Min-Cooldown bei kurzem Stufen-Berührung, max 2x — danach wird sofort geschlossen.

    Stufen:
    - 0 (kein Cross aligned): ALLE schließen
    - 1 (1 Cross): Trades mit >5% Verlust schließen
    - 2 (2 Crosses): Trades mit >15% Verlust schließen
    - 3+ (voll aligned): Nichts schließen
    """
    global _sma_risk_cooldown

    btc = get_btc_sma_cached()
    if not btc:
        return

    now = time.time()

    for direction in ["SHORT", "LONG"]:
        leverage, alignment, reason = get_btc_sma_alignment(direction)

        # Stufe 3+ = alles OK, Cooldown zurücksetzen
        if alignment >= 3:
            _sma_risk_cooldown.pop(direction, None)
            continue

        # Übergangszone (leverage=5, alignment=0): Trade wurde bewusst eröffnet → tolerieren
        if leverage > 0 and alignment == 0 and "Übergang" in reason:
            _sma_risk_cooldown.pop(direction, None)
            continue

        # Cooldown-Logik: 5 Min Pause, max 2x bevor Force-Close
        cd = _sma_risk_cooldown.get(direction)
        if cd:
            elapsed = now - cd["triggered_at"]
            if elapsed < 300 and cd["count"] < 2:
                # Noch im Cooldown und unter 2x → warten
                continue
            elif elapsed >= 300 and cd["count"] < 2:
                # Cooldown abgelaufen, aber Stufe immer noch schlecht → zählen
                _sma_risk_cooldown[direction] = {"triggered_at": now, "count": cd["count"] + 1}
                log(f"  SMA-RISK Cooldown #{cd['count']+1} für {direction} | Alignment {alignment} | {reason}")
                continue
            # count >= 2 → kein Cooldown mehr, sofort schließen
        else:
            # Erstes Mal diese Stufe → Cooldown starten
            _sma_risk_cooldown[direction] = {"triggered_at": now, "count": 1}
            log(f"  SMA-RISK Cooldown #1 für {direction} | Alignment {alignment} | {reason}")
            continue

        # Ab hier: Cooldown 2x überschritten → Force Close
        log(f"  SMA-RISK FORCE: {direction} Alignment {alignment} nach 2x Cooldown | {reason}")

        # Verlust-Schwelle je nach Stufe
        if alignment == 0:
            max_loss_pct = 0      # ALLE schließen
        elif alignment == 1:
            max_loss_pct = -5     # >5% Verlust schließen
        elif alignment == 2:
            max_loss_pct = -15    # >15% Verlust schließen

        closed_count = 0
        for tf_key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
            open_trades = [t for t in data[tf_key]
                          if t["status"] == "open" and t["direction"] == direction]

            if not open_trades:
                continue

            trades_with_pnl = []
            for trade in open_trades:
                coin = trade["coin"]
                if price_cache and coin in price_cache:
                    price = price_cache[coin][0]
                else:
                    continue

                pnl = calc_pnl(direction, trade["entry"], price, trade.get("size", 0))
                margin = trade.get("margin", CONFIG["capital"])
                pnl_pct = (pnl / margin * 100) if margin > 0 else 0
                trades_with_pnl.append((trade, price, pnl, pnl_pct))

            # Sortiere NUR nach Verlustgröße (kleinste Verluste zuerst)
            trades_with_pnl.sort(key=lambda x: x[3])

            for trade, price, pnl, pnl_pct in trades_with_pnl:
                if alignment == 0 or pnl_pct < max_loss_pct:
                    close_trade(trade, price, "SMA_RISK")
                    log(f"  SMA-RISK: {trade['coin']} {direction} closed | PnL: ${pnl:.2f} ({pnl_pct:+.1f}%) | Alignment {alignment}")
                    closed_count += 1

        if closed_count > 0:
            log(f"  SMA-RISK: {closed_count} {direction} Trades geschlossen")
            # Cooldown zurücksetzen nach Aktion
            _sma_risk_cooldown.pop(direction, None)


def check_coin_sma(coin, direction):
    """Coin-eigener SMA-Filter auf 1H.
    Prüft ob der Coin selbst gegen die Trade-Richtung läuft.
    Returns: (skip: bool, penalty: int, reason: str)
    """
    try:
        sym = f"{coin}USDT"
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=50"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            klines = json.loads(r.read())
        if len(klines) < 50:
            return False, 0, "OK (zu wenig Daten)"

        closes = [float(k[4]) for k in klines]
        c = closes[-1]
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50

        if direction == "SHORT":
            if c > sma50:
                return True, 0, f"{coin} ${c:.4f} > SMA50 ${sma50:.4f} — Coin bullish"
            if c > sma20:
                return False, 5, f"{coin} > SMA20 — leicht bullish"
            return False, 0, "OK"
        else:  # LONG
            if c < sma50:
                return True, 0, f"{coin} ${c:.4f} < SMA50 ${sma50:.4f} — Coin bearish"
            if c < sma20:
                return False, 5, f"{coin} < SMA20 — leicht bearish"
            return False, 0, "OK"
    except:
        return False, 0, "OK (Error)"


def open_trade(data, tf_key, coin, direction, entry, tp, probability, tf):
    """Open a new paper trade with SMA-Cross Alignment leverage."""
    capital = CONFIG["capital"]

    # SMA-Cross Alignment: Hebel basierend auf BTC Trendstärke
    leverage, alignment, sma_reason = get_btc_sma_alignment(direction)
    if leverage == 0:
        log(f"  SMA-SKIP: {coin} {direction} — {sma_reason}")
        return None

    # 12x nur für 15m erlauben, sonst auf 10x begrenzen
    if leverage > 10 and tf != "15m":
        leverage = 10

    log(f"  SMA-Alignment: {alignment}/4 | {leverage}x | {sma_reason}")

    # Coin-eigener SMA-Filter
    coin_skip, coin_penalty, coin_reason = check_coin_sma(coin, direction)
    if coin_skip:
        log(f"  COIN-SMA: {coin} {direction} übersprungen — {coin_reason}")
        return None
    if coin_penalty > 0 and leverage >= 10:
        leverage = max(7, leverage - 3)
        log(f"  COIN-SMA: {coin} {direction} gebremst auf {leverage}x — {coin_reason}")

    margin = capital
    size = capital * leverage / entry
    liq = calc_liquidation(entry, direction, margin, size)

    trade = {
        "id": next_trade_id(data, tf_key),
        "coin": coin,
        "tf": tf,
        "direction": direction,
        "entry": round(entry, 8),
        "tp": round(tp, 8),
        "liq": round(liq, 8),
        "size": round(size, 4),
        "margin": margin,
        "probability": probability,
        "leverage": leverage,
        "open_time": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S"),
        "close_time": None,
        "close_price": None,
        "close_reason": None,
        "pnl": None,
        "roi": None,
        "status": "open",
    }

    data[tf_key].append(trade)
    log(f"  OPENED {direction} {coin} @ {entry:.6f} | TP: {tp:.6f} | Liq: {liq:.6f} | Prob: {probability}% | TF: {tf}")
    return trade


def close_trade(trade, close_price, reason):
    """Close a paper trade."""
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
    return trade


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
    except:
        return None, None


def check_open_trades(data):
    """Check all open trades for TP or liquidation hits using kline highs/lows.
    Returns price_cache dict: {coin: (current, high, low)} for reuse by manage_open_risk.
    """
    all_price_cache = {}
    for tf_key in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        open_trades = [t for t in data[tf_key] if t["status"] == "open"]
        if not open_trades:
            continue

        # Group by coin to minimize API calls
        coins_needed = set(t["coin"] for t in open_trades)
        price_data = {}  # coin -> (current, high, low)
        for coin in coins_needed:
            sym = f"{coin}USDT"
            p = get_current_price(sym)
            h, l = get_recent_highlow(sym, 3)
            if p is not None:
                price_data[coin] = (p, h or p, l or p)
                all_price_cache[coin] = (p, h or p, l or p)
            time.sleep(0.05)

        for trade in open_trades:
            coin = trade["coin"]
            if coin not in price_data:
                continue
            current_price, recent_high, recent_low = price_data[coin]

            # SL check: close at sl_pct% loss before liquidation
            sl_pct = CONFIG.get("sl_pct", 0)
            sl_price = None
            if sl_pct > 0:
                margin = trade.get("margin", CONFIG["capital"])
                size = trade.get("size", 0)
                if size > 0:
                    sl_loss = margin * (sl_pct / 100.0)
                    if trade["direction"] == "LONG":
                        sl_price = trade["entry"] - sl_loss / size
                    else:
                        sl_price = trade["entry"] + sl_loss / size

            if trade["direction"] == "LONG":
                # Check TP hit (using high — wick up counts)
                if recent_high >= trade["tp"]:
                    close_trade(trade, trade["tp"], "TP")
                # Check SL hit before LIQ
                elif sl_price is not None and recent_low <= sl_price:
                    close_trade(trade, sl_price, "SL")
                    log(f"  SL HIT: {coin} LONG | Low {recent_low:.6f} <= SL {sl_price:.6f} ({sl_pct}%)")
                # Check LIQ hit (using low — wick down counts)
                elif recent_low <= trade["liq"]:
                    close_trade(trade, trade["liq"], "LIQ")
                    log(f"  LIQ HIT: {coin} LONG | Low {recent_low:.6f} <= Liq {trade['liq']:.6f}")
            else:  # SHORT
                # Check TP hit (using low — wick down counts)
                if recent_low <= trade["tp"]:
                    close_trade(trade, trade["tp"], "TP")
                # Check SL hit before LIQ
                elif sl_price is not None and recent_high >= sl_price:
                    close_trade(trade, sl_price, "SL")
                    log(f"  SL HIT: {coin} SHORT | High {recent_high:.6f} >= SL {sl_price:.6f} ({sl_pct}%)")
                # Check LIQ hit (using high — wick up counts)
                elif recent_high >= trade["liq"]:
                    close_trade(trade, trade["liq"], "LIQ")
                    log(f"  LIQ HIT: {coin} SHORT | High {recent_high:.6f} >= Liq {trade['liq']:.6f}")

    return all_price_cache


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

        wins = [t for t in closed if t.get("close_reason") == "TP"]
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

        # Also count open trades
        open_count = len([t for t in data[tf_key] if t["status"] == "open"])
        data[stats_key]["open"] = open_count


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
        open_price = float(klines[0][1])  # open of 15min ago
        close_price = float(klines[-1][4])  # close of latest
        move_pct = abs(close_price - open_price) / open_price * 100
        if move_pct > 1.0:
            return True  # Spike detected
        return False
    except:
        return False


def scan_and_trade(data, tf, limit, tf_key):
    """Scan all coins and open trades where probability >= threshold."""
    now = datetime.now(TZ)
    log(f"\n{'='*60}")
    log(f"SCAN {tf.upper()} | {now.strftime('%Y-%m-%d %H:%M ET')}")
    log(f"{'='*60}")

    # Fakeout-Bremse: wenn BTC >1% in 15min bewegt → 15min warten
    if check_btc_spike():
        log(f"  BTC SPIKE erkannt (>1% in 15min) — Bremse aktiv, kein neuer Trade.")
        return

    open_trades = [t for t in data[tf_key] if t["status"] == "open"]
    # Check ALL open trades across ALL timeframes — 1 coin = 1 trade max
    all_open = [t for tf in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]
                for t in data.get(tf, []) if t["status"] == "open"]
    open_coins = set(t["coin"] for t in all_open)

    # For 15m: check max open trades limit
    if tf_key == "trades_15m" and len(open_trades) >= CONFIG["max_open_15m"]:
        log(f"  Max open 15m trades reached ({CONFIG['max_open_15m']}). Skipping scan.")
        return

    # For 4h: check max open trades limit
    if tf_key == "trades_4h" and len(open_trades) >= CONFIG["max_open_4h"]:
        log(f"  Max open 4h trades reached ({CONFIG['max_open_4h']}). Skipping scan.")
        return

    # Per-TF budget allocation
    tf_budget_key = f"tf_budget_{tf}"  # tf_budget_15m, tf_budget_30m, etc.
    tf_budget_pct = CONFIG.get(tf_budget_key, 25)
    tf_budget_limit = CONFIG.get("total_budget", 10000) * (tf_budget_pct / 100.0)
    tf_margin_used = sum(t.get("margin", CONFIG["capital"]) for t in open_trades)
    if tf_budget_pct == 0:
        log(f"  TF Budget {tf}: deaktiviert (0%).")
        return
    if tf_margin_used >= tf_budget_limit:
        log(f"  TF Budget {tf}: ${tf_margin_used:.0f} / ${tf_budget_limit:.0f} ({tf_budget_pct}%) — voll.")
        return

    # Budget check: starting budget + realized PnL - margin in open trades
    all_closed_trades = []
    for tfk in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        all_closed_trades.extend([t for t in data.get(tfk, []) if t["status"] == "closed"])
    realized_pnl = sum(t.get("pnl", 0) or 0 for t in all_closed_trades)
    margin_used = sum(t.get("margin", CONFIG["capital"]) for t in all_open)
    current_budget = CONFIG.get("total_budget", 10000) + realized_pnl
    budget_available = current_budget - margin_used
    if budget_available < CONFIG["capital"]:
        log(f"  Budget: ${current_budget:.0f} (Start ${CONFIG['total_budget']:.0f} + PnL ${realized_pnl:+.0f}) | Margin used: ${margin_used:.0f} | Frei: ${budget_available:.0f} — kein Platz.")
        return

    # For 1h: track daily trades per coin
    today = now.strftime("%Y-%m-%d")

    signals_found = 0
    trades_opened = 0

    # Build recent LIQ history for cooldown check
    recent_liqs = {}  # coin -> {direction, close_time}
    for tfk in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
        for t in data.get(tfk, []):
            if t.get("close_reason") == "LIQ" and t.get("close_time"):
                try:
                    ct = datetime.fromisoformat(t["close_time"])
                    age_min = (now - ct.replace(tzinfo=TZ if ct.tzinfo is None else ct.tzinfo)).total_seconds() / 60
                    if age_min < 30:  # Only last 30 minutes
                        key = t["coin"]
                        if key not in recent_liqs or t["close_time"] > recent_liqs[key]["close_time"]:
                            recent_liqs[key] = {"direction": t["direction"], "close_time": t["close_time"]}
                except:
                    pass

    for coin in COINS:
        # Skip if already in an open trade for this coin+tf
        if coin in open_coins:
            continue

        # 1h mode: max 1 trade per coin per day
        if tf_key == "trades_1h":
            today_trades = [t for t in data[tf_key]
                           if t["coin"] == coin and t.get("open_time", "").startswith(today)]
            if len(today_trades) >= CONFIG["max_trades_per_coin_1h"]:
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

            # Cooldown: nach LIQ 30min Sperre fuer gleiche Richtung, Reversal sofort erlaubt
            if coin in recent_liqs:
                liq_dir = recent_liqs[coin]["direction"]
                if direction == liq_dir:
                    log(f"  COOLDOWN: {coin} {direction} — gleiche Richtung wie LIQ vor <30min. Uebersprungen.")
                    continue

            if probability >= CONFIG["min_probability"]:
                signals_found += 1
                log(f"  SIGNAL: {coin} {direction} | Prob: {probability}% | Bias: {result['coin_bias']} | BTC: {result['btc_trend']}")
                log(f"          Scores: {result['scores']} | L:{result['long_count']} S:{result['short_count']}")

                # Check 15m max open limit again (could have filled during scan)
                if tf_key == "trades_15m":
                    current_open = len([t for t in data[tf_key] if t["status"] == "open"])
                    if current_open >= CONFIG["max_open_15m"]:
                        log(f"  Max open 15m trades reached. Stopping scan.")
                        break

                # Check 4h max open limit again
                if tf_key == "trades_4h":
                    current_open = len([t for t in data[tf_key] if t["status"] == "open"])
                    if current_open >= CONFIG["max_open_4h"]:
                        log(f"  Max open 4h trades reached. Stopping scan.")
                        break

                open_trade(data, tf_key, coin, direction, entry, tp, probability, tf)
                trades_opened += 1

        except Exception as e:
            log(f"  ERROR analyzing {coin}: {e}")
            continue

        time.sleep(0.1)  # Rate limit between coins

    log(f"\nScan complete: {signals_found} signals found, {trades_opened} trades opened")
    open_count = len([t for t in data[tf_key] if t["status"] == "open"])
    log(f"Open {tf} trades: {open_count}")


# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

_LOG_FILE = "/tmp/paper_bot_v2k2.log"

def log(msg):
    """Print timestamped log message to stdout and file."""
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    line = f"[{ts}] [V2] {msg}"
    print(line, flush=True)
    try:
        with open(_LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except:
        pass


# ═══════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════

def print_status(data):
    """Print current status summary."""
    for tf_key, label in [("trades_15m", "15m"), ("trades_30m", "30m"), ("trades_1h", "1h"), ("trades_4h", "4h")]:
        open_trades = [t for t in data[tf_key] if t["status"] == "open"]
        closed = [t for t in data[tf_key] if t["status"] == "closed"]
        total_pnl = sum(t["pnl"] for t in closed if t["pnl"])
        wins = len([t for t in closed if t["pnl"] and t["pnl"] > 0])
        losses = len([t for t in closed if t["pnl"] and t["pnl"] <= 0])
        wr = round(wins / len(closed) * 100, 1) if closed else 0

        log(f"  [{label}] Open: {len(open_trades)} | Closed: {len(closed)} | W/L: {wins}/{losses} ({wr}%) | PnL: ${total_pnl:.2f}")


def main():
    log("Paper Trading Bot V2 starting...")
    log(f"Config: Capital=${CONFIG['capital']}, Leverage={CONFIG['leverage']}x, Min Prob={CONFIG['min_probability']}%")
    log(f"Coins: {len(COINS)} | Data file: {DATA_FILE}")
    log(f"TP range: {CONFIG['tp_range_pct']}% of expected move")
    log("")

    data = load_data()

    # Track last scan times to avoid duplicate scans
    last_15m_scan = -1
    last_30m_scan = -1
    last_1h_scan = -1
    last_4h_scan = -1

    while True:
        try:
            # Zeitpunkt ZUERST merken, bevor check_open_trades die Minute verbraucht
            now = datetime.now(TZ)
            minute = now.minute
            hour = now.hour

            # Scans ZUERST ausführen (zeitkritisch, verpassen sonst den Slot)
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

            # 1h scan: run at minute 0
            if minute == 0 and hour != last_1h_scan:
                last_1h_scan = hour
                scan_and_trade(data, "1h", 500, "trades_1h")
                update_stats(data)
                save_data(data)
                print_status(data)

            # 4h scan: run at hours 0,4,8,12,16,20 minute 0
            if minute == 0 and hour in (0, 4, 8, 12, 16, 20) and hour != last_4h_scan:
                last_4h_scan = hour
                scan_and_trade(data, "4h", 500, "trades_4h")
                update_stats(data)
                save_data(data)
                print_status(data)

            # Check open trades for TP/Liq (nach Scans, da zeitintensiv ~30s)
            price_cache = check_open_trades(data)

            # Dynamischer Risk Manager — nutzt price_cache, keine extra API-Calls
            manage_open_risk(data, price_cache)

            # Save after all checks
            update_stats(data)
            save_data(data)

            # Auto-push to GitHub every 5 minutes
            if minute % 5 == 0:
                try:
                    import subprocess
                    subprocess.run(["git", "add", "paper_trades_v2k2.json"], cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
                    subprocess.run(["git", "commit", "-m", "Paper bot V2 data update"], cwd=os.path.dirname(DATA_FILE), capture_output=True, timeout=10)
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
