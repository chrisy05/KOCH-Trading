#!/bin/bash
# ═══════════════════════════════════════════════════════
# KOCH Trading Dashboard — Komplettes Update
# Führt ALLE Schritte in einem Durchlauf aus:
# 1. Daten aus Excel + extra_trades.json generieren
# 2. Lokales Backup aktualisieren
# 3. Git commit + push
# ═══════════════════════════════════════════════════════

cd "$(dirname "$0")"
export PATH="/Library/Frameworks/Python.framework/Versions/3.14/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
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

# 1b. Bybit Live Data Sync
echo "1b. Bybit Live Data Sync..."
python3 bybit_sync.py 2>&1 || echo "  Warnung: bybit_sync.py fehlgeschlagen"
echo ""

# 2. Backup
echo "2. Lokales Backup..."
for f in index.html trades.html stats.html bot.html koda.html auszahlung.html trades_data.js bybit_live_data.json; do
    [ -f "$f" ] && cp "$f" "$BACKUP/$f"
done
echo "   Backup → $BACKUP/"
echo ""

# 3. Validierung
echo "3. Validiere Dashboard..."
python3 validate_dashboard.py
if [ $? -ne 0 ]; then
    echo "⛔ VALIDIERUNG FEHLGESCHLAGEN — Push abgebrochen!"
    exit 1
fi
echo ""

# 4. Code Backup (letzte 5 Versionen als Word)
echo "4. Code Backup..."
BACKUP_DIR="../docs/dashboard_code_versions"
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date '+%Y%m%d_%H%M')
python3 -c "
from docx import Document
from docx.shared import Pt
import os, glob

doc = Document()
doc.styles['Normal'].font.name = 'Consolas'
doc.styles['Normal'].font.size = Pt(8)
doc.add_heading('Dashboard Code — $TIMESTAMP', level=0)
for f in ['index.html','stats.html','trades.html','auszahlung.html','build_data.py','update_all.sh','validate_dashboard.py','extra_trades.json']:
    try:
        with open(f,'r') as fh: code = fh.read()
        doc.add_heading(f, level=1)
        doc.add_paragraph(code[:50000])
    except: pass
doc.save('$BACKUP_DIR/dashboard_code_$TIMESTAMP.docx')

# Nur letzte 5 behalten
files = sorted(glob.glob('$BACKUP_DIR/dashboard_code_*.docx'))
for old in files[:-5]:
    os.remove(old)
print(f'  Backup: dashboard_code_$TIMESTAMP.docx ({len(files)} Versionen, max 5)')
" 2>/dev/null || echo "  Backup-Warnung: docx nicht erstellt"
echo ""

# 5. Git
echo "5. Git commit + push..."
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
