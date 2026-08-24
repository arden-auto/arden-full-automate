"""One-off: flip named SKUs' Sync Status to a Failed text so the create
fallback resubmits them. For rows whose creation OnBuy REJECTED (per their
queue results) while our sheet shows "Pending Approval"/"Awaiting OnBuy
go-live" - those statuses make already_created true, so without this flip
the row never re-creates. Use ONLY for SKUs whose failure is confirmed in
OnBuy's own queue results; flipping a genuinely-pending row would mint a
duplicate product. DRY_RUN default on."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from retry_utils import with_retry

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
SKUS = {s.strip() for s in (os.getenv("SKUS") or "").split(",") if s.strip()}
FAILED_TEXT = os.getenv("FAILED_TEXT") or "Failed: creation rejected by OnBuy (category since corrected) - resubmitting"


def col_letter(idx):
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def main():
    if not SKUS:
        raise SystemExit("SKUS is empty - refusing to run")
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    headers = with_retry(lambda: sheet.row_values(1), what="headers", max_attempts=3)
    col = {h.strip(): i for i, h in enumerate(headers)}
    rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)
    updates = []
    for i, r in enumerate(rows):
        sku = str(r.get("SKU") or "").strip()
        if sku not in SKUS:
            continue
        rownum = i + 2
        status = str(r.get("Sync Status") or "").strip()
        opc = str(r.get("OPC") or "").strip()
        print(f"row {rownum} SKU {sku} | status {status[:40]!r} | OPC {opc!r} | category {str(r.get('Category') or '')[:60]}")
        if opc and opc.upper() != "PENDING":
            print(f"  SKIP: has a real OPC - flipping would duplicate")
            continue
        updates.append({"range": f"{col_letter(col['Sync Status'])}{rownum}", "values": [[FAILED_TEXT]]})
    print(f"rows to flip: {len(updates)}")
    if DRY_RUN:
        print("DRY RUN - nothing written")
        return
    if updates:
        with_retry(lambda: sheet.batch_update([dict(u) for u in updates]), what="flip write", max_attempts=3)
    print(f"written: {len(updates)}")


if __name__ == "__main__":
    main()
