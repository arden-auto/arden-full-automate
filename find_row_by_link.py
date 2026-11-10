"""One-off, READ-ONLY: print every product-tab row whose Supplier URL contains NEEDLE
(e.g. a dead eBay item id). Headers are matched stripped, so a padded header still resolves."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

NEEDLE = os.environ["NEEDLE"].strip()
SHEET_NAME = os.environ["SHEET_NAME"]
TABS = [t.strip() for t in (os.getenv("TABS") or "Sheet1,Amazon").split(",") if t.strip()]

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
book = gspread.authorize(creds).open(SHEET_NAME)
total = 0
for tab in TABS:
    try:
        ws = book.worksheet(tab)
    except gspread.WorksheetNotFound:
        print(f"[{tab}] no such tab - skipped")
        continue
    vals = ws.get_all_values()
    hdr = [h.strip() for h in vals[0]]
    def idx(name):
        return hdr.index(name) if name in hdr else None
    c_sku, c_url = idx("SKU"), idx("Supplier URL")
    c_stock, c_status, c_opc = idx("Stock"), idx("Status"), idx("OPC")
    if c_url is None:
        print(f"[{tab}] no 'Supplier URL' column - skipped")
        continue
    for i, row in enumerate(vals[1:], start=2):
        url = row[c_url] if c_url < len(row) else ""
        if NEEDLE in url:
            total += 1
            def cell(c):
                return (row[c] if c is not None and c < len(row) else "").strip()
            print(f"[{tab}] row {i} | SKU {cell(c_sku)!r} | stock {cell(c_stock)!r} | status {cell(c_status)!r} | OPC {cell(c_opc)!r} | {url[:90]}")
print(f"TOTAL rows matching {NEEDLE!r}: {total}")
