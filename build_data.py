#!/usr/bin/env python3
"""
Liest TOCH_Trading_2026_v2.1.xlsx und erzeugt trades_data.js
Alle Dashboard-Seiten (index, trades, stats) laden diese eine Datei.
Aufruf: python3 build_data.py
"""
import json
import openpyxl
import os
from datetime import datetime

EXCEL = os.path.join(os.path.dirname(__file__), "TOCH_Trading_2026_v2.1.xlsx")
OUTPUT = os.path.join(os.path.dirname(__file__), "trades_data.js")

def fmt_duration(val):
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s == "None":
        return ""
    return s

def safe_float(val):
    if val is None:
        return None
    try:
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).replace(",", ".").strip()
        if not s or s == "None" or s == "nicht getradet":
            return None
        return float(s)
    except:
        return None

def safe_str(val):
    if val is None:
        return ""
    return str(val).strip()

def fmt_date(val):
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%d.%m.%y")
    s = str(val).strip()
    # Normalize date formats
    if "00:00:00" in s:
        s = s.split(" ")[0]
    return s

def main():
    wb = openpyxl.load_workbook(EXCEL, data_only=True)
    ws = wb["Trades"]

    trades = []
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True):
        nr = row[0]
        if nr is None:
            continue
        try:
            nr = int(nr)
        except:
            continue

        t = {
            "nr": nr,
            "signal": safe_str(row[1]),
            "datum": fmt_date(row[2]),
            "zeit": safe_str(row[3]),
            "account": safe_str(row[4]),
            "coin": safe_str(row[5]),
            "richtung": safe_str(row[6]),
            "kapital": safe_float(row[7]),
            "einsatz": safe_str(row[8]),
            "hebel": safe_str(row[9]),
            "pct_kapital": safe_str(row[10]),
            "geh_einsatz": safe_float(row[11]),
            "entry": safe_str(row[12]),
            "entry2": safe_str(row[13]),
            "entry3": safe_str(row[14]),
            "tp1": safe_str(row[15]),
            "tp2": safe_str(row[16]),
            "tp3": safe_str(row[17]),
            "sl": safe_str(row[18]),
            "ausstieg_dat": fmt_date(row[19]),
            "ausstieg_zeit": safe_str(row[20]),
            "ausstieg": safe_str(row[21]),
            "dauer": fmt_duration(row[22]),
            "roi": safe_float(row[23]),
            "pnl": safe_float(row[24]),
            "net_pnl": safe_float(row[25]),
            "abgeschl": safe_str(row[26]),
            "notiz": safe_str(row[27]) if len(row) > 27 else ""
        }
        trades.append(t)

    # Statistik-Sheet: Einzahlungen und Kontostände
    ws_stat = wb["Statistik"]
    einzahlungen = []
    kontostand = []

    in_einzahlungen = False
    for row in ws_stat.iter_rows(min_row=1, max_row=ws_stat.max_row, values_only=True):
        r0 = safe_str(row[0]) if row[0] else ""
        if "EINZAHLUNGEN" in r0.upper():
            in_einzahlungen = True
            continue
        if "TOTAL" in r0.upper() and in_einzahlungen:
            in_einzahlungen = False
            continue
        if in_einzahlungen and row[0] and row[1]:
            s = safe_float(row[1])
            ini = safe_str(row[2])
            if s and ini in ("TR", "GT"):
                einzahlungen.append({
                    "datum": fmt_date(row[0]),
                    "summe": s,
                    "initialen": ini
                })

    # Trades die nur im Dashboard existieren (nicht in Excel)
    # Diese werden manuell gepflegt bis Excel aktualisiert wird
    EXTRA_FILE = os.path.join(os.path.dirname(__file__), "extra_trades.json")
    if os.path.exists(EXTRA_FILE):
        with open(EXTRA_FILE, "r") as ef:
            extra = json.load(ef)
            trades.extend(extra)
            print(f"  Extra trades loaded: {len(extra)}")

    data = {
        "trades": trades,
        "einzahlungen": einzahlungen,
        "generated": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "total_einzahlungen": sum(e["summe"] for e in einzahlungen)
    }

    js = "// Auto-generated from TOCH_Trading_2026_v2.1.xlsx\n"
    js += "// Generated: " + data["generated"] + "\n"
    js += "const TRADE_DATA = " + json.dumps(data, ensure_ascii=False, indent=None) + ";\n"

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(js)

    print(f"Generated {OUTPUT}")
    print(f"  Trades: {len(trades)}")
    print(f"  Einzahlungen: {len(einzahlungen)} ({data['total_einzahlungen']} USDT)")
    print(f"  Stand: {data['generated']}")

if __name__ == "__main__":
    main()
