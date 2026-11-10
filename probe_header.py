"""One-off, READ-ONLY: print row 1 of a tab EXACTLY as stored (repr per cell) -
get_all_records keys rows by the RAW header text, so an invisible character in
a header cell silently blanks dict reads; col_map['SKU'] then raises KeyError.
Also counts OPC-seeded rows (OPC set, SKU empty) on the tab."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
TAB = os.getenv("TAB") or "Sheet1"

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
book = gspread.authorize(creds).open(SHEET_NAME)
print("tabs:", [w.title for w in book.worksheets()])
ws = book.worksheet(TAB)
print(f"tab {TAB!r}: {ws.row_count} grid rows")
raw = ws.get_values("A1:AZ1")
row = raw[0] if raw else []
print(f"row 1 has {len(row)} cell(s)")
for i, c in enumerate(row, start=1):
    print(f"col {i}: {c!r}")
rec = ws.get_all_records(head=1)
print("data rows:", len(rec))
if rec:
    keys = list(rec[0].keys())
    print("get_all_records keys:", [repr(k) for k in keys][:40])
    r0 = rec[0]
    print("first row SKU/OPC via dict:", repr(r0.get("SKU")), repr(r0.get("OPC")))
    seeded = [r for r in rec if str(r.get("OPC", "")).strip() and not str(r.get("SKU", "")).strip()]
    print("OPC set + SKU empty (dict view):", len(seeded))
    for r in seeded[:3]:
        print("  sample seeded row:", {k: r.get(k) for k in list(keys)[:8]})
