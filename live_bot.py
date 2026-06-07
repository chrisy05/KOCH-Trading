#!/usr/bin/env python3
"""
Live Trading Bot — Bybit V5 API
Exact same strategy as Paper Trading Bot, but with real orders on Bybit.

Usage:
    python3 live_bot.py              # Dry-run mode (logs what it WOULD do)
    python3 live_bot.py --live       # LIVE mode (places real orders!)
"""

import json
import ssl
import math
import time
import os
import sys
import traceback
import hmac
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

CONFIG = {
    "capital": 100,
    "leverage": 10,
    "min_probability": 60,
    "tp_range_pct": 80,       # 80% of expected move
    "sl_pct": 70,             # SL bei 70% Verlust der Margin (0 = aus, nur Liq)
    "max_open_15m": 50,
    "max_trades_per_coin_1h": 1,  # per day
    "max_open_4h": 3,
    "total_budget": 10000,    # Virtuelles Gesamtkapital
    "tf_budget_15m": 50,      # % of total budget for 15m trades
    "tf_budget_30m": 30,      # % of total budget for 30m trades
    "tf_budget_1h": 20,       # % of total budget for 1h trades
    "tf_budget_4h": 0,        # % of total budget for 4h trades (0 = disabled)
}

# Load overrides from config file (written by bot_server.py)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_bot_config.json")
STATUS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_bot_status.json")

