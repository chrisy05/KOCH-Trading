#!/usr/bin/env python3
"""
KODA Cascade 4 LIVE Bot — Phase Detection + Bybit V5 API
Merges: paper_bot_cascade4.py (signal/trading logic) + live_bot_confirm.py (Bybit execution)

Usage:
    python3 live_bot_cascade4.py              # Dry-run mode (logs what WOULD happen)
    python3 live_bot_cascade4.py --live       # LIVE mode (places real orders on Bybit!)

Strategy:
    - Full 7-score analysis (delta, OB, funding, distance, walls, POC, VA)
    - Score gate: >= 3/7 aligned
    - BTC Cascade Ampel: >= 4 lights required (CASCADE_MIN=4)
    - BTC MTF Gate: 1H SMA20 vs SMA50
    - BTC Spike Filter: >1% in 15min blocks new trades
    - Confirmation Stage: signal must move 0.3% in predicted direction within 8 bars
    - Phase Detection Entry: score >= 6.0, no Phase D, consistent direction
    - Phase Detection SL: progressive tightening (5m D->5%, 15m D->3%, 30m D->close)
    - TP1/TP2 trailing: 50% close at TP1, SL to BE (fee-covered), 2% trail from peak
    - SL: MARGIN-based (70% margin / 10x = 7% price)
    - Fees: 0.11% round trip deducted from PnL
    - BE Stop: entry + fees + 0.1% buffer
    - TP from confirmation entry price (not signal price)
    - 24h Force Close: no TP1 after 24h -> close at market
    - Collective Profit Exit: ROI>30% single + sum>=100% -> close all profitable
    - Drawdown Brake: 5 consecutive SLs -> pause + Telegram alert
    - Budget recheck before each trade open (K3 fix)

Config: $15/trade, 10x leverage, $1000 budget, 70% margin SL, 60% TP EM
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
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ===============================================================
# CONFIGURATION
# ===============================================================

CONFIG = {
    "capital": 15,            # $15 per trade (live)
    "leverage": 10,
    "min_probability": 60,
    "tp_range_pct": 60,       # 60% EM
    "sl_margin_pct": 70,      # 70% MARGIN = 7% price at 10x
    "total_budget": 1000,
    "tf_budget_15m": 50,
    "tf_budget_30m": 50,
    "tf_budget_1h": 0,
    "tf_budget_4h": 0,
    "max_open_15m": 20,
    "max_open_30m": 20,
}

CASCADE_MIN = 3               # Minimum 4/5 timeframes aligned
CONFIRM_PCT = 0.003            # 0.3% confirmation
CONFIRM_BARS = 8               # 8 minutes timeout
TRAIL_PCT = 0.002  # 0.2% price = 2% margin at 10x               # 2% trailing
FEE_RATE = 0.0011              # 0.11% round trip

# C8 Strategy: High Win-Rate Hours Only (UTC)
TRADING_HOURS_UTC = {0, 3, 5, 6, 7, 8, 9, 11, 14, 20, 21, 22}

# C8 Strategy: BTC Momentum Gate threshold (0.2%)
BTC_MOMENTUM_THRESHOLD = 0.002

# Phase Detection config
PHASE_ENTRY_MIN_SCORE = 4.0    # Minimum phase score for entry
PHASE_SCORES = {'C': 2.0, 'B': 1.5, 'A': 1.0, 'D': 0.0, 'X': 0.0}
PHASE_TFS = ["5m", "15m", "30m", "1h", "4h"]
PHASE_SL_LEVELS = {
    0: 0.07,   # Normal: 70% margin / 10x = 7% price
    1: 0.05,   # 5m Phase D: tighten to 50% margin = 5% price
    2: 0.03,   # 15m Phase D: tighten to 30% margin = 3% price
    3: 0.00,   # 30m Phase D: close immediately (PHASE_EXIT)
}

# Drawdown brake
DRAWDOWN_BRAKE_SL_COUNT = 5
_consecutive_sl_count = 0
_drawdown_paused = False
_current_data = None

# Phase Detection caches
_phase_cache = {}  # coin -> {"ts": timestamp, "phases": {tf: (phase, direction)}}
PHASE_CACHE_SECONDS = 120

# Confirmation stage
_pending_signals = []

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load config overrides from JSON file (written by dashboard settings)
_CFG_OVERRIDE = os.path.join(BASE_DIR, "live_bot_cascade4_config.json")
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
DATA_FILE = os.path.join(BASE_DIR, "live_trades_cascade4.json")
LOG_FILE = os.path.join(BASE_DIR, "live_bot_cascade4.log")
STATUS_FILE_PATH = os.path.join(BASE_DIR, "live_bot_cascade4_status.json")

# SSL context for Binance API (analysis data)
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# ===============================================================
# MODE FLAG
# ===============================================================

LIVE_MODE = "--live" in sys.argv

# ===============================================================
# LOGGING
# ===============================================================

def log(msg):
    """Print timestamped log message and append to log file."""
    ts = datetime.now(TZ).strftime("%H:%M:%S")
    line = f"[{ts}] [CASCADE4-LIVE] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# ===============================================================
# API CREDENTIALS (Bybit)
# ===============================================================

CREDENTIALS_FILE = os.path.join(BASE_DIR, "bybit_credentials.json")
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
        if API_KEY and API_KEY != "YOUR_API_KEY_HERE":
            log(f"Credentials loaded. API key: {API_KEY[:8]}...")
    except Exception as e:
        log(f"ERROR loading credentials: {e}")
        if LIVE_MODE:
            sys.exit(1)

# ===============================================================
# TELEGRAM
# ===============================================================

TG_BOT_TOKEN = "8623243424:AAEqo7FlHPqZzZHrpLMQJFBxGnNY382YhW4"
TG_CHRIS_ID = "351653518"


def send_tg(chat_id, text):
    """Send Telegram message."""
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except Exception as e:
        log(f"TG send failed ({chat_id}): {e}")


def send_tg_chris(text):
    """Send direct message to Chris."""
    send_tg(TG_CHRIS_ID, text)


def send_tg_channel(text):
    """DISABLED -- Cascade4 LIVE bot does NOT post to signal channel."""
    pass

# ===============================================================
# BYBIT API HELPERS
# ===============================================================

BYBIT_BASE = "https://api.bybit.com"

# Bybit symbol mapping
BYBIT_SYMBOL_MAP = {}  # Default: {coin}USDT

# Bybit quantity precision per symbol (decimals for qty rounding)
BYBIT_QTY_DECIMALS = {
    "BTCUSDT": 3, "ETHUSDT": 2, "SOLUSDT": 1, "BCHUSDT": 2,
    "LTCUSDT": 2, "BNBUSDT": 2, "XMRUSDT": 2, "AAVEUSDT": 2,
    "TAOUSDT": 3, "ICPUSDT": 1, "AVAXUSDT": 1, "APTUSDT": 1,
    "DOTUSDT": 1, "FILUSDT": 1, "NEOUSDT": 1, "THETAUSDT": 1,
    "LINKUSDT": 1, "UNIUSDT": 1, "INJUSDT": 1, "SUIUSDT": 0,
    "TONUSDT": 1, "RUNEUSDT": 0, "SEIUSDT": 0, "OPUSDT": 1,
    "IMXUSDT": 0, "JUPUSDT": 0, "METISUSDT": 2,
    # Confirmation bot coins
    "GLMUSDT": 0, "CYSUSDT": 0, "SYNUSDT": 0, "AXLUSDT": 1,
    "DUSKUSDT": 0, "IOSTUSDT": 0, "CELRUSDT": 0, "GRTUSDT": 0,
    "KASUSDT": 0, "IDUSDT": 0, "ENJUSDT": 0, "MOVRUSDT": 2,
    "MORPHOUSDT": 0, "BERAUSDT": 0, "KMNOUSDT": 0, "AXSUSDT": 1,
    "ORCAUSDT": 1,
    # Cascade4 additional coins
    "XRPUSDT": 1, "FLOWUSDT": 1, "MINAUSDT": 1,
    "CAKEUSDT": 1, "KAITOUSDT": 0,
    "TRXUSDT": 0, "SUNUSDT": 0, "BATUSDT": 0,
    "HBARUSDT": 0, "IOSTUSDT": 0,
}


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
    params: dict
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
    except Exception as e:
        log(f"  Warning: switch-isolated for {symbol}: {e}")

    try:
        result = bybit_request("POST", "/v5/position/set-leverage", {
            "category": "linear",
            "symbol": symbol,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        })
        if result and result.get("retCode") == 0:
            log(f"  Set leverage {leverage}x for {symbol}")
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
        "positionIdx": 0,
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
    """Set TP and/or SL on an existing position in a SINGLE call."""
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
                    try:
                        return float(v) if v else 0.0
                    except Exception:
                        return 0.0
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

# ===============================================================
# BINANCE API HELPERS (for analysis data)
# ===============================================================

def api(url, timeout=10):
    """Fetch JSON from URL with error handling."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LiveBotCascade4/1.0"})
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


