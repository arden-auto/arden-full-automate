"""One-off (2026-08-27, user policy): the all-products Buy Box track is
GTV-only; Arden reverts to eBay-sheet-products-only like YRA (Amazon API
integration is the future route for the rest of the catalog). Remove the
remaining 08-25 imported block rows: a row is deleted only if ALL hold:
  - it sits in the appended import block (row >= BLOCK_START);
  - it is import-shaped (no Supplier URL, Sync Status "Synced");
  - its Cost Price is still empty (a filled cost = the team adopted it -
    kept and reported).
Original rows are never touched; the live listings stay live on OnBuy.
Block rows with a different Sync Status are kept and reported (the hourly
backfill re-stamped some imported rows). deleteDimension runs descending.
DRY_RUN default on."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from retry_utils import with_retry

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
BLOCK_START = int(os.environ["BLOCK_START"])


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)
    print(f"rows: {len(rows)} | block starts at row {BLOCK_START}")

    to_delete, kept_cost, kept_status, kept_url = [], [], [], []
    for i, r in enumerate(rows):
        rownum = i + 2
        if rownum < BLOCK_START:
            continue
        sku = str(r.get("SKU") or "").strip()
        if not sku:
            continue
        if str(r.get("Supplier URL") or "").strip():
            kept_url.append(rownum)
            continue
        if str(r.get("Cost Price (£)") or "").strip():
            kept_cost.append((rownum, sku))
            continue
        if str(r.get("Sync Status") or "").strip() != "Synced":
            kept_status.append((rownum, sku, str(r.get("Sync Status") or "").strip()[:30]))
            continue
        to_delete.append(rownum)
    for rn, sku in kept_cost:
        print(f"  KEEP row {rn} SKU {sku} - team filled Cost Price")
    for rn, sku, st in kept_status[:10]:
        print(f"  KEEP row {rn} SKU {sku} - status {st!r}")
    if kept_url:
        print(f"  KEEP {len(kept_url)} row(s) with a Supplier URL (team-adopted)")
    print(f"import rows to delete: {len(to_delete)} | kept: cost {len(kept_cost)}, status {len(kept_status)}, url {len(kept_url)}")
    if DRY_RUN:
        print("DRY RUN - nothing deleted")
        return
    if not to_delete:
        print("nothing to delete")
        return
    requests = [{"deleteDimension": {"range": {
        "sheetId": sheet.id, "dimension": "ROWS",
        "startIndex": rn - 1, "endIndex": rn}}} for rn in sorted(to_delete, reverse=True)]
    done = 0
    for c in range(0, len(requests), 400):
        chunk = requests[c:c + 400]
        with_retry(lambda ch=chunk: sheet.spreadsheet.batch_update({"requests": ch}),
                   what=f"delete batch {c}", max_attempts=3)
        done = min(c + 400, len(requests))
        print(f"deleted {done}/{len(requests)}")
    print(f"removed {len(to_delete)} imported row(s); originals and live listings untouched")


if __name__ == "__main__":
    main()
