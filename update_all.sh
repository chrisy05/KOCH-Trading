#!/bin/bash
# ═══════════════════════════════════════════════════════
# KOCH Trading Dashboard — Komplettes Update
# Führt ALLE Schritte in einem Durchlauf aus:
# 1. Daten aus Excel + extra_trades.json generieren
# 2. Lokales Backup aktualisieren
# 3. Git commit + push
# ═══════════════════════════════════════════════════════

cd "$(dirname "$0")"
export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"
BACKUP="../docs/dashboard_backup"

echo "═══ KOCH Dashboard Update ═══"
echo ""

# 1. Daten generieren
echo "1. Generiere trades_data.js aus Excel..."
python3 build_data.py
if [ $? -ne 0 ]; then
    echo "FEHLER: build_data.py fehlgeschlagen!"
    exit 1
fi
echo ""

# 2. Backup
echo "2. Lokales Backup..."
for f in index.html trades.html stats.html bot.html koda.html auszahlung.html trades_data.js; do
    [ -f "$f" ] && cp "$f" "$BACKUP/$f"
done
echo "   Backup → $BACKUP/"
echo ""

# 3. Git
echo "3. Git commit + push..."
git add -A
CHANGES=$(git diff --cached --stat)
if [ -z "$CHANGES" ]; then
    echo "   Keine Änderungen."
    exit 0
fi
echo "$CHANGES"
git commit -m "Dashboard Update $(date '+%d.%m.%Y %H:%M')

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
git push origin main

echo ""
echo "═══ Fertig! Alle Daten konsistent. ═══"