def load_config_overrides():
    """Load config overrides from live_bot_config.json if it exists."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                overrides = json.load(f)
            for key in ("capital", "leverage", "min_probability", "tp_range_pct", "sl_pct", "max_open_4h",
                        "total_budget", "tf_budget_15m", "tf_budget_30m", "tf_budget_1h", "tf_budget_4h"):
                if key in overrides:
                    CONFIG[key] = overrides[key]
        except Exception:
            pass

def write_bot_status():
    """Write current bot status to live_bot_status.json for the dashboard."""
    try:
        balance_str = None
        api_ok = False
        if API_KEY and API_KEY != "YOUR_API_KEY_HERE":
            bal = get_wallet_balance()
            if bal:
                balance_str = str(round(bal.get("wallet_balance", 0), 8))
                api_ok = True

        status = {
            "running": True,
            "mode": "LIVE" if LIVE_MODE else "DRY-RUN",
            "pid": os.getpid(),
            "balance": balance_str or "0",
            "api_ok": api_ok,
            "config": {k: CONFIG[k] for k in ("capital", "leverage", "min_probability", "tp_range_pct", "max_open_4h")},
            "start_time": getattr(write_bot_status, '_start_time', datetime.now(TZ).isoformat()),
            "last_scan": datetime.now(TZ).isoformat(),
            "last_check": datetime.now(TZ).isoformat(),
        }
        with open(STATUS_FILE_PATH, "w") as f:
            json.dump(status, f, indent=2)
    except Exception:
        pass

load_config_overrides()

# ═══════════════════════════════════════════════════════════════
# MODE FLAG
# ═══════════════════════════════════════════════════════════════

LIVE_MODE = "--live" in sys.argv

# ═══════════════════════════════════════════════════════════════
# API CREDENTIALS
# ═══════════════════════════════════════════════════════════════

CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bybit_credentials.json")
API_KEY = ""
API_SECRET = ""

def load_credentials():
    global API_KEY, API_SECRET
    if not os.path.exists(CREDENTIALS_FILE):
        log("ERROR: bybit_credentials.json not found!")
        if LIVE_MODE:
            log("Cannot run in LIVE mode without credentials. Exiting.")
            sys.exit(1)
        return
    try:
        with open(CREDENTIALS_FILE, "r") as f:
            creds = json.load(f)
        API_KEY = creds.get("api_key", "")
        API_SECRET = creds.get("api_secret", "")
        if LIVE_MODE and (not API_KEY or API_KEY == "YOUR_API_KEY_HERE"):
            log("ERROR: API credentials not configured! Edit bybit_credentials.json")
            sys.exit(1)
        log(f"Credentials loaded. API key: {API_KEY[:8]}...") if API_KEY and API_KEY != "YOUR_API_KEY_HERE" else None
    except Exception as e:
        log(f"ERROR loading credentials: {e}")
        if LIVE_MODE:
            sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# COINS — Bybit-compatible list (removed ONE, PEPE, BGB)
# ═══════════════════════════════════════════════════════════════

# Mapping: analysis coin name -> Bybit symbol suffix
# Most coins: COINUSDT. Special cases mapped here.
BYBIT_SYMBOL_MAP = {
    "BANANAS31": "BANANAS31USDT",
}

COINS = [
    "TON", "SOL", "BCH", "LINK", "DOGE",
    "FIDA", "NEO", "DYDX", "ADA", "HYPE", "FIL", "ICP", "LTC",
    "JASMY", "EIGEN", "IP", "OP", "SEI", "ONE", "IMX", "AVAX",
    "GLM", "CFX", "BNT", "CRV", "TRX", "SUI",
    "JUP", "TAO", "HBAR", "OGN", "LPT", "ETH", "THETA",
    "APT", "DOT", "XRP", "WIF", "PEPE", "BNB", "RUNE",
    "XTZ", "METIS", "AAVE", "UNI", "BGB"]

# Bybit quantity precision per symbol (decimals for qty rounding)
# Default is 0 (whole numbers). Override for specific coins.
BYBIT_QTY_DECIMALS = {
    "BTCUSDT": 3, "ETHUSDT": 2, "SOLUSDT": 1, "BCHUSDT": 2,
    "LTCUSDT": 2, "BNBUSDT": 2, "XMRUSDT": 2, "AAVEUSDT": 2,
    "TAOUSDT": 3, "ICPUSDT": 1, "AVAXUSDT": 1, "APTUSDT": 1,
    "DOTUSDT": 1, "FILUSDT": 1, "NEOUSDT": 1, "THETAUSDT": 1,
    "LINKUSDT": 1, "UNIUSDT": 1, "INJUSDT": 1, "SUIUSDT": 0,
    "TONUSDT": 1, "RUNEUSDT": 0, "SEIUSDT": 0, "OPUSDT": 1,
    "IMXUSDT": 0, "JUPUSDT": 0, "METISUSDT": 2,
    # Most small-cap alts: 0 decimals (whole numbers)
}

TZ = timezone(timedelta(hours=-4))
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_trades.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_bot.log")

# SSL context for Binance API (analysis data)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ═══════════════════════════════════════════════════════════════
# BYBIT API HELPERS
# ═══════════════════════════════════════════════════════════════

BYBIT_BASE = "https://api.bybit.com"

def bybit_sign(params_str, timestamp, recv_window="5000"):
    """Create HMAC SHA256 signature for Bybit V5 API."""
    param_str = str(timestamp) + API_KEY + recv_window + params_str
    signature = hmac.new(API_SECRET.encode(), param_str.encode(), hashlib.sha256).hexdigest()
    return signature


def bybit_request(method, endpoint, params=None):
    """
    Authenticated Bybit V5 API request.
    method: "GET" or "POST"
    endpoint: e.g. "/v5/order/create"
    params: dict (for POST: JSON body, for GET: query params)
    Returns: parsed JSON response or None on error
    """
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"

    if method == "GET":
        params_str = urllib.parse.urlencode(params) if params else ""
        url = f"{BYBIT_BASE}{endpoint}"
        if params_str:
            url += f"?{params_str}"
        body = None
    else:  # POST
        params_str = json.dumps(params) if params else ""
        url = f"{BYBIT_BASE}{endpoint}"
        body = params_str.encode() if params_str else None

    signature = bybit_sign(params_str, timestamp, recv_window)

    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json",
    }

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())

        if data.get("retCode") != 0:
            log(f"  BYBIT ERROR: {endpoint} -> {data.get('retCode')}: {data.get('retMsg')}")
            return data  # Still return so caller can inspect
        return data
    except Exception as e:
        log(f"  BYBIT REQUEST ERROR: {method} {endpoint} -> {e}")
        return None


def get_bybit_symbol(coin):
    """Get Bybit futures symbol for a coin."""
    if coin in BYBIT_SYMBOL_MAP:
        return BYBIT_SYMBOL_MAP[coin]
    return f"{coin}USDT"


def set_leverage_and_margin(symbol, leverage):
    """Set isolated margin mode and leverage for a symbol on Bybit."""
    # First try to set isolated margin
    try:
        result = bybit_request("POST", "/v5/position/switch-isolated", {
            "category": "linear",
            "symbol": symbol,
            "tradeMode": 1,  # 1 = isolated
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        })
        if result and result.get("retCode") == 0:
            log(f"  Set isolated margin for {symbol}")
        elif result and result.get("retCode") == 110026:
            pass  # Already in isolated mode
        # Some errors (110026) mean already set — that's fine
    except Exception as e:
        log(f"  Warning: switch-isolated for {symbol}: {e}")

    # Then set leverage
    try:
        result = bybit_request("POST", "/v5/position/set-leverage", {
            "category": "linear",
            "symbol": symbol,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        })
        if result and result.get("retCode") == 0:
            log(f"  Set leverage {leverage}x for {symbol}")
        elif result and result.get("retCode") == 110043:
            pass  # Leverage not modified — already set
    except Exception as e:
        log(f"  Warning: set-leverage for {symbol}: {e}")


def place_market_order(symbol, side, qty):
    """
    Place a market order on Bybit.
    side: "Buy" or "Sell"
    qty: string, quantity in base currency
    Returns: order response or None
    """
    params = {
        "category": "linear",
        "symbol": symbol,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "positionIdx": 0,  # one-way mode
        "timeInForce": "GTC",
    }
    result = bybit_request("POST", "/v5/order/create", params)
    if result and result.get("retCode") == 0:
        order_id = result.get("result", {}).get("orderId", "unknown")
        log(f"  ORDER PLACED: {side} {qty} {symbol} | OrderID: {order_id}")
        return result
    else:
        log(f"  ORDER FAILED: {side} {qty} {symbol} | Response: {result}")
        return None


def set_tp_sl(symbol, tp_price=None, sl_price=None, position_idx=0):
    """Set TP and/or SL on an existing position in a SINGLE call.
    Bybit tpslMode=Full overwrites on each call, so both must be set together."""
    params = {
        "category": "linear",
        "symbol": symbol,
        "positionIdx": position_idx,
        "tpslMode": "Full",
    }
    if tp_price is not None:
        params["takeProfit"] = str(tp_price)
        params["tpTriggerBy"] = "LastPrice"
    if sl_price is not None:
        params["stopLoss"] = str(sl_price)
        params["slTriggerBy"] = "LastPrice"
    result = bybit_request("POST", "/v5/position/trading-stop", params)
    if result and result.get("retCode") == 0:
        parts = []
        if tp_price is not None:
            parts.append(f"TP={tp_price}")
        if sl_price is not None:
            parts.append(f"SL={sl_price}")
        log(f"  TP/SL SET: {symbol} @ {', '.join(parts)}")
        return result
    else:
        log(f"  TP/SL FAILED: {symbol} | Response: {result}")
        return None


def set_take_profit(symbol, tp_price, position_idx=0):
    """Set take profit on an existing position."""
    return set_tp_sl(symbol, tp_price=tp_price, position_idx=position_idx)


def set_stop_loss(symbol, sl_price, position_idx=0):
    """Set stop loss on an existing position."""
    return set_tp_sl(symbol, sl_price=sl_price, position_idx=position_idx)


def get_bybit_close_data(symbol):
    """Fetch the most recent close data for a symbol from Bybit closed-pnl."""
    try:
        result = bybit_request("GET", "/v5/position/closed-pnl", {
            "category": "linear",
            "symbol": symbol,
            "limit": "1",
        })
        if result and result.get("retCode") == 0:
            records = result.get("result", {}).get("list", [])
            if records:
                r = records[0]
                return {
                    "exit_price": float(r.get("avgExitPrice", "0")),
                    "pnl": float(r.get("closedPnl", "0")),
                    "exec_type": r.get("execType", "Trade"),
                    "order_type": r.get("orderType", ""),
                }
    except Exception as e:
        log(f"  Warning: Could not fetch closed-pnl for {symbol}: {e}")
    return None


def get_positions():
    """Get all open positions from Bybit."""
    result = bybit_request("GET", "/v5/position/list", {
        "category": "linear",
        "settleCoin": "USDT",
    })
    if result and result.get("retCode") == 0:
        positions = result.get("result", {}).get("list", [])
        # Filter to positions with actual size > 0
        return [p for p in positions if float(p.get("size", "0")) > 0]
    return []


def get_wallet_balance():
    """Get USDT wallet balance from Bybit."""
    result = bybit_request("GET", "/v5/account/wallet-balance", {
        "accountType": "UNIFIED",
    })
    if result and result.get("retCode") == 0:
        coins = result.get("result", {}).get("list", [{}])[0].get("coin", [])
        for c in coins:
            if c.get("coin") == "USDT":
                def safe_float(v):
                    try: return float(v) if v else 0.0
                    except: return 0.0
                return {
                    "available": safe_float(c.get("availableToWithdraw", "0")),
                    "equity": safe_float(c.get("equity", "0")),
                    "wallet_balance": safe_float(c.get("walletBalance", "0")),
                }
    return None


def close_position_market(symbol, side, qty):
    """Close a position with a market order. side should be opposite of position side."""
    return place_market_order(symbol, side, qty)


def round_qty(symbol, qty):
    """Round quantity to Bybit's accepted precision for a symbol."""
    decimals = BYBIT_QTY_DECIMALS.get(symbol, 0)
    if decimals == 0:
        return str(int(qty))
    return str(round(qty, decimals))