def get_recent_highlow(sym, minutes=5):
    """Get high/low from recent 1m klines to catch wicks."""
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit={minutes}"
        req = urllib.request.Request(url, headers={"User-Agent": "LiveBotCascade4/1.0"})
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
# ANALYSIS (Volume Profile, Absorption, 7-Score)
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

    timeframes = ["15m", "30m", "1h", "4h"]
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
# PHASE DETECTION (from backtest_phase_detection_c4.py)
# ===============================================================

def _detect_phase_direction(sma10, sma20, sma50, sma10_hist, sma20_hist, direction):
    """Detect phase for a single direction. Returns phase letter (A/B/C/D/X)."""
    if direction == 'LONG':
        if sma10 > sma20 > sma50:
            gap_now = sma10 - sma20
            gaps = [sma10_hist[-(i+1)] - sma20_hist[-(i+1)] for i in range(min(3, len(sma10_hist)))]
            if len(gaps) >= 2 and all(g > 0 for g in gaps):
                if gap_now < gaps[-1] and (len(gaps) < 3 or gap_now < gaps[-2]):
                    return 'D'
            return 'C'

        if sma10 > sma20:
            crossed_recently = False
            for i in range(1, min(4, len(sma10_hist))):
                if sma10_hist[-(i+1)] <= sma20_hist[-(i+1)]:
                    crossed_recently = True
                    break
            if crossed_recently:
                return 'A'

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
    """Fetch klines for a TF and compute SMA10/20/50 with history for phase detection."""
    klines = fetch_klines(symbol, tf, limit)
    if not klines or len(klines) < 50:
        return None
    closes = [k["close"] for k in klines]

    sma10_hist = []
    sma20_hist = []
    for offset in range(3, -1, -1):
        idx = len(closes) - 1 - offset
        if idx < 49:
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
    """Get phase detection for a coin across all 5 TFs. Uses cache."""
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
                time.sleep(0.05)
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

    # Phase D blocks on 30m/1h only (not 5m/15m/4h)
    # Opposite direction blocks on 15m/30m/1h
    phase_d_block_tfs = ["30m", "1h"]
    opposite_block_tfs = ["15m", "30m", "1h"]

    for tf in PHASE_TFS:
        if tf not in phases:
            details_parts.append(f"{tf}:?")
            continue

        phase, phase_dir = phases[tf]

        if phase_dir == direction:
            score += PHASE_SCORES[phase]
            if phase == 'D' and tf in phase_d_block_tfs:
                has_phase_d = True
            details_parts.append(f"{tf}:{phase}")
        elif phase_dir is not None and phase_dir != direction:
            if tf in opposite_block_tfs:
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
            return None

        direction = trade["direction"]
        sl_level = 0

        tf_order = ['5m', '15m', '30m']
        for i, tf in enumerate(tf_order):
            if tf not in phases:
                continue
            phase, phase_dir = phases[tf]

            is_degrading = False
            if phase == 'D' and phase_dir == direction:
                is_degrading = True
            elif phase_dir is not None and phase_dir != direction and phase != 'X':
                is_degrading = True

            if is_degrading:
                new_level = i + 1  # 5m=1, 15m=2, 30m=3
                if new_level > sl_level:
                    sl_level = new_level

        if sl_level >= 3:
            return "CLOSE"
        elif sl_level >= 2:
            return "TIGHTEN_L2"
        elif sl_level >= 1:
            return "TIGHTEN_L1"
        else:
            return None

    except Exception as e:
        log(f"  PHASE SL: Error checking {coin}: {e}")
        return None

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

def calc_liquidation(entry, direction, margin, size):
    """Calculate liquidation price."""
    maint = margin * 0.005
    net_margin = margin - maint
    if direction == "LONG":
        return entry - net_margin / size
    else:
        return entry + net_margin / size


