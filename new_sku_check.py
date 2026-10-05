"""READ-ONLY (2026-10-05): are the SKUs the team just typed into rows that had a supplier link but no SKU usable?

Rows with a Supplier URL but no SKU are skipped by every sync until a SKU is typed. Once SKUs are in, the next syncs create
those products - and a bad barcode only shows up as a "Failed" status hours later. This reads each product tab (the first
product tab and the Amazon tab) and reports:

  - rows that still have a Supplier URL but NO SKU
  - rows with a SKU the sync has never processed (Last Checked Time blank): how many, and how many are
      * not a usable barcode (the digits are not a GS1-valid 8/12/13/14-digit code, or fall in the coupon range)
      * sharing their digits with another row (one barcode = one product), or the exact same SKU on two rows
      * on a supplier link that an earlier row already owns (the sync would freeze them as duplicates)
      * with no supplier link yet, or a link with no recognisable eBay item / ASIN
Counts and sample SKUs only (never a cost); the full list goes to OUT_DIR/new_sku_problems.csv for the team.
Writes nothing to the sheet, Supabase or OnBuy.
"""
import csv
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import sheet_tabs
from generate_xml import _supplier_identity, is_valid_gtin, sku_numeric_part
from retry_utils import with_retry

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
OUT_DIR = os.getenv("OUT_DIR") or "out"


def classify(rows):
    """rows: dicts tab,row,sku,url,checked. -> (problems [(row dict, reason)], summary counters)."""
    by_digits, by_sku, by_ident = {}, {}, {}
    for r in rows:
        if r["sku"]:
            by_digits.setdefault(sku_numeric_part(r["sku"]), []).append(r)
            by_sku.setdefault(r["sku"], []).append(r)
        if r["ident"]:
            by_ident.setdefault(r["ident"], []).append(r)
    problems = []
    for r in rows:
        if not r["sku"] or r["checked"]:
            continue                                   # not a SKU that was waiting for its first sync
        digits = sku_numeric_part(r["sku"])
        if not is_valid_gtin(digits):
            problems.append((r, "not a usable barcode (digits fail the GS1 check, wrong length or coupon range)"))
        if len(by_digits.get(digits, [])) > 1:
            others = [f"{x['tab']} row {x['row']}" for x in by_digits[digits] if x is not r][:3]
            problems.append((r, "same barcode digits as " + ", ".join(others)))
        if len(by_sku.get(r["sku"], [])) > 1:
            problems.append((r, "the exact same SKU is on more than one row"))
        if not r["url"]:
            problems.append((r, "no supplier link yet"))
        elif not r["ident"]:
            problems.append((r, "supplier link has no recognisable eBay item or Amazon ASIN"))
        elif by_ident[r["ident"]][0] is not r:
            first = by_ident[r["ident"]][0]
            problems.append((r, f"supplier product already owned by {first['tab']} row {first['row']}"))
    return problems


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME), what="sheet open", max_attempts=3)
    tabs = [sheet_tabs.product_sheet(book)]
    try:
        amz = book.worksheet(sheet_tabs.AMAZON_TAB)
        if amz.title != tabs[0].title:
            tabs.append(amz)
    except gspread.exceptions.WorksheetNotFound:
        pass

    rows, no_sku = [], []
    for ws in tabs:
        values = with_retry(lambda ws=ws: ws.get_all_values(), what=f"read {ws.title}", max_attempts=3)
        header = [str(h).strip() for h in values[0]]
        ix = {h: i for i, h in enumerate(header) if h}

        def cell(r, col):
            i = ix.get(col)
            return str(r[i]).strip() if i is not None and i < len(r) else ""
        for n, r in enumerate(values[1:], start=2):
            sku = cell(r, "SKU").replace(",", "").strip()
            url = cell(r, "Supplier URL")
            if not sku and not url:
                continue
            item = {"tab": ws.title, "row": n, "sku": sku, "url": url, "ident": _supplier_identity(url),
                    "checked": cell(r, "Last Checked Time"), "status": cell(r, "Sync Status")[:60]}
            rows.append(item)
            if url and not sku:
                no_sku.append(item)
        print(f"tab {ws.title!r}: {sum(1 for x in rows if x['tab'] == ws.title)} rows with a SKU or a link")

    waiting = [r for r in rows if r["sku"] and not r["checked"]]
    print(f"\nrows with a supplier link but NO SKU yet: {len(no_sku)}"
          + (f" (first rows: {[(x['tab'], x['row']) for x in no_sku[:8]]})" if no_sku else ""))
    print(f"rows with a SKU that no sync has processed yet (Last Checked Time blank): {len(waiting)} "
          f"{ {t: sum(1 for x in waiting if x['tab'] == t) for t in {x['tab'] for x in waiting}} }")

    problems = classify(rows)
    reasons = {}
    for r, why in problems:
        key = why.split(" (")[0] if why.startswith("not a usable") else (
            "same barcode digits as another row" if why.startswith("same barcode") else
            "supplier product already owned by an earlier row" if why.startswith("supplier product already") else why)
        reasons.setdefault(key, []).append(r)
    bad_rows = {(r["tab"], r["row"]) for r, _ in problems}
    print(f"\nof those {len(waiting)}: {len(waiting) - len(bad_rows & {(w['tab'], w['row']) for w in waiting})} look fine; "
          f"{len(bad_rows)} row(s) have at least one problem:")
    for key, rs in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(rs):5d}  {key} | e.g. {[x['sku'] for x in rs[:6]]}")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "new_sku_problems.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["Tab", "Row", "SKU", "Problem", "Supplier URL"])
        for r, why in sorted(problems, key=lambda p: (p[0]["tab"], p[0]["row"])):
            w.writerow([r["tab"], r["row"], r["sku"], why, r["url"][:120]])
        for r in no_sku:
            w.writerow([r["tab"], r["row"], "", "no SKU yet", r["url"][:120]])
    print(f"\nwrote {len(problems) + len(no_sku)} line(s) to {OUT_DIR}/new_sku_problems.csv")


if __name__ == "__main__":
    main()