def round_price(price, tick_decimals=None):
    """Round price to reasonable precision."""
    if price >= 1000:
        return round(price, 2)
    elif price >= 1:
        return round(price, 4)
    elif price >= 0.01:
        return round(price, 6)
    else:
        return round(price, 8)


# ═══════════════════════════════════════════════════════════════
# BINANCE API HELPERS (for analysis data — same as paper bot)
# ═══════════════════════════════════════════════════════════════

def api(url, timeout=10):
    """Fetch JSON from URL with error handling."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LiveBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def fetch_klines(symbol, interval="15m", limit=800):
    """Fetch klines from Binance Futures (for analysis)."""
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
    """Fetch orderbook depth from Binance."""
    url = f"https://fapi.binance.com/fapi/v1/depth?symbol={symbol}&limit={limit}"
    data = api(url)
    if not data:
        return None
    asks = [(float(p), float(q)) for p, q in data["asks"]]
    bids = [(float(p), float(q)) for p, q in data["bids"]]
    return {"asks": asks, "bids": bids}


def get_oi_funding(symbol):
    """Fetch OI and funding rate from Binance."""
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
    """Fetch current mark price from Binance."""
    url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}"
    data = api(url)
    if data:
        return float(data["price"])
    return None


# ═══════════════════════════════════════════════════════════════
# ANALYSIS (exact copy from paper_bot.py)
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
    """Load live trades from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
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
            "min_prob": CONFIG["min_probability"],
            "tp_pct": CONFIG["tp_range_pct"],
            "start_date": datetime.now(TZ).strftime("%Y-%m-%d"),
            "mode": "LIVE" if LIVE_MODE else "DRY-RUN",
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
    """Save live trades to JSON file."""
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
    """Calculate liquidation price."""
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


## ═══════════════════════════════════════════════════════════════
## V2K3 REGIME + MTF LOGIC
## ═══════════════════════════════════════════════════════════════

def get_btc_sma_data():
    """Holt BTC 1H Klines und berechnet SMAs + Slopes."""
    try:
        url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=105"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            klines = json.loads(r.read())
        if len(klines) < 105:
            return None
        closes = [float(k[4]) for k in klines]
        sma10 = sum(closes[-10:]) / 10
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50
        sma100 = sum(closes[-100:]) / 100
        sma10_prev = sum(closes[-15:-5]) / 10
        sma20_prev = sum(closes[-25:-5]) / 20
        sma50_prev = sum(closes[-55:-5]) / 50
        price = closes[-1]
        slope10 = (sma10 - sma10_prev) / price * 100
        slope20 = (sma20 - sma20_prev) / price * 100
        slope50 = (sma50 - sma50_prev) / price * 100
        gap_10_20 = abs(sma10 - sma20) / price * 100
        gap_20_50 = abs(sma20 - sma50) / price * 100
        slope_diff_all = abs(slope10 - slope20) + abs(slope20 - slope50)
        return {
            "price": price, "sma10": sma10, "sma20": sma20, "sma50": sma50, "sma100": sma100,
            "slope10": slope10, "slope20": slope20, "slope50": slope50,
            "gap_10_20": gap_10_20, "gap_20_50": gap_20_50, "slope_diff_all": slope_diff_all,
        }
    except:
        return None

