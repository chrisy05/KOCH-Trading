#!/bin/bash
# auto_sync.sh — Rebuild data from Excel + sync to GitHub
# Runs build_data.py, stages all dashboard files, commits + pushes

cd "/Users/Chris/Desktop/Claude - shared/Trading/dashboard"

# Step 1: Rebuild trades_data.js from Excel + extra_trades
/usr/local/bin/python3 build_data.py >> /tmp/auto_sync.log 2>&1

# Step 2: Also run bybit_sync if it exists
if [ -f bybit_sync.py ]; then
    /usr/local/bin/python3 bybit_sync.py >> /tmp/auto_sync.log 2>&1
fi

# Step 3: Stage all important files
git add \
    trades_data.js \
    extra_trades.json \
    paper_trades_cascade4.json \
    paper_trades_koda_se.json \
    bot.html \
    live_trades*.json \
    bybit_live_data.json \
    auszahlung.html \
    tracker.html \
    2>/dev/null

# Step 4: Commit only if there are staged changes
if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "auto-sync $(date '+%Y-%m-%d %H:%M')" >/dev/null 2>&1
    git push >/dev/null 2>&1
fi