def load_data():
    """Load trades from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
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
                "bot_name": "KODA Cascade 4 LIVE",
                "mode": "LIVE" if LIVE_MODE else "DRY-RUN",
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
            "bot_name": "KODA Cascade 4 LIVE",
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
    """Save trades to JSON file."""
    data["_heartbeat"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
    data["_drawdown_paused"] = _drawdown_paused
    data["_consecutive_sl"] = _consecutive_sl_count
    data["_mode"] = "LIVE" if LIVE_MODE else "DRY-RUN"
    data["_pending_signals"] = len(_pending_signals)
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

# ===============================================================
# NOTIFICATIONS
# ===============================================================

def notify_trade_opened(trade, data=None):
    """Send Telegram notification when trade opens."""
    coin = trade["coin"]
    d = trade["direction"]
    entry = trade["entry"]
    tp = trade["tp"]
    sl = trade["sl"]
    prob = trade["probability"]
    lev = trade["leverage"]
    tf = trade["tf"]
    cascade = trade.get("cascade_code", "")
    mode = trade.get("mode", "DRY-RUN")
    phase_score = trade.get("phase_score", 0)
    phase_details = trade.get("phase_details", "")

    tp_pct = abs(tp / entry - 1) * 100
    sl_pct = abs(sl / entry - 1) * 100

    fmt = lambda x: f"${x:,.2f}" if x > 100 else (f"${x:.4f}" if x > 1 else f"${x:.6f}")

    msg = f"""CASCADE4-LIVE #{trade['id']} -- {coin} {d} [{mode}]
{'='*30}
Prob: {prob}% | TF: {tf} | Kaskade: {cascade}
Phase: {phase_score:.1f} | {phase_details}

Entry: {fmt(entry)}
TP1:   {fmt(tp)} ({'+' if d=='LONG' else '-'}{tp_pct:.1f}%) -> 50% close, SL->BE
TP2:   Trailing {TRAIL_PCT*100:.0f}% vom Peak
SL:    {fmt(sl)} ({'-' if d=='LONG' else '+'}{sl_pct:.1f}% = {CONFIG['sl_margin_pct']}% margin)