_btc_sma_cache = {"data": None, "ts": 0}
def get_btc_sma_cached():
    now = time.time()
    if _btc_sma_cache["data"] and now - _btc_sma_cache["ts"] < 60:
        return _btc_sma_cache["data"]
    data = get_btc_sma_data()
    if data:
        _btc_sma_cache["data"] = data
        _btc_sma_cache["ts"] = now
    return data

def detect_btc_regime(btc_data):
    """Erkennt TRENDING / SIDEWAYS / TRANSITIONING."""
    if not btc_data:
        return "UNKNOWN", 0, "Keine Daten"
    slope10, slope20, slope50 = btc_data["slope10"], btc_data["slope20"], btc_data["slope50"]
    gap_10_20, gap_20_50 = btc_data["gap_10_20"], btc_data["gap_20_50"]
    slope_diff = btc_data["slope_diff_all"]
    price, sma10, sma20, sma50 = btc_data["price"], btc_data["sma10"], btc_data["sma20"], btc_data["sma50"]
    dist_to_sma10 = abs(price - sma10) / price * 100
    dist_to_sma20 = abs(price - sma20) / price * 100

    signals_trending = 0
    slopes_same_dir = (slope10 > 0 and slope20 > 0 and slope50 > 0) or (slope10 < 0 and slope20 < 0 and slope50 < 0)
    if slopes_same_dir and (abs(slope10) > 0.15 or abs(slope20) > 0.10): signals_trending += 2
    if dist_to_sma10 > 0.5 and dist_to_sma20 > 0.8: signals_trending += 2
    if gap_10_20 > 0.3: signals_trending += 1
    if gap_20_50 > 1.0: signals_trending += 1
    if slope_diff > 0.3: signals_trending += 1

    signals_sideways = 0
    sw_details = []
    if abs(slope10) < 0.15 and abs(slope20) < 0.10: signals_sideways += 2; sw_details.append("Slopes flach")
    if slope_diff < 0.15: signals_sideways += 2; sw_details.append("Slopes konvergent")
    if gap_10_20 < 0.3: signals_sideways += 1; sw_details.append("SMA eng")
    if dist_to_sma10 < 0.3 and dist_to_sma20 < 0.5: signals_sideways += 1; sw_details.append("Preis nahe")
    if gap_10_20 < 0.15: signals_sideways += 1; sw_details.append("SMA10/20 gleich")

    if signals_trending >= 4: return "TRENDING", signals_trending, "Trend aktiv"
    elif signals_sideways >= 5: return "SIDEWAYS", signals_sideways, " | ".join(sw_details)
    elif signals_trending >= 3: return "TRENDING", signals_trending, "Trend aktiv"
    elif signals_sideways >= 3: return "TRANSITIONING", signals_sideways, " | ".join(sw_details)
    elif signals_trending > signals_sideways: return "TRENDING", signals_trending, "Leicht trending"
    else: return "TRANSITIONING", signals_sideways, " | ".join(sw_details) if sw_details else "Unklar"

def get_btc_sma_alignment(direction):
    """SMA-Cross Alignment für Hebel."""
    btc = get_btc_sma_data()
    if not btc:
        return CONFIG["leverage"], 0, "Keine Daten"
    c, sma10, sma20, sma50, sma100 = btc["price"], btc["sma10"], btc["sma20"], btc["sma50"], btc["sma100"]

    if direction == "SHORT":
        if c > sma100: return 0, 0, f"SKIP: BTC > SMA100"
        if c > sma50: return 5, 0, f"Übergang: BTC > SMA50"
        crosses = int(sma10 < sma20) + int(sma20 < sma50) + int(sma50 < sma100)
        if crosses == 0: return 0, 0, "SKIP: Kein bärisches Cross"
        elif crosses == 1: return 5, 1, "1 Cross"
        elif crosses == 2: return 7, 2, "2 Crosses"
        else: return (12 if c < sma10 else 10), (4 if c < sma10 else 3), "Voll aligned"
    else:
        if c < sma100:
            if c > sma50: return 5, 0, "Übergang: BTC zwischen SMA50/100"
            return 0, 0, f"SKIP: BTC < SMA50"
        crosses = int(sma10 > sma20) + int(sma20 > sma50) + int(sma50 > sma100)
        if crosses == 0: return 0, 0, "SKIP: Kein bullisches Cross"
        elif crosses == 1: return 5, 1, "1 Cross"
        elif crosses == 2: return 7, 2, "2 Crosses"
        else: return (12 if c > sma10 else 10), (4 if c > sma10 else 3), "Voll aligned"

def check_coin_sma(coin, direction):
    """Coin-eigener SMA-Filter auf 1H."""
    try:
        sym = f"{coin}USDT"
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=50"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            klines = json.loads(r.read())
        if len(klines) < 50: return False, 0, "OK"
        closes = [float(k[4]) for k in klines]
        c, sma20, sma50 = closes[-1], sum(closes[-20:])/20, sum(closes[-50:])/50
        if direction == "SHORT":
            if c > sma50: return True, 0, f"{coin} > SMA50 (bullish)"
            if c > sma20: return False, 3, f"{coin} > SMA20"
            return False, 0, "OK"
        else:
            if c < sma50: return True, 0, f"{coin} < SMA50 (bearish)"
            if c < sma20: return False, 3, f"{coin} < SMA20"
            return False, 0, "OK"
    except:
        return False, 0, "OK"

