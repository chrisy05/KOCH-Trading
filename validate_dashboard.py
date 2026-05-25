#!/usr/bin/env python3
"""
Dashboard Validator — Prueft ALLES auf ALLEN Seiten.
Wird automatisch von update_all.sh aufgerufen.
Blockiert Push bei Fehlern.
"""

import json
import re
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))

errors = []
warnings = []

def error(msg):
    errors.append(msg)
    print(f"  ❌ {msg}")

def warn(msg):
    warnings.append(msg)
    print(f"  ⚠️ {msg}")

def ok(msg):
    print(f"  ✅ {msg}")

# ══════════════════════════════════════════════════════════════
# 1. TRADES DATEN LADEN
# ══════════════════════════════════════════════════════════════
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

print(f"   {len(closed)} Trades | {wins}W/{losses}L | WR {wr:.1f}% | PnL ${total_pnl:,.2f}")

# ══════════════════════════════════════════════════════════════
# 2. EINZELNE TRADES PRUEFEN
# ══════════════════════════════════════════════════════════════
print("\n2. Pruefe einzelne Trades...")
for t in closed:
    nr = t.get('nr', '?')
    coin = t.get('coin', '?')
    richtung = t.get('richtung', '?')
    entry = t.get('entry', '')
    ausstieg = t.get('ausstieg', '')
    pnl = eff(t)
    datum = t.get('datum', '')
    signal = t.get('signal', '')

    # Pflichtfelder
    if not coin or coin == '?':
        error(f"#{nr}: Coin fehlt")
    richtung_norm = richtung.upper().strip() if richtung else ''
    if richtung_norm not in ('L', 'S', 'LONG', 'SHORT', ''):
        if nr > 10:
            warn(f"#{nr} {coin}: Richtung '{richtung}' ungewoehnlich")
    if not datum and coin != 'Versch':
        error(f"#{nr} {coin}: Datum fehlt")
    if not signal:
        warn(f"#{nr} {coin}: Signal fehlt")

    # PnL Plausibilitaet
    if entry and ausstieg and richtung_norm in ('L', 'S', 'LONG', 'SHORT'):
        try:
            e = float(str(entry).replace(',', '.'))
            a = float(str(ausstieg).replace(',', '.'))
            is_long = richtung_norm in ('L', 'LONG')
            # Toleranz fuer Fee-bedingte Verluste (kleiner PnL bei richtiger Richtung)
            if is_long and pnl > 10 and a < e * 0.99:
                error(f"#{nr} {coin}: LONG mit Gewinn aber Exit ({a}) < Entry ({e})")
            if is_long and pnl < -10 and a > e * 1.01:
                error(f"#{nr} {coin}: LONG mit Verlust aber Exit ({a}) > Entry ({e})")
            if not is_long and pnl > 10 and a > e * 1.01:
                error(f"#{nr} {coin}: SHORT mit Gewinn aber Exit ({a}) > Entry ({e})")
            if not is_long and pnl < -10 and a < e * 0.99:
                error(f"#{nr} {coin}: SHORT mit Verlust aber Exit ({a}) < Entry ({e})")
        except (ValueError, TypeError):
            pass

    # Doppelte Trade-Nummern
    nrs = [t.get('nr') for t in closed if t.get('nr') is not None]
    if len(nrs) != len(set(nrs)):
        dupes = [n for n in nrs if nrs.count(n) > 1]
        error(f"Doppelte Trade-Nummern: {set(dupes)}")

# Fortlaufende Nummern
sorted_nrs = sorted([t.get('nr', 0) for t in closed if t.get('nr')])
for i in range(1, len(sorted_nrs)):
    if sorted_nrs[i] != sorted_nrs[i-1] + 1:
        warn(f"Luecke in Trade-Nummern: #{sorted_nrs[i-1]} → #{sorted_nrs[i]}")

trade_check_ok = len([e for e in errors if '#' in e]) == 0
if trade_check_ok:
    ok(f"Alle {len(closed)} Trades plausibel")

# ══════════════════════════════════════════════════════════════
# 3. INDEX.HTML
# ══════════════════════════════════════════════════════════════
print("\n3. Pruefe index.html...")
with open('index.html', 'r') as f:
    idx_html = f.read()

if 'var eff=function' in idx_html or 'eff(t)' in idx_html:
    ok("eff() Funktion vorhanden")
else:
    error("index.html nutzt NICHT eff()")

# ══════════════════════════════════════════════════════════════
# 4. STATS.HTML
# ══════════════════════════════════════════════════════════════
print("\n4. Pruefe stats.html...")
with open('stats.html', 'r') as f:
    stats_html = f.read()

if 'const eff = t => t.net_pnl !== null ? t.net_pnl : t.pnl' in stats_html:
    ok("eff() Funktion vorhanden")
else:
    error("stats.html nutzt NICHT eff()")

payout_matches = re.findall(r"amount:\s*(\d+)", stats_html)
if payout_matches:
    ok(f"Auszahlungen: {len(payout_matches)} Eintraege, ${sum(int(x) for x in payout_matches):,}")
else:
    error("Keine Auszahlungs-Datenpunkte")

# ══════════════════════════════════════════════════════════════
# 5. TRADES.HTML
# ══════════════════════════════════════════════════════════════
print("\n5. Pruefe trades.html...")
with open('trades.html', 'r') as f:
    trades_html = f.read()

