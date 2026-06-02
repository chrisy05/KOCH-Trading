#!/usr/bin/env python3
"""Paper Bot Health Check — runs hourly, checks bot is alive and data is consistent."""
import json, os, subprocess, time, ssl, urllib.request
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=-4))
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trades.json")
BOT_TOKEN_FILE = "/Users/Chris/.claude/channels/telegram-2/.env"
CHAT_ID = "351653518"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def get_token():
    with open(BOT_TOKEN_FILE) as f:
        for line in f:
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                return line.strip().split("=",1)[1]
    return None

def send_tg(text):
    token = get_token()
    if not token: return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({"chat_id": CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10, context=ctx)
    except:
        pass

def check():
    now = datetime.now(TZ)
    issues = []

    # 1. Bot process running?
    r = subprocess.run(["pgrep", "-f", "paper_bot.py"], capture_output=True, text=True)
    if not r.stdout.strip():
        issues.append("Bot Prozess GESTOPPT!")
        # Try to restart
        subprocess.Popen(
            ["python3", "paper_bot.py"],
            cwd=os.path.dirname(DATA),
            stdout=open("/tmp/paper_bot.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
        issues.append("Neustart versucht.")

    # 2. Data file fresh? (should be updated every minute)
    if os.path.exists(DATA):
        mtime = os.path.getmtime(DATA)
        age_min = (time.time() - mtime) / 60
        if age_min > 5:
            issues.append(f"JSON {age_min:.0f}min alt (sollte <2min sein)")
    else:
        issues.append("paper_trades.json fehlt!")

    # 3. Check for trades that should be liquidated
    if os.path.exists(DATA):
        with open(DATA) as f:
            d = json.load(f)
        for key in ["trades_15m", "trades_1h"]:
            for t in d.get(key, []):
                if t["status"] != "open": continue
                sym = t["coin"] + "USDT"
                try:
                    req = urllib.request.Request(
                        f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}",
                        headers={"User-Agent": "Health/1.0"})
                    with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                        price = float(json.loads(resp.read().decode())["price"])
                    if t["direction"] == "LONG" and price <= t["liq"]:
                        issues.append(f"{t['coin']} LONG sollte liquidiert sein! Preis {price} <= Liq {t['liq']}")
                    elif t["direction"] == "SHORT" and price >= t["liq"]:
                        issues.append(f"{t['coin']} SHORT sollte liquidiert sein! Preis {price} >= Liq {t['liq']}")
                except:
                    pass
                time.sleep(0.05)

    if issues:
        send_tg(f"⚠️ Paper Bot Health Check {now.strftime('%H:%M')}:\n" + "\n".join(f"• {i}" for i in issues))
        print(f"[{now.strftime('%H:%M')}] ISSUES: {issues}")
    else:
        print(f"[{now.strftime('%H:%M')}] OK — Bot laeuft, Daten frisch, keine Liq-Probleme")

if __name__ == "__main__":
    check()