def check_mtf_alignment(sym, direction):
    """MTF-Alignment: 5m→15m→30m→1h."""
    tfs = [('5m', 50), ('15m', 50), ('30m', 50), ('1h', 50)]
    aligned = 0
    details = []
    for tf_check, limit in tfs:
        try:
            url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={tf_check}&limit={limit}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                klines = json.loads(r.read())
            closes = [float(k[4]) for k in klines]
            if len(closes) < 20: continue
            sma10 = sum(closes[-10:]) / 10
            sma20 = sum(closes[-20:]) / 20
            price = closes[-1]
            if direction == "LONG" and price > sma10 > sma20:
                aligned += 1; details.append(f"{tf_check}✓")
            elif direction == "SHORT" and price < sma10 < sma20:
                aligned += 1; details.append(f"{tf_check}✓")
            else:
                details.append(f"{tf_check}✗"); break
        except:
            details.append(f"{tf_check}?"); continue
    return aligned, len(tfs), " ".join(details)

## ═══════════════════════════════════════════════════════════════

def open_trade(data, tf_key, coin, direction, entry, tp, probability, tf):
    """Open a trade with V2K3 Regime-aware logic."""
    capital = CONFIG["capital"]

    # ═══ V2K3 REGIME + BTC FILTER ═══
    btc_data = get_btc_sma_cached()
    regime, regime_conf, regime_detail = detect_btc_regime(btc_data)

    # BTC 1H Grundrichtung
    btc_bearish = btc_data and btc_data["price"] < btc_data["sma20"] and btc_data["sma10"] < btc_data["sma50"]
    btc_bullish = btc_data and btc_data["price"] > btc_data["sma20"] and btc_data["sma10"] > btc_data["sma50"]
    with_btc = (direction == "SHORT" and btc_bearish) or (direction == "LONG" and btc_bullish)
    against_btc = (direction == "LONG" and btc_bearish) or (direction == "SHORT" and btc_bullish)

    if regime == "SIDEWAYS":
        log(f"  REGIME: SIDEWAYS ({regime_conf}/7) | {regime_detail}")
        if against_btc:
            sym = f"{coin}USDT"
            mtf_a, _, mtf_d = check_mtf_alignment(sym, direction)
            if mtf_a < 4:
                log(f"  SKIP: Gegen BTC + MTF nur {mtf_a}/4")
                return None
            leverage = 5
            log(f"  Gegen BTC erlaubt mit 5x — MTF 4/4")
        else:
            coin_skip, coin_pen, coin_rsn = check_coin_sma(coin, direction)
            if coin_skip:
                log(f"  COIN-SMA: {coin} {direction} skip — {coin_rsn}")
                return None
            sym = f"{coin}USDT"
            mtf_a, _, mtf_d = check_mtf_alignment(sym, direction)
            log(f"  MTF: {mtf_a}/4 | {mtf_d}")
            if mtf_a == 0:
                log(f"  MTF-SKIP: kein TF aligned")
                return None
            # Coin-SMA Alignment auf 1H
            coin_al = 0
            try:
                url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=50"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                    ck = json.loads(r.read())
                cc = [float(k[4]) for k in ck]
                cs10, cs20, cs50 = sum(cc[-10:])/10, sum(cc[-20:])/20, sum(cc[-50:])/50
                cp = cc[-1]
                if direction == "SHORT":
                    coin_al = int(cp<cs10) + int(cs10<cs20) + int(cs20<cs50)
                else:
                    coin_al = int(cp>cs10) + int(cs10>cs20) + int(cs20>cs50)
            except: pass
            if mtf_a >= 4 and coin_al >= 3: leverage = 12 if tf == "15m" else 10
            elif mtf_a >= 3 and coin_al >= 2: leverage = 10
            elif mtf_a >= 2 and coin_al >= 1: leverage = 7
            else: leverage = 5
            if coin_pen > 0: leverage = max(5, leverage - 2)

    elif regime == "TRANSITIONING":
        log(f"  REGIME: TRANSITIONING ({regime_conf}/7)")
        lev, alignment, sma_rsn = get_btc_sma_alignment(direction)
        if lev == 0:
            if against_btc:
                log(f"  SMA-SKIP: {coin} {direction} — {sma_rsn}")
                return None
            sym = f"{coin}USDT"
            mtf_a, _, mtf_d = check_mtf_alignment(sym, direction)
            if mtf_a >= 3:
                leverage = 5
                log(f"  TRANSITION-OVERRIDE: MTF {mtf_a}/4 → 5x")
            else:
                log(f"  SMA-SKIP: {coin} {direction} — {sma_rsn}")
                return None
        else:
            leverage = min(lev, 10) if tf != "15m" else lev
            log(f"  SMA-Alignment: {alignment}/4 | {leverage}x")
        coin_skip, coin_pen, coin_rsn = check_coin_sma(coin, direction)
        if coin_skip:
            log(f"  COIN-SMA: {coin} {direction} skip — {coin_rsn}")
            return None
        if coin_pen > 0 and leverage >= 10:
            leverage = max(7, leverage - 3)

    else:  # TRENDING
        lev, alignment, sma_rsn = get_btc_sma_alignment(direction)
        if lev == 0:
            log(f"  SMA-SKIP: {coin} {direction} — {sma_rsn}")
            return None
        leverage = min(lev, 10) if tf != "15m" else lev
        log(f"  REGIME: TRENDING | {alignment}/4 | {leverage}x | {sma_rsn}")
        coin_skip, coin_pen, coin_rsn = check_coin_sma(coin, direction)
        if coin_skip:
            log(f"  COIN-SMA: {coin} {direction} skip — {coin_rsn}")
            return None
        if coin_pen > 0 and leverage >= 10:
            leverage = max(7, leverage - 3)

    # ═══ END V2K3 ═══

    margin = capital
    size = capital * leverage / entry
    liq = calc_liquidation(entry, direction, margin, size)

    bybit_symbol = get_bybit_symbol(coin)
    side = "Buy" if direction == "LONG" else "Sell"
    qty_str = round_qty(bybit_symbol, size)
    tp_rounded = round_price(tp)

    order_id = None
    bybit_entry_price = entry  # Will be updated from Bybit if live

    if LIVE_MODE:
        # === REAL EXECUTION ===
        log(f"  [LIVE] Executing {direction} {coin} on Bybit...")

        # 1. Set leverage + isolated margin
        set_leverage_and_margin(bybit_symbol, leverage)
        time.sleep(0.3)

        # 2. Place market order
        order_result = place_market_order(bybit_symbol, side, qty_str)
        if not order_result or order_result.get("retCode") != 0:
            log(f"  [LIVE] ORDER FAILED for {coin}. Skipping trade.")
            return None
        order_id = order_result.get("result", {}).get("orderId")
        time.sleep(0.5)

        # 3. Set TP + SL in a SINGLE call (Bybit tpslMode=Full overwrites on separate calls)
        sl_pct = CONFIG.get("sl_pct", 0)
        sl_rounded = None
        if sl_pct > 0 and entry > 0:
            margin_trade = CONFIG["capital"]
            size_trade = margin_trade * leverage / entry
            sl_loss = margin_trade * (sl_pct / 100.0)
            if direction == "LONG":
                sl_price = entry - sl_loss / size_trade
            else:
                sl_price = entry + sl_loss / size_trade
            sl_rounded = round_price(sl_price)

        tpsl_result = set_tp_sl(bybit_symbol, tp_price=str(tp_rounded), sl_price=str(sl_rounded) if sl_rounded else None)
        if not tpsl_result or tpsl_result.get("retCode") != 0:
            log(f"  [LIVE] WARNING: TP/SL not set for {coin}. Manual intervention needed!")

        log(f"  [LIVE] Trade opened successfully: {direction} {coin} | OrderID: {order_id}")
    else:
        # === DRY-RUN ===
        log(f"  [DRY-RUN] Would execute {direction} {coin} | Qty: {qty_str} | TP: {tp_rounded}")
        log(f"  [DRY-RUN] Would set leverage {leverage}x isolated on {bybit_symbol}")
        order_id = f"DRY-{int(time.time())}"

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
        "bybit_symbol": bybit_symbol,
        "bybit_order_id": order_id,
        "bybit_qty": qty_str,
        "mode": "LIVE" if LIVE_MODE else "DRY-RUN",
    }

    data[tf_key].append(trade)
    log(f"  OPENED {direction} {coin} @ {entry:.6f} | TP: {tp:.6f} | Liq: {liq:.6f} | Prob: {probability}% | TF: {tf}")
    return trade


