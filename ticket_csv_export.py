"""One-off, READ-ONLY: build the price/stock CSV OnBuy asked for (ticket reply
2026-11-10) to repair the 323 'Price below minimum' suspended listings.

Rules (user policy + 2026-11-06 plan):
- SKU with a sheet row + Supplier URL + real price:
    eBay row  -> sheet price; stock = sheet stock while Status is ACTIVE, else 0
    other supplier (AliExpress - token dead, prices ~6 weeks old) -> sheet price, STOCK 0
      (a stale price must never become a sellable offer; stock 0 only lifts the suspension)
- row without a usable price/link, or no row at all -> price 9.99, STOCK 0
  (lifts the suspension so the API can touch the listing again; NOT-ON-SHEET ones are
   the delete-after-unsuspension candidates - the user decides)
Writes ticket_price_stock.csv (SKU,Price,Stock) as an artifact. No OnBuy calls, no sheet writes.
"""
import csv
import io
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import sheet_tabs

CSV_PATH = os.getenv("CSV_PATH") or "suspended_arden_2026-11-06.csv"
SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
MIN_PRICE = 1.00
FALLBACK_PRICE = 9.99


def fnum(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


sus = list(csv.DictReader(io.open(CSV_PATH, encoding="utf-8-sig", newline="")))
print(f"suspended export rows: {len(sus)}")

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
book = gspread.authorize(creds).open(SHEET_NAME)
tabs = [sheet_tabs.product_sheet(book)]
try:
    amz = book.worksheet(sheet_tabs.AMAZON_TAB)
    if amz.title != tabs[0].title:
        tabs.append(amz)
except gspread.exceptions.WorksheetNotFound:
    pass
by_sku = {}
for ws in tabs:
    for r in ws.get_all_records():
        key = str(r.get("SKU") or "").replace(",", "").strip()
        if key and key not in by_sku:
            by_sku[key] = r
print(f"sheet SKUs: {len(by_sku)}")
core = {}
for k in by_sku:
    core.setdefault(k.lstrip("0") or k, []).append(k)

out, classes = [], {"EBAY": 0, "OTHER-SUPPLIER-STOCK0": 0, "NO-SOURCE": 0, "NOT-ON-SHEET": 0}
detail = {"OTHER-SUPPLIER-STOCK0": [], "NO-SOURCE": [], "NOT-ON-SHEET": []}
seen = set()
for r in sus:
    sku = (r.get("sku") or r.get("SKU") or "").strip()
    if not sku or sku in seen:
        continue
    seen.add(sku)
    srow = by_sku.get(sku)
    if srow is None:
        cands = core.get(sku.lstrip("0") or sku) or []
        if len(cands) == 1:
            srow = by_sku[cands[0]]
    if srow is None:
        out.append((sku, FALLBACK_PRICE, 0)); classes["NOT-ON-SHEET"] += 1
        detail["NOT-ON-SHEET"].append(sku)
        continue
    url = str(srow.get("Supplier URL") or "").strip().lower()
    price = fnum(srow.get("Selling Price (\u00a3)"))
    if not url or price < MIN_PRICE:
        out.append((sku, FALLBACK_PRICE, 0)); classes["NO-SOURCE"] += 1
        detail["NO-SOURCE"].append(sku)
        continue
    if "ebay." in url:
        status = str(srow.get("Status") or "").strip().upper()
        stock = int(fnum(srow.get("Stock"))) if status.startswith("ACTIVE") else 0
        out.append((sku, round(price, 2), max(stock, 0))); classes["EBAY"] += 1
    else:
        out.append((sku, round(price, 2), 0)); classes["OTHER-SUPPLIER-STOCK0"] += 1
        detail["OTHER-SUPPLIER-STOCK0"].append(sku)

with open("ticket_price_stock.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["SKU", "Price", "Stock"])
    w.writerows(out)
print(f"CSV rows written: {len(out)}")
print("classes:", classes)
in_stock = sum(1 for _, _, st in out if st > 0)
print(f"rows with stock > 0: {in_stock}")
for k, v in detail.items():
    for s in v:
        print(f"  {k} {s}")
