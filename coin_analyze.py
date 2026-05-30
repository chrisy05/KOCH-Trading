#!/usr/bin/env python3
"""
Coin Liquidity Analyzer — Backend fuer Dashboard
Kombiniert: AlgoAlpha Profile + Orderbook Depth + OI/Funding + BTC-Kontext
Ergebnis: JSON mit Analyse + 3-teiligem Resümee (BTC long/seitwärts/short)
"""

import json
import ssl
import math
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=-4))
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def api(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CoinAnalyzer/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode())
    except:
        return None


def fmt(v):
    if abs(v) >= 1e9: return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
    if abs(v) >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def fetch_klines(symbol, interval="15m", limit=800):
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
        if bins[i]["absorption"] > bins[i-1]["absorption"] and bins[i]["absorption"] > bins[i+1]["absorption"] and bins[i]["absorption"] > 0:
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


def get_depth(symbol, limit=50):
    url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit={limit}"
    data = api(url)
    if not data:
        return None
    asks = [(float(p), float(q)) for p, q in data["asks"]]
    bids = [(float(p), float(q)) for p, q in data["bids"]]
    return {"asks": asks, "bids": bids}


def get_oi_funding(symbol):
    oi = api(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}")
    fr = api(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=3")
    result = {}
    if oi:
        result["oi"] = float(oi["openInterest"])
    if fr and len(fr) > 0:
        result["funding"] = float(fr[-1]["fundingRate"])
        result["funding_pct"] = result["funding"] * 100
    return result


def analyze_depth(depth, price):
    if not depth:
        return {}
    asks, bids = depth["asks"], depth["bids"]

    # Walls = orders > 3x median
    ask_qtys = [q for _, q in asks]
    bid_qtys = [q for _, q in bids]
    med_ask = sorted(ask_qtys)[len(ask_qtys)//2] if ask_qtys else 1
    med_bid = sorted(bid_qtys)[len(bid_qtys)//2] if bid_qtys else 1

    ask_walls = [{"price": p, "qty": q, "dist": (p-price)/price*100}
                 for p, q in asks if q > med_ask * 3]
    bid_walls = [{"price": p, "qty": q, "dist": (price-p)/price*100}
                 for p, q in bids if q > med_bid * 3]

    # Cumulative imbalance
    cum_ask = sum(q for _, q in asks[:25])
    cum_bid = sum(q for _, q in bids[:25])
    ratio = cum_bid / cum_ask if cum_ask > 0 else 1

    # Thin zones (vacuum)
    ask_thin = []
    for i in range(1, len(asks)):
        gap = asks[i][0] - asks[i-1][0]
        if gap > (asks[-1][0] - asks[0][0]) / len(asks) * 3:
            ask_thin.append({"from": asks[i-1][0], "to": asks[i][0]})
    bid_thin = []
    for i in range(1, len(bids)):
        gap = bids[i-1][0] - bids[i][0]
        if gap > (bids[0][0] - bids[-1][0]) / len(bids) * 3:
            bid_thin.append({"from": bids[i][0], "to": bids[i-1][0]})

    return {
        "bid_ask_ratio": ratio,
        "cum_bids": cum_bid, "cum_asks": cum_ask,
        "ask_walls": ask_walls[:5], "bid_walls": bid_walls[:5],
        "ask_thin": ask_thin[:3], "bid_thin": bid_thin[:3],
        "ob_bias": "BULLISH" if ratio > 1.2 else ("BEARISH" if ratio < 0.8 else "NEUTRAL"),
    }


def analyze_btc():
    """Quick BTC context: trend direction + key levels"""
    klines = fetch_klines("BTCUSDT", "1h", 50)
    if not klines:
        return {"trend": "UNKNOWN"}

    closes = [k["close"] for k in klines]
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma20
    price = closes[-1]

    if price > sma20 > sma50: trend = "BULLISH"
    elif price < sma20 < sma50: trend = "BEARISH"
    else: trend = "SIDEWAYS"

    oi_data = get_oi_funding("BTCUSDT")

    return {
        "price": price, "sma20": sma20, "sma50": sma50,
        "trend": trend,
        "funding": oi_data.get("funding_pct", 0),
    }


def full_analysis(coin):
    sym = f"{coin.upper()}USDT"
    dec = lambda p: 5 if p < 0.01 else (4 if p < 1 else (2 if p < 100 else 0))

    # Parallel data fetch
    klines = fetch_klines(sym, "15m", 800)
    if not klines:
        return {"error": f"Keine Daten fuer {sym}"}

    profile = build_profile(klines)
    depth = get_depth(sym)
    oi_fund = get_oi_funding(sym)
    depth_analysis = analyze_depth(depth, profile["price"])
    btc = analyze_btc()

    p = profile["price"]
    d = dec(p)

    # Liqi estimation from recent highs/lows
    recent_high = max(k["high"] for k in klines[-100:])
    recent_low = min(k["low"] for k in klines[-100:])

    # Distance calculations
    dist_up = (recent_high - p) / p * 100
    dist_down = (p - recent_low) / p * 100

    # Resistance on the way
    support_zones = [z for z in profile["zones"] if z["type"] == "SUPPORT"]
    resist_zones = [z for z in profile["zones"] if z["type"] == "RESISTANCE"]

    # Score components
    scores = {}

    # 1. Delta bias
    delta_ratio = abs(profile["delta"]) / max(profile["total_buy"] + profile["total_sell"], 1) * 100
    scores["delta"] = {"value": profile["bias"], "strength": f"{delta_ratio:.1f}%",
                       "favor": "LONG" if profile["bias"] == "BULLISH" else "SHORT"}

    # 2. OB imbalance
    scores["orderbook"] = {"value": depth_analysis.get("ob_bias", "?"),
                           "ratio": f"{depth_analysis.get('bid_ask_ratio', 1):.2f}",
                           "favor": "LONG" if depth_analysis.get("ob_bias") == "BULLISH" else
                                    ("SHORT" if depth_analysis.get("ob_bias") == "BEARISH" else "NEUTRAL")}

    # 3. Funding
    fund = oi_fund.get("funding_pct", 0)
    scores["funding"] = {"value": f"{fund:.4f}%",
                         "favor": "SHORT" if fund > 0.005 else ("LONG" if fund < -0.005 else "NEUTRAL"),
                         "meaning": "Longs zahlen→Short-heavy" if fund > 0 else "Shorts zahlen→Long-heavy"}

    # 4. Distance
    scores["distance"] = {
        "to_high": f"{dist_up:.1f}%", "to_low": f"{dist_down:.1f}%",
        "favor": "SHORT" if dist_down < dist_up else "LONG",
        "nearest": "UNTEN" if dist_down < dist_up else "OBEN",
    }

    # 5. Walls/Resistance
    walls_up = len(resist_zones)
    walls_down = len(support_zones)
    scores["walls"] = {
        "up": walls_up, "down": walls_down,
        "favor": "SHORT" if walls_up < walls_down else ("LONG" if walls_down < walls_up else "NEUTRAL"),
    }

    # POC position
    poc_dist = (profile["poc"] - p) / p * 100
    scores["poc"] = {
        "price": f"${profile['poc']:.{d}f}",
        "dist": f"{poc_dist:+.1f}%",
        "favor": "SHORT" if poc_dist < -1 else ("LONG" if poc_dist > 1 else "NEUTRAL"),
    }

    # VA position
    in_va = profile["va_low"] <= p <= profile["va_high"]
    above_va = p > profile["va_high"]
    below_va = p < profile["va_low"]
    scores["va"] = {
        "range": f"${profile['va_low']:.{d}f} - ${profile['va_high']:.{d}f}",
        "position": "DARIN" if in_va else ("DARUEBER" if above_va else "DARUNTER"),
        "favor": "LONG" if above_va else ("SHORT" if below_va else "NEUTRAL"),
    }

    # Overall bias (without BTC)
    long_count = sum(1 for s in scores.values() if s.get("favor") == "LONG")
    short_count = sum(1 for s in scores.values() if s.get("favor") == "SHORT")
    neutral_count = sum(1 for s in scores.values() if s.get("favor") == "NEUTRAL")

    if long_count > short_count + 1:
        coin_bias = "LONG"
    elif short_count > long_count + 1:
        coin_bias = "SHORT"
    else:
        coin_bias = "NEUTRAL"

    # 3-part BTC conclusion
    def build_resume(btc_scenario):
        if coin_bias == "LONG":
            if btc_scenario == "LONG":
                return "LONG — Coin bullish + BTC Rueckenwind. Optimales Setup. Entry an Support/Absorption, TP am Liqi-Cluster oben."
            elif btc_scenario == "SIDEWAYS":
                return "LONG moeglich — Coin bullish, BTC neutral. Funktioniert, aber mit kleinerem Size. Engerer SL."
            else:
                return "ABWARTEN — Coin zwar bullish, aber BTC bearish ueberwiegt. Erst Boden bei BTC abwarten."
        elif coin_bias == "SHORT":
            if btc_scenario == "LONG":
                return "ABWARTEN — Coin bearish, aber BTC steigt. Gegenwind zu stark. Kein Entry."
            elif btc_scenario == "SIDEWAYS":
                return "SHORT moeglich — Coin bearish, BTC neutral. Setup ok mit normalem Size."
            else:
                return "SHORT — Coin bearish + BTC faellt. Doppelte Bestaetigung. Aggressiver Entry moeglich."
        else:
            if btc_scenario == "LONG":
                return "LONG tendenz — Coin neutral, BTC gibt die Richtung. Kleiner Size, weiter SL."
            elif btc_scenario == "SIDEWAYS":
                return "KEIN TRADE — Coin neutral + BTC seitwaerts. Kein Edge. Warten auf klares Signal."
            else:
                return "SHORT tendenz — Coin neutral, BTC baerisch drueckt. Kleiner Size, konservativ."

    resume = {
        "A_btc_long": build_resume("LONG"),
        "B_btc_sideways": build_resume("SIDEWAYS"),
        "C_btc_short": build_resume("SHORT"),
    }

    # Format output
    output = {
        "coin": coin.upper(),
        "timestamp": datetime.now(TZ).strftime("%d.%m.%Y %H:%M ET"),
        "price": f"${p:.{d}f}",
        "price_raw": p,

        "profile": {
            "poc": f"${profile['poc']:.{d}f}",
            "va": f"${profile['va_low']:.{d}f} - ${profile['va_high']:.{d}f}",
            "delta": fmt(profile["delta"]),
            "bias": profile["bias"],
            "delta_pct": f"{delta_ratio:.1f}%",
        },

        "orderbook": {
            "bias": depth_analysis.get("ob_bias", "?"),
            "ratio": f"{depth_analysis.get('bid_ask_ratio', 1):.2f}",
            "ask_walls": len(depth_analysis.get("ask_walls", [])),
            "bid_walls": len(depth_analysis.get("bid_walls", [])),
            "thin_up": len(depth_analysis.get("ask_thin", [])),
            "thin_down": len(depth_analysis.get("bid_thin", [])),
        },

        "oi_funding": {
            "oi": fmt(oi_fund.get("oi", 0) * p) if oi_fund.get("oi") else "?",
            "oi_raw": oi_fund.get("oi", 0),
            "funding": f"{fund:.4f}%",
            "funding_bias": scores["funding"]["meaning"],
        },

        "liquidity": {
            "recent_high": f"${recent_high:.{d}f}",
            "recent_low": f"${recent_low:.{d}f}",
            "dist_up": f"{dist_up:.1f}%",
            "dist_down": f"{dist_down:.1f}%",
            "nearest": scores["distance"]["nearest"],
            "support_zones": [{"price": f"${z['mid']:.{d}f}", "dist": f"{(p-z['mid'])/p*100:.1f}%",
                               "strength": fmt(z["abs"]), "delta": "BUY" if z["delta"]>0 else "SELL"}
                              for z in support_zones[:4]],
            "resist_zones": [{"price": f"${z['mid']:.{d}f}", "dist": f"{(z['mid']-p)/p*100:.1f}%",
                              "strength": fmt(z["abs"]), "delta": "BUY" if z["delta"]>0 else "SELL"}
                             for z in resist_zones[:4]],
        },

        "scores": {
            "delta": scores["delta"]["favor"],
            "orderbook": scores["orderbook"]["favor"],
            "funding": scores["funding"]["favor"],
            "distance": scores["distance"]["favor"],
            "walls": scores["walls"]["favor"],
            "poc": scores["poc"]["favor"],
            "va": scores["va"]["favor"],
            "long_count": long_count,
            "short_count": short_count,
            "neutral_count": neutral_count,
        },

        "coin_bias": coin_bias,

        "btc": {
            "price": f"${btc['price']:.0f}" if btc.get("price") else "?",
            "trend": btc.get("trend", "?"),
            "sma20": f"${btc.get('sma20', 0):.0f}",
            "sma50": f"${btc.get('sma50', 0):.0f}",
            "funding": f"{btc.get('funding', 0):.4f}%",
        },

        "resume": resume,
    }

    return output


if __name__ == "__main__":
    coin = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    result = full_analysis(coin)
    print(json.dumps(result, indent=2, ensure_ascii=False))