def close_trade(trade, close_price, reason):
    """Close a trade."""
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
        req = urllib.request.Request(url, headers={"User-Agent": "LiveBot/1.0"})
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
    """
    Check all open trades for TP or liquidation hits.
    In LIVE mode: also cross-check with Bybit positions.
    """
    # Get Bybit positions for cross-referencing (LIVE mode only)
    bybit_positions = {}
    if LIVE_MODE:
        try:
            positions = get_positions()
            for p in positions:
                sym = p.get("symbol", "")
                bybit_positions[sym] = {
                    "size": float(p.get("size", "0")),
                    "side": p.get("side", ""),
                    "unrealisedPnl": float(p.get("unrealisedPnl", "0")),
                    "avgPrice": float(p.get("avgPrice", "0")),
                    "takeProfit": p.get("takeProfit", ""),
                }
        except Exception as e:
            log(f"  Warning: Could not fetch Bybit positions: {e}")

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
            time.sleep(0.05)

        for trade in open_trades:
            coin = trade["coin"]
            if coin not in price_data:
                continue
            current_price, recent_high, recent_low = price_data[coin]

            bybit_sym = trade.get("bybit_symbol", f"{coin}USDT")

            # In LIVE mode: check if position still exists on Bybit
            if LIVE_MODE and trade.get("mode") == "LIVE":
                if bybit_sym not in bybit_positions:
                    # Position gone — fetch REAL close data from Bybit
                    real_close = get_bybit_close_data(bybit_sym)
                    if real_close:
                        close_price = real_close["exit_price"]
                        bybit_pnl = real_close["pnl"]
                        exec_type = real_close["exec_type"]
                        entry = trade["entry"]
                        direction = trade["direction"]

                        if exec_type == "BustTrade":
                            reason = "LIQ"
                        elif direction == "LONG":
                            if close_price >= trade["tp"] * 0.998:
                                reason = "TP"
                            elif close_price <= entry:
                                reason = "SL"
                            else:
                                reason = "TP"
                        else:  # SHORT
                            if close_price <= trade["tp"] * 1.002:
                                reason = "TP"
                            elif close_price >= entry:
                                reason = "SL"
                            else:
                                reason = "TP"

                        close_trade(trade, close_price, reason)
                        log(f"  [LIVE] Position {coin} closed on Bybit | {reason} @ {close_price:.6f} | Bybit PnL: ${bybit_pnl:.2f} ({exec_type})")
                    else:
                        # API nicht erreichbar — Trade offen lassen, nächsten Scan abwarten
                        log(f"  [LIVE] Position {coin} gone but Bybit closed-pnl unavailable. Retrying next scan.")
                    continue

            # SL check
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

            # Standard price-based checks (same as paper bot)
            if trade["direction"] == "LONG":
                if recent_high >= trade["tp"]:
                    close_trade(trade, trade["tp"], "TP")
                elif sl_price is not None and recent_low <= sl_price:
                    close_trade(trade, sl_price, "SL")
                    log(f"  SL HIT: {coin} LONG | Low {recent_low:.6f} <= SL {sl_price:.6f} ({sl_pct}%)")
                elif recent_low <= trade["liq"]:
                    close_trade(trade, trade["liq"], "LIQ")
                    log(f"  LIQ HIT: {coin} LONG | Low {recent_low:.6f} <= Liq {trade['liq']:.6f}")
            else:  # SHORT
                if recent_low <= trade["tp"]:
                    close_trade(trade, trade["tp"], "TP")
                elif sl_price is not None and recent_high >= sl_price:
                    close_trade(trade, sl_price, "SL")
                    log(f"  SL HIT: {coin} SHORT | High {recent_high:.6f} >= SL {sl_price:.6f} ({sl_pct}%)")
                elif recent_high >= trade["liq"]:
                    close_trade(trade, trade["liq"], "LIQ")
                    log(f"  LIQ HIT: {coin} SHORT | High {recent_high:.6f} >= Liq {trade['liq']:.6f}")