if 'net_pnl||t.pnl' in trades_html or 'net_pnl||' in trades_html:
    ok("net_pnl||pnl Fallback vorhanden")
else:
    error("trades.html nutzt NICHT net_pnl Fallback")

# ══════════════════════════════════════════════════════════════
# 6. AUSZAHLUNG.HTML
# ══════════════════════════════════════════════════════════════
print("\n6. Pruefe auszahlung.html...")
with open('auszahlung.html', 'r') as f:
    ausz_html = f.read()

# Berechne aktuellen Zeitraum
current_pnl = 0
current_pnl_after_mt = 0
current_trades = 0
for t in closed:
    nr = t.get('nr', 0)
    if nr >= 82:
        p = eff(t)
        current_pnl += p
        current_trades += 1
        if t.get('signal') == 'MarkTrade' and p > 0:
            current_pnl_after_mt += p * 0.5
        else:
            current_pnl_after_mt += p

# Check KPI Box
kpi_match = re.search(r'id="kpi-offen"[^>]*>([^<]+)', ausz_html)
if kpi_match:
    kpi_val_str = kpi_match.group(1).replace(',', '').replace('.', '').strip()
    try:
        kpi_val = float(kpi_val_str.replace(' ', ''))
    except:
        kpi_val = None

    # KPI Offen = raw PnL (MT-Abzug ist nur informativ, nicht in Verteilung)
    expected = round(current_pnl)
    if kpi_val is not None and abs(kpi_val - expected) > 50:
        error(f"KPI 'Offen' zeigt {kpi_match.group(1)} aber sollte ~{expected} sein")
    else:
        ok(f"KPI 'Offen': {kpi_match.group(1)} (erwartet ~{expected})")

print(f"   {current_trades} Trades seit Auszahlung, PnL ${current_pnl:,.0f}, nach MT ${current_pnl_after_mt:,.0f}")

# ══════════════════════════════════════════════════════════════
# 7. EXTRA_TRADES.JSON INTEGRITAET
# ══════════════════════════════════════════════════════════════
print("\n7. Pruefe extra_trades.json...")
try:
    with open('extra_trades.json', 'r') as f:
        extra = json.load(f)
    ok(f"JSON gueltig, {len(extra)} Trades")

    for t in extra:
        nr = t.get('nr', '?')
        if t.get('pnl') is not None and t.get('net_pnl') is None:
            warn(f"#{nr}: pnl gesetzt aber net_pnl fehlt")
        if t.get('net_pnl') is not None and t.get('pnl') is not None:
            pnl = float(t['pnl']) if not isinstance(t['pnl'], str) else float(t['pnl'].replace(',','.'))
            net = float(t['net_pnl']) if not isinstance(t['net_pnl'], str) else float(t['net_pnl'].replace(',','.'))
            if abs(pnl - net) > 0.01:
                warn(f"#{nr}: pnl ({pnl}) != net_pnl ({net})")
except json.JSONDecodeError as e:
    error(f"extra_trades.json ist KEIN gueltiges JSON: {e}")
except FileNotFoundError:
    warn("extra_trades.json nicht gefunden")

# ══════════════════════════════════════════════════════════════
# 8. ALLE HTML DATEIEN VORHANDEN
# ══════════════════════════════════════════════════════════════
print("\n8. Pruefe Dateien...")
required = ['index.html', 'stats.html', 'trades.html', 'auszahlung.html',
            'trades_data.js', 'build_data.py', 'extra_trades.json',
            'kapitalfluss.html', 'mtf_scan.html', 'cockpit.html', 'kalender.html',
            'tracker.html', 'kalkulator.html', 'technik.html', 'looking_glass.html']
for f in required:
    if os.path.exists(f):
        ok(f"{f} vorhanden")
    else:
        error(f"{f} FEHLT!")

# Looking Glass Archiv Check
archive_path = os.path.join('..', 'signals', 'looking_glass_archive.json')
if os.path.exists(archive_path):
    try:
        with open(archive_path) as af:
            archive = json.load(af)
        ok(f"Looking Glass Archiv: {len(archive)} Eintraege")
    except Exception:
        warn("Looking Glass Archiv nicht lesbar")

# Looking Glass Resultate nachtragen Check
lg_data_path = 'looking_glass_data.json'
if os.path.exists(lg_data_path):
    with open(lg_data_path) as lf:
        lg = json.load(lf)
    pending = sum(1 for e in lg if e.get("direction", "NEUTRAL") != "NEUTRAL" and "result" not in e)
    missing_r1h = sum(1 for e in lg if e.get("direction", "NEUTRAL") != "NEUTRAL" and "r1h" not in e)
    if pending > 10:
        warn(f"Looking Glass: {pending} Eintraege ohne Ergebnis — nachtragen!")
    if missing_r1h > 20:
        warn(f"Looking Glass: {missing_r1h} Eintraege ohne 1h Bewertung")

# ══════════════════════════════════════════════════════════════
# ERGEBNIS
# ══════════════════════════════════════════════════════════════
print(f"\n{'='*50}")
if errors:
    print(f"❌ {len(errors)} FEHLER:")
    for e in errors:
        print(f"   → {e}")
    print(f"\n⛔ PUSH BLOCKIERT!")
    sys.exit(1)
else:
    if warnings:
        print(f"⚠️ {len(warnings)} Warnungen (Push erlaubt):")
        for w in warnings:
            print(f"   → {w}")
    print(f"\n✅ ALLE CHECKS BESTANDEN — Push OK")
    sys.exit(0)
