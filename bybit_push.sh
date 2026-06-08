#!/bin/bash
cd "/Users/Chris/Desktop/Claude - shared/Trading/dashboard"
/usr/local/bin/python3 bybit_sync.py >> /tmp/bybit_sync.log 2>&1
git add bybit_live_data.json live_trades.json paper_trades_koda_se.json 2>/dev/null
if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "sync" >/dev/null 2>&1
    git push >/dev/null 2>&1
fi