def update_stats(data):
    """Update statistics for all timeframes."""
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

        wins = [t for t in closed if t.get("close_reason") == "TP"]
        losses = [t for t in closed if t.get("close_reason") in ("SL", "LIQ", "SMA_RISK", "SW_RISK")]
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

        open_count = len([t for t in data[tf_key] if t["status"] == "open"])
        data[stats_key]["open"] = open_count


# ═══════════════════════════════════════════════════════════════
# SCAN & TRADE
# ═══════════════════════════════════════════════════════════════

def check_btc_spike():
    """Check if BTC moved >1% in last 15min — fakeout protection."""
    try:
        url = "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=3"
        req = urllib.request.Request(url, headers={"User-Agent": "LiveBot/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            klines = json.loads(resp.read().decode())
        if not klines or len(klines) < 3:
            return False
        open_price = float(klines[0][1])
        close_price = float(klines[-1][4])
        move_pct = abs(close_price - open_price) / open_price * 100
        return move_pct > 1.0
    except:
        return False


def scan_and_trade(data, tf, limit, tf_key):
    """Scan all coins and open trades where probability >= threshold."""
    now = datetime.now(TZ)
    mode_label = "LIVE" if LIVE_MODE else "DRY-RUN"
    log(f"\n{'='*60}")
    log(f"SCAN {tf.upper()} [{mode_label}] | {now.strftime('%Y-%m-%d %H:%M ET')}")
    log(f"{'='*60}")

    # Fakeout-Bremse
    if check_btc_spike():
        log(f"  BTC SPIKE erkannt (>1% in 15min) — Bremse aktiv, kein neuer Trade.")
        return

    open_trades = [t for t in data[tf_key] if t["status"] == "open"]
    # Check ALL open trades across ALL timeframes — 1 coin = 1 trade max
    all_open = [t for tf_k in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]
                for t in data.get(tf_k, []) if t["status"] == "open"]
    open_coins = set(t["coin"] for t in all_open)

    if tf_key == "trades_15m" and len(open_trades) >= CONFIG["max_open_15m"]:
        log(f"  Max open 15m trades reached ({CONFIG['max_open_15m']}). Skipping scan.")
        return

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

    today = now.strftime("%Y-%m-%d")

    # In LIVE mode: check wallet balance before scanning
    if LIVE_MODE:
        balance = get_wallet_balance()
        if balance:
            log(f"  Wallet: ${balance['available']:.2f} available | ${balance['equity']:.2f} equity")
            if balance["available"] < CONFIG["capital"]:
                log(f"  WARNING: Available balance (${balance['available']:.2f}) < capital (${CONFIG['capital']}). Some trades may fail.")

    # Build recent LIQ history for cooldown
    recent_liqs = {}
    for tfk in ["trades_15m", "trades_30m", "trades_1h", "trades_4h"]:
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

    signals_found = 0
    trades_opened = 0

    for coin in COINS:
        if coin in open_coins:
            continue

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

            # Cooldown: nach LIQ 30min Sperre fuer gleiche Richtung
            if coin in recent_liqs:
                liq_dir = recent_liqs[coin]["direction"]
                if direction == liq_dir:
                    log(f"  COOLDOWN: {coin} {direction} — gleiche Richtung wie LIQ vor <30min.")
                    continue

            if probability >= CONFIG["min_probability"]:
                signals_found += 1
                log(f"  SIGNAL: {coin} {direction} | Prob: {probability}% | Bias: {result['coin_bias']} | BTC: {result['btc_trend']}")
                log(f"          Scores: {result['scores']} | L:{result['long_count']} S:{result['short_count']}")

                if tf_key == "trades_15m":
                    current_open = len([t for t in data[tf_key] if t["status"] == "open"])
                    if current_open >= CONFIG["max_open_15m"]:
                        log(f"  Max open 15m trades reached. Stopping scan.")
                        break

                if tf_key == "trades_4h":
                    current_open = len([t for t in data[tf_key] if t["status"] == "open"])
                    if current_open >= CONFIG["max_open_4h"]:
                        log(f"  Max open 4h trades reached. Stopping scan.")
                        break

                trade = open_trade(data, tf_key, coin, direction, entry, tp, probability, tf)
                if trade:
                    trades_opened += 1

        except Exception as e:
            log(f"  ERROR analyzing {coin}: {e}")
            traceback.print_exc()
            continue

        time.sleep(0.1)

    log(f"\nScan complete: {signals_found} signals found, {trades_opened} trades opened")
    open_count = len([t for t in data[tf_key] if t["status"] == "open"])
    log(f"Open {tf} trades: {open_count}")


# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

def log(msg):
    """Print timestamped log message and append to log file."""
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
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
    mode_label = "LIVE" if LIVE_MODE else "DRY-RUN"
    log(f"{'='*60}")
    log(f"  Live Trading Bot starting in {mode_label} mode")
    log(f"{'='*60}")

    if not LIVE_MODE:
        log("NOTE: Running in DRY-RUN mode. No real orders will be placed.")
        log("      Use --live flag to enable real trading.")
        log("")

    load_credentials()

    log(f"Config: Capital=${CONFIG['capital']}, Leverage={CONFIG['leverage']}x, Min Prob={CONFIG['min_probability']}%")
    log(f"Coins: {len(COINS)} | Data file: {DATA_FILE}")
    log(f"TP range: {CONFIG['tp_range_pct']}% of expected move")

    if LIVE_MODE:
        log("")
        log("*** WARNING: LIVE MODE ACTIVE — REAL MONEY AT RISK ***")
        log("*** Trades will be executed on Bybit ***")
        log("")
        # Show wallet balance
        balance = get_wallet_balance()
        if balance:
            log(f"Bybit wallet: ${balance['available']:.2f} available | ${balance['equity']:.2f} equity | ${balance['wallet_balance']:.2f} balance")
        else:
            log("WARNING: Could not fetch wallet balance. Check API credentials.")
        log("")
        # 5-second countdown for safety
        for i in range(5, 0, -1):
            log(f"  Starting in {i}...")
            time.sleep(1)

    # Apply config overrides from file
    load_config_overrides()

    # Store start time for status reporting
    write_bot_status._start_time = datetime.now(TZ).isoformat()

    # Write PID file for bot_server.py
    try:
        with open("/tmp/live_bot.pid", "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    # Initial status write
    write_bot_status()

    data = load_data()

    # Track last scan times
    last_15m_scan = -1
    last_30m_scan = -1
    last_1h_scan = -1
    last_4h_scan = -1

    while True:
        try:
            now = datetime.now(TZ)
            minute = now.minute
            hour = now.hour

            # Check open trades for TP/Liq every minute
            check_open_trades(data)

            # 15min scan: run every 15 minutes (matching paper bot)
            current_15m_slot = (hour * 60 + minute) // 15
            if minute % 15 == 0 and current_15m_slot != last_15m_scan:
                last_15m_scan = current_15m_slot
                scan_and_trade(data, "15m", 800, "trades_15m")
                update_stats(data)
                save_data(data)
                print_status(data)

            # 30m scan: run every 10 minutes
            current_10m_slot = (hour * 60 + minute) // 10
            current_30m_slot = (hour * 60 + minute) // 30
            if minute % 30 == 0 and current_30m_slot != last_30m_scan:
                last_30m_scan = current_10m_slot
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

            # Reload config from file (allows bot_server.py to change settings)
            load_config_overrides()

            # Write status + positions for dashboard every minute
            write_bot_status()
            try:
                positions = get_positions()
                pos_data = []
                if positions:
                    for p in positions:
                        if float(p.get('size', '0')) > 0:
                            pos_data.append({
                                'symbol': p.get('symbol', ''),
                                'side': p.get('side', ''),
                                'size': p.get('size', '0'),
                                'avgPrice': p.get('avgPrice', '0'),
                                'markPrice': p.get('markPrice', '0'),
                                'liqPrice': p.get('liqPrice', '0'),
                                'unrealisedPnl': p.get('unrealisedPnl', '0'),
                                'leverage': p.get('leverage', '0'),
                                'takeProfit': p.get('takeProfit', ''),
                                'stopLoss': p.get('stopLoss', ''),
                            })
                pos_file = os.path.join(os.path.dirname(DATA_FILE), 'live_positions.json')
                with open(pos_file, 'w') as f:
                    json.dump({'positions': pos_data, 'timestamp': datetime.now(TZ).isoformat()}, f, indent=2)
            except:
                pass

            # Save periodically
            update_stats(data)
            save_data(data)

            # Auto-push to GitHub every 5 minutes
            if minute % 5 == 0:
                try:
                    import subprocess
                    bot_dir = os.path.dirname(DATA_FILE)
                    subprocess.run(["git", "add", "live_trades.json", "live_positions.json", "live_bot_status.json"], cwd=bot_dir, capture_output=True, timeout=10)
                    subprocess.run(["git", "commit", "-m", f"Live bot data update ({mode_label})"], cwd=bot_dir, capture_output=True, timeout=10)
                    subprocess.run(["git", "push"], cwd=bot_dir, capture_output=True, timeout=30)
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
            # Write stopped status
            try:
                with open(STATUS_FILE_PATH, "w") as f:
                    json.dump({"running": False, "mode": "Gestoppt", "last_check": datetime.now(TZ).isoformat()}, f, indent=2)
                os.remove("/tmp/live_bot.pid")
            except Exception:
                pass
            log("Data saved. Goodbye.")
            sys.exit(0)
        except Exception as e:
            log(f"ERROR in main loop: {e}")
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()
