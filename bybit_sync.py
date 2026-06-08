#!/usr/bin/env python3
"""
Bybit Live Data Sync for KODA Dashboard
Fetches account balance, open positions, and closed PnL from Bybit API.
Writes results to bybit_live_data.json for the static dashboard.
"""

import json
import hashlib
import hmac
import time
import ssl
import urllib.request
import urllib.parse
import os
import sys
import certifi
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CRED_FILE = os.path.join(SCRIPT_DIR, "bybit_credentials.json")
OUT_FILE = os.path.join(SCRIPT_DIR, "bybit_live_data.json")
BASE_URL = "https://api.bybit.com"
RECV_WINDOW = "5000"


def load_credentials():
    with open(CRED_FILE, "r") as f:
        creds = json.load(f)
    return creds["api_key"], creds["api_secret"]


def sign_request(api_key, api_secret, timestamp, query_string):
    """HMAC SHA256 signing for Bybit V5 API."""
    sign_str = str(timestamp) + api_key + RECV_WINDOW + query_string
    return hmac.new(
        api_secret.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def api_get(endpoint, params, api_key, api_secret):
    """Authenticated GET request to Bybit V5 API."""
    timestamp = str(int(time.time() * 1000))
    query_string = urllib.parse.urlencode(params) if params else ""
    signature = sign_request(api_key, api_secret, timestamp, query_string)

    url = BASE_URL + endpoint
    if query_string:
        url += "?" + query_string

    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-SIGN": signature,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
    }

    req = urllib.request.Request(url, headers=headers, method="GET")
    ctx = ssl.create_default_context(cafile=certifi.where())

    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("retCode") != 0:
        raise Exception(f"API error {data.get('retCode')}: {data.get('retMsg')}")
    return data.get("result", {})


def fetch_account(api_key, api_secret):
    """Fetch unified account wallet balance."""
    result = api_get(
        "/v5/account/wallet-balance",
        {"accountType": "UNIFIED"},
        api_key,
        api_secret,
    )
    coins_list = result.get("list", [])
    if not coins_list:
        return {"equity": 0, "balance": 0, "available": 0}

    account = coins_list[0]

    def safe_float(val, default=0):
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    return {
        "equity": round(safe_float(account.get("totalEquity")), 2),
        "balance": round(safe_float(account.get("totalWalletBalance")), 2),
        "available": round(safe_float(account.get("totalAvailableBalance")), 2),
    }


def fetch_positions(api_key, api_secret):
    """Fetch open positions (linear USDT perpetuals)."""
    result = api_get(
        "/v5/position/list",
        {"category": "linear", "settleCoin": "USDT"},
        api_key,
        api_secret,
    )
    positions = []
    for p in result.get("list", []):
        size = float(p.get("size", 0))
        if size == 0:
            continue
        coin = p.get("symbol", "").replace("USDT", "")
        positions.append({
            "coin": coin,
            "symbol": p.get("symbol", ""),
            "side": p.get("side", ""),
            "size": p.get("size", "0"),
            "entry": round(float(p.get("avgPrice", 0)), 6),
            "markPrice": round(float(p.get("markPrice", 0)), 6),
            "liqPrice": p.get("liqPrice", ""),
            "upnl": round(float(p.get("unrealisedPnl", 0)), 4),
            "margin": round(float(p.get("positionIM", 0)), 2),
            "leverage": p.get("leverage", "1"),
            "sl": p.get("stopLoss", ""),
            "tp": p.get("takeProfit", ""),
        })
    return positions


def fetch_closed_pnl(api_key, api_secret):
    """Fetch all closed PnL records with pagination."""
    all_records = []
    cursor = ""
    page = 0
    max_pages = 50  # safety limit

    while page < max_pages:
        params = {"category": "linear", "limit": "100"}
        if cursor:
            params["cursor"] = cursor

        result = api_get(
            "/v5/position/closed-pnl",
            params,
            api_key,
            api_secret,
        )
        records = result.get("list", [])
        all_records.extend(records)

        cursor = result.get("nextPageCursor", "")
        if not cursor or len(records) < 100:
            break
        page += 1
        time.sleep(0.2)  # rate limit

    # Calculate stats
    total_pnl = 0.0
    wins = 0
    losses = 0
    for r in all_records:
        pnl = float(r.get("closedPnl", 0))
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

    return {
        "total_trades": len(all_records),
        "total_pnl": round(total_pnl, 4),
        "wins": wins,
        "losses": losses,
    }


def main():
    try:
        api_key, api_secret = load_credentials()
    except Exception as e:
        print(f"ERROR: Could not load credentials: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching Bybit data...")

    try:
        account = fetch_account(api_key, api_secret)
        print(f"  Account: equity=${account['equity']}, balance=${account['balance']}, available=${account['available']}")
    except Exception as e:
        print(f"  ERROR fetching account: {e}", file=sys.stderr)
        account = {"equity": 0, "balance": 0, "available": 0}

    try:
        positions = fetch_positions(api_key, api_secret)
        print(f"  Positions: {len(positions)} open")
    except Exception as e:
        print(f"  ERROR fetching positions: {e}", file=sys.stderr)
        positions = []

    try:
        stats = fetch_closed_pnl(api_key, api_secret)
        print(f"  Closed PnL: {stats['total_trades']} trades, PnL=${stats['total_pnl']}, W/L={stats['wins']}/{stats['losses']}")
    except Exception as e:
        print(f"  ERROR fetching closed PnL: {e}", file=sys.stderr)
        stats = {"total_trades": 0, "total_pnl": 0, "wins": 0, "losses": 0}

    output = {
        "updated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "account": account,
        "positions": positions,
        "stats": stats,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  Written to {OUT_FILE}")


if __name__ == "__main__":
    main()
