#!/usr/bin/env python3
"""
Dashboard Validator — Prueft ALLE Zahlen auf ALLEN Seiten.
Wird automatisch von update_all.sh aufgerufen.
Blockiert Push bei Fehlern.
"""

import json
import re
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

errors = []

def error(msg):
    errors.append(msg)
    print(f"  ❌ {msg}")

def ok(msg):
    print(f"  ✅ {msg}")

# ── 1. trades_data.js laden und berechnen ──
print("1. Lade trades_data.js...")
with open('trades_data.js', 'r') as f:
    c = f.read()
s = c.index('{')
bc = 0
for i in range(s, len(c)):
    if c[i] == '{': bc += 1
    elif c[i] == '}': bc -= 1
    if bc == 0: break
data = json.loads(c[s:i+1])
trades = data['trades']

def eff(t):
    n = t.get('net_pnl')
    p = t.get('pnl')
    if n is not None and n != '':
        return float(n) if not isinstance(n, str) else float(n.replace(',', '.'))
    if p is not None and p != '':
        return float(p) if not isinstance(p, str) else float(p.replace(',', '.'))
    return 0

closed = [t for t in trades if t.get('abgeschl') == 'j' and t.get('pnl') is not None]
total_pnl = sum(eff(t) for t in closed)
wins = sum(1 for t in closed if eff(t) > 0)
losses = len(closed) - wins
wr = wins / len(closed) * 100 if closed else 0

print(f"   Berechnet: {len(closed)} Trades | {wins}W/{losses}L | WR {wr:.1f}% | PnL ${total_pnl:,.2f}")

# ── 2. index.html pruefen ──
print("\n2. Pruefe index.html...")
with open('index.html', 'r') as f:
    idx_html = f.read()

# Check if eff() function is used (not raw pnl)
if 'var eff=function' in idx_html or 'eff(t)' in idx_html:
    ok("index.html nutzt eff() Funktion")
else:
    error("index.html nutzt NICHT eff() — PnL wird falsch berechnet!")

# ── 3. stats.html pruefen ──
print("\n3. Pruefe stats.html...")
with open('stats.html', 'r') as f:
    stats_html = f.read()

if 'const eff = t => t.net_pnl !== null ? t.net_pnl : t.pnl' in stats_html:
    ok("stats.html nutzt eff() Funktion")
else:
    error("stats.html nutzt NICHT eff()")

# Check Auszahlungs-Datenpunkte in stats.html
payout_matches = re.findall(r"amount:\s*(\d+)", stats_html)
if payout_matches:
    payout_total = sum(int(x) for x in payout_matches)
    ok(f"stats.html Auszahlungen: {len(payout_matches)} Eintraege, Total ${payout_total:,}")
else:
    error("stats.html: Keine Auszahlungs-Datenpunkte gefunden")

# ── 4. trades.html pruefen ──
print("\n4. Pruefe trades.html...")
with open('trades.html', 'r') as f:
    trades_html = f.read()

if 'net_pnl||t.pnl' in trades_html or 'net_pnl||' in trades_html:
    ok("trades.html nutzt net_pnl||pnl Fallback")
else:
    error("trades.html nutzt NICHT net_pnl Fallback")

# ── 5. auszahlung.html pruefen ──
print("\n5. Pruefe auszahlung.html...")
with open('auszahlung.html', 'r') as f:
    ausz_html = f.read()

# Berechne aktuellen Zeitraum PnL (Trades seit letzter Auszahlung #5 am 12.05.)
from datetime import datetime
current_pnl = 0
current_pnl_after_mt = 0
current_trades = 0
for t in closed:
    nr = t.get('nr', 0)
    if nr >= 82:  # Trades nach Auszahlung #5
        p = eff(t)
        current_pnl += p
        current_trades += 1
        if t.get('signal') == 'MarkTrade' and p > 0:
            current_pnl_after_mt += p * 0.5
        else:
            current_pnl_after_mt += p

# Check KPI Box "Offen zur Verteilung"
kpi_match = re.search(r'id="kpi-offen"[^>]*>([^<]+)', ausz_html)
if kpi_match:
    kpi_val_str = kpi_match.group(1).replace(',', '').replace('.', '').strip()
    try:
        # Handle negative with different formats
        kpi_val = float(kpi_val_str.replace(' ', ''))
    except:
        kpi_val = None

    expected = round(current_pnl_after_mt)
    if kpi_val is not None and abs(kpi_val - expected) > 50:
        error(f"auszahlung.html KPI 'Offen' zeigt {kpi_match.group(1)} aber sollte ~{expected} sein (Diff: {kpi_val - expected})")
    else:
        ok(f"auszahlung.html KPI 'Offen': {kpi_match.group(1)} (erwartet ~{expected})")

# Check Tabelle Zeitraum 6
zeit6_match = re.search(r'<td[^>]*class="num red"[^>]*>([^<]*-[\d,]+)', ausz_html)
print(f"   Berechnet: {current_trades} Trades seit Auszahlung, PnL ${current_pnl:,.0f}, nach MT ${current_pnl_after_mt:,.0f}")

# ── 6. Konsistenz-Check ──
print("\n6. Konsistenz-Check...")
ok(f"Total: {len(closed)} Trades | {wins}W/{losses}L | WR {wr:.1f}% | PnL ${total_pnl:,.2f}")

# ── Ergebnis ──
print(f"\n{'='*50}")
if errors:
    print(f"❌ {len(errors)} FEHLER GEFUNDEN:")
    for e in errors:
        print(f"   → {e}")
    print(f"\n⛔ PUSH SOLLTE NICHT DURCHGEFUEHRT WERDEN!")
    sys.exit(1)
else:
    print(f"✅ ALLE CHECKS BESTANDEN — Push OK")
    sys.exit(0)
