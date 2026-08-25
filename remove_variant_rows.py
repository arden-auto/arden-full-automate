"""One-off (2026-08-25, user decision): the 08-25 catalog import brought in
live listings that are second copies of products the sheet already manages
(same product under a different SKU - dashboard-era/incident duplicates).
Buy Box defense on our own duplicate copy makes no sense, so remove those
rows again. A row is removed only if ALL hold:
  - it sits in the appended import block (row >= BLOCK_START);
  - it is import-shaped (no Supplier URL, Sync Status "Synced");
  - its Cost Price is still empty (a filled cost = the team adopted it);
  - its normalized Title matches a pre-block row's Title;
  - its SKU is not in KEEP_SKUS (rows kept deliberately: where the original
    row is a dead stuck pointer, the imported row is the product's ONLY
    working handle).
Original (pre-block) rows are never touched. The matching live listings
stay live on OnBuy - this only removes sheet rows. deleteDimension applies
against live state, so deletions run descending. DRY_RUN default on."""
import json
import os
import re

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from retry_utils import with_retry

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
BLOCK_START = int(os.environ["BLOCK_START"])
KEEP_SKUS = {s.strip() for s in (os.getenv("KEEP_SKUS") or "").split(",") if s.strip()}


def norm_title(t):
    return re.sub(r"\s+", " ", str(t or "").strip()).casefold()


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)

    pre_titles = {}
    for i, r in enumerate(rows):
        rownum = i + 2
        if rownum >= BLOCK_START:
            break
        t = norm_title(r.get("Title"))
        if t and t not in pre_titles:
            pre_titles[t] = (rownum, str(r.get("SKU") or "").strip())
    print(f"rows: {len(rows)} | block starts at row {BLOCK_START} | pre-block titles: {len(pre_titles)}")

    to_delete, kept_cost, kept_keep = [], [], []
    for i, r in enumerate(rows):
        rownum = i + 2
        if rownum < BLOCK_START:
            continue
        sku = str(r.get("SKU") or "").strip()
        t = norm_title(r.get("Title"))
        if not sku or not t or t not in pre_titles:
            continue
        if str(r.get("Supplier URL") or "").strip() or str(r.get("Sync Status") or "").strip() != "Synced":
            continue
        if sku in KEEP_SKUS:
            kept_keep.append((rownum, sku))
            continue
        if str(r.get("Cost Price (£)") or "").strip():
            kept_cost.append((rownum, sku))
            continue
        prn, psku = pre_titles[t]
        to_delete.append(rownum)
        print(f"  REMOVE row {rownum} SKU {sku} (variant of pre row {prn} SKU {psku}) | {str(r.get('Title') or '')[:60]}")
    for rn, sku in kept_keep:
        print(f"  KEEP row {rn} SKU {sku} - KEEP_SKUS (only working handle for its listing)")
    for rn, sku in kept_cost:
        print(f"  KEEP row {rn} SKU {sku} - team already filled Cost Price")
    print(f"variant rows to remove: {len(to_delete)} | kept (cost filled): {len(kept_cost)} | kept (keep-list): {len(kept_keep)}")
    if DRY_RUN:
        print("DRY RUN - nothing deleted")
        return
    if not to_delete:
        print("nothing to delete")
        return
    requests = [{"deleteDimension": {"range": {
        "sheetId": sheet.id, "dimension": "ROWS",
        "startIndex": rn - 1, "endIndex": rn}}} for rn in sorted(to_delete, reverse=True)]
    for i in range(0, len(requests), 400):
        chunk = requests[i:i + 400]
        with_retry(lambda c=chunk: sheet.spreadsheet.batch_update({"requests": c}), what=f"delete batch {i}", max_attempts=3)
        print(f"deleted {min(i + 400, len(requests))}/{len(requests)}")
    print(f"removed {len(to_delete)} variant row(s); originals and live listings untouched")


if __name__ == "__main__":
    main()