Hebel: {lev}x | Margin: ${trade['margin']}
{'='*30}
CASCADE4-LIVE | {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')} ET"""

    send_tg_chris(msg)


def notify_trade_closed(trade, data=None):
    """Send Telegram notification when trade closes."""
    coin = trade["coin"]
    d = trade["direction"]
    pnl = trade["pnl"]
    roi = trade["roi"]
    reason = trade["close_reason"]
    entry = trade["entry"]
    close = trade["close_price"]
    mode = trade.get("mode", "DRY-RUN")

    fmt = lambda x: f"${x:,.2f}" if x > 100 else (f"${x:.4f}" if x > 1 else f"${x:.6f}")

    footer = ""
    if data:
        total, wins, wr, total_pnl = get_overall_stats(data)
        pnl_sign = "+" if total_pnl >= 0 else ""
        footer = f"\n{total} Trades | {wins} positiv | WR: {wr:.0f}% | Gesamt: {pnl_sign}${total_pnl:.2f}"

    if pnl > 0:
        header = f"CASCADE4 CLOSED -- WIN [{mode}]"
        result_line = f"+${pnl:.2f} ({roi:+.1f}%)"
    else:
        header = f"CASCADE4 CLOSED -- LOSS [{mode}]"
        result_line = f"${pnl:.2f} ({roi:+.1f}%)"

    duration = ""
    if trade.get("open_time") and trade.get("close_time"):
        try:
            t1 = datetime.fromisoformat(trade["open_time"])
            t2 = datetime.fromisoformat(trade["close_time"])
            mins = int((t2 - t1).total_seconds() / 60)
            if mins < 60:
                duration = f"{mins}m"
            elif mins < 1440:
                duration = f"{mins//60}h {mins%60}m"
            else:
                duration = f"{mins//1440}d {(mins%1440)//60}h"
        except Exception:
            pass

    msg = f"""{header}
{'='*30}
{coin} {d} | {trade.get('tf','')} | {reason}

Entry:  {fmt(entry)}
Exit:   {fmt(close)}
Dauer:  {duration}

{result_line}
{'='*30}{footer}
CASCADE4-LIVE | {datetime.now(TZ).strftime('%H:%M')} ET"""

    send_tg_chris(msg)


def send_hourly_status(data):
    """Send hourly status update to Chris."""
    total, wins, wr, total_pnl = get_overall_stats(data)
    all_open = []
    for tfk in ["trades_15m", "trades_30m"]:
        all_open.extend([t for t in data.get(tfk, []) if t["status"] == "open"])

    pending = len(_pending_signals)
    mode = "LIVE" if LIVE_MODE else "DRY-RUN"

    balance_str = ""
    if LIVE_MODE:
        bal = get_wallet_balance()
        if bal:
            balance_str = f"\nWallet: ${bal['available']:.2f} avail | ${bal['equity']:.2f} equity"

    open_summary = ""
    if all_open:
        open_pnl = sum(t.get("pnl", 0) or 0 for t in all_open)
        open_summary = f"\nOpen: {len(all_open)} trades | Unrealized: ${open_pnl:+.2f}"

    msg = f"""CASCADE4-LIVE [{mode}] -- Stundenbericht
{'='*30}
Closed: {total} | Wins: {wins} | WR: {wr:.0f}%
PnL: ${total_pnl:+.2f}{open_summary}
Pending: {pending} signals{balance_str}
SL-Streak: {_consecutive_sl_count}/{DRAWDOWN_BRAKE_SL_COUNT}
{'='*30}
{datetime.now(TZ).strftime('%d.%m.%Y %H:%M')} ET"""

    send_tg_chris(msg)


def send_drawdown_alert(sl_count):
    """Send Telegram alert when drawdown brake activates."""
    text = (f"DRAWDOWN-BREMSE AKTIV -- CASCADE4-LIVE Bot\n\n"
            f"{sl_count} Verluste in Folge!\n"
            f"Bot ist PAUSIERT. Keine neuen Trades.\n"
            f"Offene Trades laufen weiter (TP/SL aktiv).\n\n"
            f"Bitte D/W Analyse durchfuehren.\n"
            f"Zum Fortfahren: Bot manuell neu starten.")
    send_tg_chris(text)
    log(f"DRAWDOWN ALERT sent ({sl_count} SLs in row)")

# ===============================================================
# OPEN TRADE
# ===============================================================

def open_trade(data, tf_key, coin, direction, entry, tp_signal, probability, tf,
               cascade_lights=0, cascade_code="00000", expected_move=0,
               phase_score=0, phase_details=""):
    """
    Open a trade -- Bybit execution in LIVE mode, local tracking in DRY-RUN.
    CRITICAL FIX 3: TP is recalculated from the confirmation entry price.
    CRITICAL FIX K3: Budget recheck before each trade open.
    """
    capital = CONFIG["capital"]
    leverage = CONFIG["leverage"]

    # ─── K3 FIX: Budget recheck before each trade open ───
    all_open = [t for tfk in ["trades_15m", "trades_30m"]
                for t in data.get(tfk, []) if t["status"] == "open"]
    all_closed_trades = []
    for tfk in ["trades_15m", "trades_30m"]:
        all_closed_trades.extend([t for t in data.get(tfk, []) if t["status"] == "closed"])
    realized_pnl = sum(t.get("pnl", 0) or 0 for t in all_closed_trades)
    margin_used = sum(t.get("margin", capital) for t in all_open)
    current_budget = CONFIG.get("total_budget", 1000) + realized_pnl
    budget_available = current_budget - margin_used
    if budget_available < capital:
        log(f"  K3 BUDGET CHECK: ${budget_available:.0f} free < ${capital} needed. SKIP {coin}.")
        return None

    # Also check per-TF budget
    tf_open = [t for t in data[tf_key] if t["status"] == "open"]
    tf_budget_key = f"tf_budget_{tf}"
    tf_budget_pct = CONFIG.get(tf_budget_key, 50)
    tf_budget_limit = CONFIG.get("total_budget", 1000) * (tf_budget_pct / 100.0)
    tf_margin_used = sum(t.get("margin", capital) for t in tf_open)
    if tf_margin_used + capital > tf_budget_limit:
        log(f"  K3 TF BUDGET: {tf} ${tf_margin_used:.0f}+${capital} > ${tf_budget_limit:.0f}. SKIP {coin}.")
        return None

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
        tp = tp_signal

    # Validate TP direction
    if direction == "LONG" and tp <= entry:
        log(f"  SKIP: {coin} LONG TP {tp:.6f} <= entry {entry:.6f}")
        return None
    if direction == "SHORT" and tp >= entry:
        log(f"  SKIP: {coin} SHORT TP {tp:.6f} >= entry {entry:.6f}")
        return None

    bybit_symbol = get_bybit_symbol(coin)
    side = "Buy" if direction == "LONG" else "Sell"
    qty_str = round_qty(bybit_symbol, size)
    tp_rounded = round_price(tp)
    sl_rounded = round_price(sl)

    order_id = None
    be_stop = calc_be_stop(entry, direction)

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

        # 3. Set TP + SL in a SINGLE call
        tpsl_result = set_tp_sl(bybit_symbol, tp_price=str(tp_rounded), sl_price=str(sl_rounded))
        if not tpsl_result or tpsl_result.get("retCode") != 0:
            log(f"  [LIVE] TP/SL failed, retrying SL only...")
            time.sleep(0.5)
            sl_only = set_tp_sl(bybit_symbol, sl_price=str(sl_rounded))
            if not sl_only or sl_only.get("retCode") != 0:
                log(f"  [LIVE] CRITICAL: Even SL alone failed for {coin}! Position ungeschuetzt!")
            else:
                log(f"  [LIVE] SL gesetzt, TP fehlt -- Checker wird TP nachholen")

        log(f"  [LIVE] Trade opened successfully: {direction} {coin} | OrderID: {order_id}")
    else:
        # === DRY-RUN ===
        log(f"  [DRY-RUN] Would execute {direction} {coin} | Qty: {qty_str} | TP: {tp_rounded} | SL: {sl_rounded}")
        order_id = f"DRY-{int(time.time())}"

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
        # Bybit tracking
        "bybit_symbol": bybit_symbol,
        "bybit_order_id": order_id,
        "bybit_qty": qty_str,
        "mode": "LIVE" if LIVE_MODE else "DRY-RUN",
        # TP1/TP2 trailing fields
        "tp1_hit": False,
        "tp1_pnl": None,
        "peak_price": None,
        # BE stop (FIX 4)
        "be_stop": round(be_stop, 8),
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

    # Notify Telegram
    notify_trade_opened(trade, data)
    return trade


def close_trade(trade, close_price, reason):
    """Close a trade with fee-adjusted PnL."""
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
    """Check all open trades for TP, SL, trailing, 24h timeout, phase SL, collective exit.
    In LIVE mode: all closes go through Bybit API first."""

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
            bybit_sym = trade.get("bybit_symbol", f"{coin}USDT")

            # Update live price + unrealized PnL for dashboard
            trade["current_price"] = round(current_price, 8)
            unrealized = calc_pnl(trade["direction"], trade["entry"], current_price, trade["size"])
            if trade.get("tp1_hit") and trade.get("tp1_pnl"):
                unrealized = unrealized * 0.5 + trade["tp1_pnl"]
            trade["pnl"] = round(unrealized, 2)
            trade["roi"] = round(unrealized / trade["margin"] * 100, 2) if trade["margin"] else 0

            # In LIVE mode: check if position still exists on Bybit
            if LIVE_MODE and trade.get("mode") == "LIVE":
                if bybit_sym not in bybit_positions:
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
                                reason = "TP" if not trade.get("tp1_hit") else "TP1+TRAIL"
                            elif close_price <= entry:
                                reason = "SL" if not trade.get("tp1_hit") else "TP1+BE"
                            else:
                                reason = "TP" if not trade.get("tp1_hit") else "TP1+TRAIL"
                        else:
                            if close_price <= trade["tp"] * 1.002:
                                reason = "TP" if not trade.get("tp1_hit") else "TP1+TRAIL"
                            elif close_price >= entry:
                                reason = "SL" if not trade.get("tp1_hit") else "TP1+BE"
                            else:
                                reason = "TP" if not trade.get("tp1_hit") else "TP1+TRAIL"

                        if trade.get("tp1_hit") and trade.get("tp1_pnl"):
                            tp2_pnl = bybit_pnl
                            total_pnl = trade["tp1_pnl"] + tp2_pnl
                            trade["pnl"] = round(total_pnl, 2)
                            trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                            trade["close_price"] = round(close_price, 8)
                            trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                            trade["close_reason"] = reason
                            trade["status"] = "closed"
                            log(f"  [LIVE] Position {coin} closed on Bybit | {reason} @ {close_price:.6f} | TP1: ${trade['tp1_pnl']:.2f} + TP2: ${tp2_pnl:.2f} = ${total_pnl:.2f}")
                            notify_trade_closed(trade, _current_data)
                        else:
                            close_trade(trade, close_price, reason)
                            log(f"  [LIVE] Position {coin} closed on Bybit | {reason} @ {close_price:.6f} | Bybit PnL: ${bybit_pnl:.2f} ({exec_type})")
                    else:
                        log(f"  [LIVE] Position {coin} gone but closed-pnl unavailable. Retrying next scan.")
                    continue

            sl_price = trade.get("sl")
            if sl_price is None:
                sl_price = calc_sl_price(trade["entry"], trade["direction"])
                trade["sl"] = round(sl_price, 8)

            be_stop = trade.get("be_stop")
            if be_stop is None:
                be_stop = calc_be_stop(trade["entry"], trade["direction"])
                trade["be_stop"] = round(be_stop, 8)

            # 24h Force Close for trades without TP1
            if not trade.get("tp1_hit", False) and trade.get("open_time"):
                try:
                    open_dt = datetime.fromisoformat(trade["open_time"])
                    if open_dt.tzinfo is None:
                        open_dt = open_dt.replace(tzinfo=TZ)
                    age_hours = (datetime.now(TZ) - open_dt).total_seconds() / 3600
                    if age_hours >= 24:
                        # Force close on Bybit
                        if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                            close_side = "Sell" if trade["direction"] == "LONG" else "Buy"
                            result = close_position_market(bybit_sym, close_side, trade["bybit_qty"])
                            if not result or result.get("retCode") != 0:
                                log(f"  24H TIMEOUT CLOSE FAILED: {coin} | Retrying next cycle")
                                continue
                            log(f"  [LIVE] 24H timeout close OK: {coin}")
                        close_trade(trade, current_price, "24H_TIMEOUT")
                        log(f"  24H TIMEOUT: {coin} {trade['direction']} | No TP1 after {age_hours:.1f}h | PnL: ${trade['pnl']:.2f}")
                        continue
                except Exception:
                    pass

            # Phase-based SL management (progressive tightening)
            phase_action = check_phase_sl(trade, coin)
            if phase_action == "CLOSE":
                # 30m Phase D -> close immediately
                if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                    close_side = "Sell" if trade["direction"] == "LONG" else "Buy"
                    result = close_position_market(bybit_sym, close_side, trade["bybit_qty"])
                    if not result or result.get("retCode") != 0:
                        log(f"  PHASE EXIT CLOSE FAILED: {coin} | Retrying next cycle")
                        continue
                    log(f"  [LIVE] PHASE EXIT close OK: {coin}")
                close_trade(trade, current_price, "PHASE_EXIT")
                log(f"  PHASE EXIT: {coin} {trade['direction']} -- 30m Phase D detected")
                continue
            elif phase_action == "TIGHTEN_L2":
                # 15m Phase D -> tighten SL to 30% margin (3% price at 10x)
                new_sl_pct = PHASE_SL_LEVELS[2]
                if trade["direction"] == "LONG":
                    new_sl = trade["entry"] * (1 - new_sl_pct)
                    if new_sl > sl_price:
                        trade["sl"] = round(new_sl, 8)
                        trade["phase_sl_level"] = 2
                        log(f"  PHASE SL L2: {coin} {trade['direction']} -- 15m Phase D | SL tightened to {new_sl:.6f} (3% price)")
                        if LIVE_MODE and trade.get("mode") == "LIVE":
                            set_tp_sl(bybit_sym, sl_price=round_price(new_sl))
                else:
                    new_sl = trade["entry"] * (1 + new_sl_pct)
                    if new_sl < sl_price:
                        trade["sl"] = round(new_sl, 8)
                        trade["phase_sl_level"] = 2
                        log(f"  PHASE SL L2: {coin} {trade['direction']} -- 15m Phase D | SL tightened to {new_sl:.6f} (3% price)")
                        if LIVE_MODE and trade.get("mode") == "LIVE":
                            set_tp_sl(bybit_sym, sl_price=round_price(new_sl))
            elif phase_action == "TIGHTEN_L1":
                # 5m Phase D -> tighten SL to 50% margin (5% price at 10x)
                current_phase_level = trade.get("phase_sl_level", 0)
                if current_phase_level < 1:
                    new_sl_pct = PHASE_SL_LEVELS[1]
                    if trade["direction"] == "LONG":
                        new_sl = trade["entry"] * (1 - new_sl_pct)
                        if new_sl > sl_price:
                            trade["sl"] = round(new_sl, 8)
                            trade["phase_sl_level"] = 1
                            log(f"  PHASE SL L1: {coin} {trade['direction']} -- 5m Phase D | SL tightened to {new_sl:.6f} (5% price)")
                            if LIVE_MODE and trade.get("mode") == "LIVE":
                                set_tp_sl(bybit_sym, sl_price=round_price(new_sl))
                    else:
                        new_sl = trade["entry"] * (1 + new_sl_pct)
                        if new_sl < sl_price:
                            trade["sl"] = round(new_sl, 8)
                            trade["phase_sl_level"] = 1
                            log(f"  PHASE SL L1: {coin} {trade['direction']} -- 5m Phase D | SL tightened to {new_sl:.6f} (5% price)")
                            if LIVE_MODE and trade.get("mode") == "LIVE":
                                set_tp_sl(bybit_sym, sl_price=round_price(new_sl))

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
                        half_qty = float(trade.get("bybit_qty", "0")) / 2.0
                        half_qty_str = round_qty(bybit_sym, half_qty)

                        if LIVE_MODE and trade.get("mode") == "LIVE":
                            result = close_position_market(bybit_sym, "Sell", half_qty_str)
                            if not result or result.get("retCode") != 0:
                                err = result.get("retMsg", "Unknown") if result else "No response"
                                log(f"  TP1 CLOSE FAILED: {coin} Sell {half_qty_str} | {err} -- retry next scan")
                                continue
                            log(f"  [LIVE] TP1 partial close OK: {coin} Sell {half_qty_str}")
                            time.sleep(0.3)
                            # Move SL to BE (entry + fees + buffer)
                            be_price = round_price(be_stop)
                            set_tp_sl(bybit_sym, sl_price=be_price)
                            log(f"  [LIVE] SL moved to BE: {coin} @ {be_price}")

                        trade["tp1_hit"] = True
                        trade["tp1_pnl"] = round(tp1_pnl, 2)
                        trade["peak_price"] = trade["tp"]
                        trade["bybit_qty"] = half_qty_str
                        log(f"  TP1 HIT: {coin} LONG @ {trade['tp']:.6f} | Partial PnL: ${tp1_pnl:.2f} | SL->BE, trailing starts")

                    elif recent_low <= sl_price:
                        if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                            result = close_position_market(bybit_sym, "Sell", trade["bybit_qty"])
                            if not result or result.get("retCode") != 0:
                                err = result.get("retMsg", "Unknown") if result else "No response"
                                log(f"  SL CLOSE FAILED: {coin} | {err} -- retry next scan")
                                continue
                            log(f"  [LIVE] SL close OK: {coin} Sell {trade['bybit_qty']}")
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
                        if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                            result = close_position_market(bybit_sym, "Sell", trade["bybit_qty"])
                            if not result or result.get("retCode") != 0:
                                err = result.get("retMsg", "Unknown") if result else "No response"
                                log(f"  TP2 BE CLOSE FAILED: {coin} | {err} -- retry next scan")
                                continue
                            log(f"  [LIVE] TP2 BE close OK: {coin} Sell {trade['bybit_qty']}")
                        tp2_pnl = 0.0
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(be_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+BE"
                        trade["status"] = "closed"
                        log(f"  TP2 BE: {coin} LONG | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: $0.00 = ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)

                    elif recent_low <= trail_stop and trail_stop > be_stop:
                        if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                            result = close_position_market(bybit_sym, "Sell", trade["bybit_qty"])
                            if not result or result.get("retCode") != 0:
                                err = result.get("retMsg", "Unknown") if result else "No response"
                                log(f"  TP2 TRAIL CLOSE FAILED: {coin} | {err} -- retry next scan")
                                continue
                            log(f"  [LIVE] TP2 TRAIL close OK: {coin} Sell {trade['bybit_qty']}")
                        tp2_pnl = calc_pnl("LONG", trade["entry"], trail_stop, trade["size"]) * 0.5
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
                        tp1_pnl = calc_pnl("SHORT", trade["entry"], trade["tp"], trade["size"]) * 0.5
                        half_qty = float(trade.get("bybit_qty", "0")) / 2.0
                        half_qty_str = round_qty(bybit_sym, half_qty)

                        if LIVE_MODE and trade.get("mode") == "LIVE":
                            result = close_position_market(bybit_sym, "Buy", half_qty_str)
                            if not result or result.get("retCode") != 0:
                                err = result.get("retMsg", "Unknown") if result else "No response"
                                log(f"  TP1 CLOSE FAILED: {coin} Buy {half_qty_str} | {err} -- retry next scan")
                                continue
                            log(f"  [LIVE] TP1 partial close OK: {coin} Buy {half_qty_str}")
                            time.sleep(0.3)
                            be_price = round_price(be_stop)
                            set_tp_sl(bybit_sym, sl_price=be_price)
                            log(f"  [LIVE] SL moved to BE: {coin} @ {be_price}")

                        trade["tp1_hit"] = True
                        trade["tp1_pnl"] = round(tp1_pnl, 2)
                        trade["peak_price"] = trade["tp"]
                        trade["bybit_qty"] = half_qty_str
                        log(f"  TP1 HIT: {coin} SHORT @ {trade['tp']:.6f} | Partial PnL: ${tp1_pnl:.2f} | SL->BE, trailing starts")

                    elif recent_high >= sl_price:
                        if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                            result = close_position_market(bybit_sym, "Buy", trade["bybit_qty"])
                            if not result or result.get("retCode") != 0:
                                err = result.get("retMsg", "Unknown") if result else "No response"
                                log(f"  SL CLOSE FAILED: {coin} | {err} -- retry next scan")
                                continue
                            log(f"  [LIVE] SL close OK: {coin} Buy {trade['bybit_qty']}")
                        close_trade(trade, sl_price, "SL")

                    elif recent_high >= trade["liq"]:
                        close_trade(trade, trade["liq"], "LIQ")
                else:
                    # Phase 2: TP1 hit, trailing for TP2
                    peak = trade.get("peak_price", trade["tp"])
                    if recent_low < peak:
                        trade["peak_price"] = recent_low
                        peak = recent_low

                    trail_stop = peak * (1 + TRAIL_PCT)

                    if recent_high >= be_stop:
                        if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                            result = close_position_market(bybit_sym, "Buy", trade["bybit_qty"])
                            if not result or result.get("retCode") != 0:
                                err = result.get("retMsg", "Unknown") if result else "No response"
                                log(f"  TP2 BE CLOSE FAILED: {coin} | {err} -- retry next scan")
                                continue
                            log(f"  [LIVE] TP2 BE close OK: {coin} Buy {trade['bybit_qty']}")
                        tp2_pnl = 0.0
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(be_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+BE"
                        trade["status"] = "closed"
                        log(f"  TP2 BE: {coin} SHORT | TP1: ${trade.get('tp1_pnl', 0):.2f} + TP2: $0.00 = ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)

                    elif recent_high >= trail_stop and trail_stop > be_stop:
                        if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                            result = close_position_market(bybit_sym, "Buy", trade["bybit_qty"])
                            if not result or result.get("retCode") != 0:
                                err = result.get("retMsg", "Unknown") if result else "No response"
                                log(f"  TP2 TRAIL CLOSE FAILED: {coin} | {err} -- retry next scan")
                                continue
                            log(f"  [LIVE] TP2 TRAIL close OK: {coin} Buy {trade['bybit_qty']}")
                        tp2_pnl = calc_pnl("SHORT", trade["entry"], trail_stop, trade["size"]) * 0.5
                        total_pnl = trade.get("tp1_pnl", 0) + tp2_pnl
                        trade["pnl"] = round(total_pnl, 2)
                        trade["roi"] = round(total_pnl / trade["margin"] * 100, 2)
                        trade["close_price"] = round(trail_stop, 8)
                        trade["close_time"] = datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S")
                        trade["close_reason"] = "TP1+TRAIL"
                        trade["status"] = "closed"
                        log(f"  TP2 TRAIL: {coin} SHORT | Peak {peak:.6f} -> Trail {trail_stop:.6f} | Total: ${total_pnl:.2f}")
                        notify_trade_closed(trade, _current_data)

    # Collective Profit Exit
    check_collective_profit_exit(data)

# ===============================================================
# COLLECTIVE PROFIT EXIT
# ===============================================================

def check_collective_profit_exit(data):
    """
    If any single open trade has ROI > 30% AND the sum of ROI of all profitable
    tp1_hit open trades >= 100%, close all profitable tp1_hit trades.
    """
    all_open = []
    for tf_key in ["trades_15m", "trades_30m"]:
        all_open.extend([t for t in data[tf_key] if t["status"] == "open"])

    if not all_open:
        return

    has_high_roi = any((t.get("roi") or 0) > 30 for t in all_open)
    if not has_high_roi:
        return

    profitable_tp1 = [t for t in all_open if t.get("tp1_hit") and (t.get("roi") or 0) > 0]
    if not profitable_tp1:
        return

    sum_roi = sum(t.get("roi", 0) for t in profitable_tp1)
    if sum_roi >= 100:
        log(f"  COLLECTIVE PROFIT EXIT: Sum ROI of {len(profitable_tp1)} profitable TP1 trades = {sum_roi:.1f}% >= 100%")
        for trade in profitable_tp1:
            coin = trade["coin"]
            bybit_sym = trade.get("bybit_symbol", f"{coin}USDT")
            current = trade.get("current_price", trade["entry"])

            # Close on Bybit if LIVE
            if LIVE_MODE and trade.get("mode") == "LIVE" and trade.get("bybit_qty"):
                close_side = "Sell" if trade["direction"] == "LONG" else "Buy"
                result = close_position_market(bybit_sym, close_side, trade["bybit_qty"])
                if not result or result.get("retCode") != 0:
                    log(f"  COLLECTIVE CLOSE FAILED: {coin} | Retrying next cycle")
                    continue
                log(f"  [LIVE] Collective close OK: {coin}")

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
            notify_trade_closed(trade, _current_data)
            time.sleep(0.1)

        total_pnl_all = sum(t.get("pnl", 0) or 0 for t in profitable_tp1 if t["status"] == "closed")
        mode = "LIVE" if LIVE_MODE else "DRY-RUN"
        send_tg_chris(f"COLLECTIVE PROFIT EXIT [{mode}]\n{len([t for t in profitable_tp1 if t['status']=='closed'])} Trades geschlossen\nGesamt: ${total_pnl_all:+.2f}")

# ===============================================================
# STATS
# ===============================================================

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
        req = urllib.request.Request(url, headers={"User-Agent": "LiveBotCascade4/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            klines = json.loads(resp.read().decode())
        if not klines or len(klines) < 3:
            return False
        open_price = float(klines[0][1])
        close_price = float(klines[-1][4])
        move_pct = abs(close_price - open_price) / open_price * 100
        if move_pct > 1.0:
            log(f"  BTC SPIKE {move_pct:.1f}% in 15min -- Bremse aktiv")
            return True
        return False
    except Exception:
        return False


def check_btc_momentum(direction, threshold=None):
    """C8: Check if BTC has momentum in trade direction (>= 0.2% in 1h)."""
    if threshold is None:
        threshold = BTC_MOMENTUM_THRESHOLD
    try:
        klines = fetch_klines("BTCUSDT", "1h", 2)
        if not klines or len(klines) < 2:
            return True, 0.0  # allow on error
        current = klines[-1]["close"]
        prev = klines[-2]["close"]  # 1h ago
        change = (current - prev) / prev
        if direction == "LONG" and change >= threshold:
            return True, change
        if direction == "SHORT" and change <= -threshold:
            return True, change
        return False, change
    except Exception:
        return True, 0.0  # allow on error


def scan_and_trade(data, tf, limit, tf_key):
    """Scan all coins and open trades where conditions are met.
    C8 Strategy: C4+Phase + Time Filter + BTC Momentum Gate."""
    # C8: Trading Hours Filter (UTC)
    utc_hour = datetime.now(timezone.utc).hour
    if utc_hour not in TRADING_HOURS_UTC:
        log(f"[CASCADE4] TIME SKIP: UTC hour {utc_hour} not in trading hours")
        return

    now = datetime.now(TZ)
    mode_label = "LIVE" if LIVE_MODE else "DRY-RUN"
    log(f"\n{'='*60}")
    log(f"SCAN {tf.upper()} [{mode_label}] | {now.strftime('%Y-%m-%d %H:%M ET')} | UTC {utc_hour}h")
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
    pending_coins = set(sig["coin"] for sig in _pending_signals)

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

    # In LIVE mode: check wallet balance
    if LIVE_MODE:
        balance = get_wallet_balance()
        if balance:
            log(f"  Wallet: ${balance['available']:.2f} available | ${balance['equity']:.2f} equity")
            if balance["available"] < CONFIG["capital"]:
                log(f"  WARNING: Available balance (${balance['available']:.2f}) < capital (${CONFIG['capital']})")

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
        if coin in open_coins or coin in pending_coins:
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

                # Cascade filter -- require >= CASCADE_MIN (4)
                bull_lights, bear_lights, cascade_dir, cascade_details = get_cascade_signal()
                if direction == "LONG":
                    lights_in_dir = bull_lights
                else:
                    lights_in_dir = bear_lights

                log(f"  SIGNAL: {coin} {direction} | Prob: {probability}% | Bias: {result['coin_bias']} | BTC: {result['btc_trend']}")
                log(f"          Scores: {result['scores']} | L:{result['long_count']} S:{result['short_count']}")
                log(f"          Cascade: {bull_lights}B/{bear_lights}S -> {cascade_dir} | In dir: {lights_in_dir} | {cascade_details}")

                if lights_in_dir < CASCADE_MIN:
                    log(f"  CASCADE SKIP: {coin} {direction} -- only {lights_in_dir} lights. Need >={CASCADE_MIN}.")
                    continue

                # Phase Detection gate
                try:
                    phase_score, has_phase_d, phase_details, phases_dict = calculate_phase_score(coin, direction)
                    log(f"          Phase: score={phase_score:.1f} | D={has_phase_d} | {phase_details}")

                    if has_phase_d:
                        log(f"  PHASE SKIP: {coin} {direction} -- Phase D or opposite direction detected | {phase_details}")
                        continue
                    if phase_score < PHASE_ENTRY_MIN_SCORE:
                        log(f"  PHASE SKIP: {coin} {direction} -- Score {phase_score:.1f} < {PHASE_ENTRY_MIN_SCORE:.1f} | {phase_details}")
                        continue

                    log(f"  PHASE OK: {coin} {direction} -- Score {phase_score:.1f} >= {PHASE_ENTRY_MIN_SCORE:.1f} | {phase_details}")
                except Exception as e:
                    log(f"  PHASE WARN: {coin} -- Phase detection failed ({e}), allowing trade as fallback")
                    phase_score = 0
                    phase_details = "FALLBACK"

                # C8: BTC Momentum Gate (0.2% in 1h in trade direction)
                btc_mom_ok, btc_change = check_btc_momentum(direction)
                if not btc_mom_ok:
                    needed = f"+{BTC_MOMENTUM_THRESHOLD*100:.2f}%" if direction == "LONG" else f"-{BTC_MOMENTUM_THRESHOLD*100:.2f}%"
                    log(f"[CASCADE4] BTC MOMENTUM SKIP: {direction} but BTC 1h change {btc_change*100:+.2f}% (need {needed})")
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
            traceback.print_exc()
            continue

        time.sleep(0.1)

    log(f"\nScan complete: {signals_found} signals, {pending_added} pending confirmation")
    open_count = len([t for t in data[tf_key] if t["status"] == "open"])
    log(f"Open {tf} trades: {open_count} | Pending confirmations: {len(_pending_signals)}")

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
# BOT STATUS (for dashboard)
# ===============================================================

def write_bot_status():
    """Write current bot status to status JSON for the dashboard."""
    try:
        balance_str = None
        api_ok = False
        if LIVE_MODE and API_KEY and API_KEY != "YOUR_API_KEY_HERE":
            bal = get_wallet_balance()
            if bal:
                balance_str = str(round(bal.get("wallet_balance", 0), 8))
                api_ok = True

        status = {
            "running": True,
            "mode": "LIVE" if LIVE_MODE else "DRY-RUN",
            "bot_type": "CASCADE4",
            "pid": os.getpid(),
            "balance": balance_str or "0",
            "api_ok": api_ok,
            "config": {
                "capital": CONFIG["capital"],
                "leverage": CONFIG["leverage"],
                "min_probability": CONFIG["min_probability"],
                "tp_range_pct": CONFIG["tp_range_pct"],
                "sl_margin_pct": CONFIG["sl_margin_pct"],
                "cascade_min": CASCADE_MIN,
                "phase_entry_min_score": PHASE_ENTRY_MIN_SCORE,
            },
            "start_time": getattr(write_bot_status, '_start_time', datetime.now(TZ).isoformat()),
            "last_scan": datetime.now(TZ).isoformat(),
            "last_check": datetime.now(TZ).isoformat(),
            "pending_signals": len(_pending_signals),
            "drawdown_paused": _drawdown_paused,
            "consecutive_sl": _consecutive_sl_count,
        }
        with open(STATUS_FILE_PATH, "w") as f:
            json.dump(status, f, indent=2)
    except Exception:
        pass


def write_positions_file():
    """Write Bybit positions to JSON file."""
    if not LIVE_MODE:
        return
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
        pos_file = os.path.join(BASE_DIR, 'live_cascade4_positions.json')
        with open(pos_file, 'w') as f:
            json.dump({'positions': pos_data, 'timestamp': datetime.now(TZ).isoformat()}, f, indent=2)
    except Exception:
        pass

# ===============================================================
# STATUS PRINT
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

    log(f"  Pending confirmations: {len(_pending_signals)} | Drawdown: {'PAUSED' if _drawdown_paused else 'OK'} ({_consecutive_sl_count}/{DRAWDOWN_BRAKE_SL_COUNT})")

# ===============================================================
# MAIN LOOP
# ===============================================================

def main():
    mode_label = "LIVE" if LIVE_MODE else "DRY-RUN"
    log(f"{'='*60}")
    log(f"  KODA Cascade 4 LIVE Bot starting in {mode_label} mode")
    log(f"{'='*60}")

    if not LIVE_MODE:
        log("NOTE: Running in DRY-RUN mode. No real orders will be placed.")
        log("      Use --live flag to enable real trading.")
        log("")

    load_credentials()

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

    if LIVE_MODE:
        log("*** WARNING: LIVE MODE ACTIVE -- REAL MONEY AT RISK ***")
        log("*** Trades will be executed on Bybit ***")
        log("")
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

    # Store start time for status reporting
    write_bot_status._start_time = datetime.now(TZ).isoformat()

    # Write PID file
    try:
        with open("/tmp/live_bot_cascade4.pid", "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    write_bot_status()

    global _current_data
    data = load_data()
    _current_data = data

    last_15m_scan = -1
    last_30m_scan = -1
    last_hourly_status = -1

    # Send startup notification
    send_tg_chris(f"CASCADE4-LIVE Bot gestartet [{mode_label}]\n{len(COINS)} Coins | ${CONFIG['capital']}/Trade | {CONFIG['leverage']}x\nCascade>={CASCADE_MIN} | Phase>={PHASE_ENTRY_MIN_SCORE}")

    while True:
        try:
            now = datetime.now(TZ)
            minute = now.minute
            hour = now.hour

            # Check open trades for TP/SL/Phase/24h every minute
            check_open_trades(data)

            # Check pending confirmations every minute
            if _pending_signals:
                check_pending_confirmations(data)

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

            # Hourly status to Telegram
            if minute == 0 and hour != last_hourly_status:
                last_hourly_status = hour
                send_hourly_status(data)

            # Reload config overrides
            if os.path.exists(_CFG_OVERRIDE):
                try:
                    with open(_CFG_OVERRIDE, "r") as _f:
                        CONFIG.update(json.load(_f))
                except Exception:
                    pass

            # Save periodically
            update_stats(data)
            save_data(data)

            # Write status + positions for dashboard
            write_bot_status()
            write_positions_file()

            # Auto-push to GitHub every 5 minutes
            if minute % 5 == 0:
                try:
                    bot_dir = BASE_DIR
                    subprocess.run(["git", "add", "live_trades_cascade4.json"],
                                   cwd=bot_dir, capture_output=True, timeout=10)
                    subprocess.run(["git", "commit", "-m", f"CASCADE4-LIVE data update ({mode_label})"],
                                   cwd=bot_dir, capture_output=True, timeout=10)
                    subprocess.run(["git", "push"], cwd=bot_dir, capture_output=True, timeout=30)
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
            try:
                with open(STATUS_FILE_PATH, "w") as f:
                    json.dump({"running": False, "mode": "Gestoppt", "last_check": datetime.now(TZ).isoformat()}, f, indent=2)
                os.remove("/tmp/live_bot_cascade4.pid")
            except Exception:
                pass
            send_tg_chris(f"CASCADE4-LIVE Bot gestoppt [{mode_label}]")
            log("Data saved. Goodbye.")
            sys.exit(0)
        except Exception as e:
            log(f"ERROR in main loop: {e}")
            traceback.print_exc()
            time.sleep(30)


if __name__ == "__main__":
    main()
