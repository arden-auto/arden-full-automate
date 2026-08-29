"""Read-only (2026-08-29): list the queue ids of rows whose OnBuy queue
outcome never confirmed - status "Pending Approval" (or an Awaiting row
whose OPC is still empty/PENDING) with a queue id on record. OnBuy support
asked for the still-pending queue ids to investigate their progress.
A queue id's first 8 hex chars are a unix timestamp - printed as the
submission date so stale entries stand out."""
import json
from datetime import datetime, timezone

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from retry_utils import with_retry
import os

SHEET_NAME = "Arden_Full_Feed_Master"


def qid_date(qid):
    try:
        return datetime.fromtimestamp(int(qid[:8], 16), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return "?"


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    headers = [str(h).strip() for h in with_retry(lambda: sheet.row_values(1), what="headers", max_attempts=3)]
    col = {h: i for i, h in enumerate(headers)}
    rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)
    disp = with_retry(lambda: sheet.col_values(col["SKU"] + 1), what="sku display col", max_attempts=3)

    out = []
    for i, r in enumerate(rows):
        rownum = i + 2
        sku = str(disp[rownum - 1]).strip() if rownum - 1 < len(disp) else ""
        if not sku:
            continue
        status = str(r.get("Sync Status") or "").strip()
        opc = str(r.get("OPC") or "").strip().upper()
        qid = str(r.get("OnBuy Product ID") or "").strip()
        pending = status.startswith("Pending Approval") or (
            status.startswith("Awaiting OnBuy go-live") and opc in ("", "PENDING"))
        if pending and qid:
            out.append((rownum, sku, status[:20], qid))
    print(f"pending rows with a queue id on record: {len(out)}")
    for rownum, sku, status, qid in out:
        print(f"PENDING|{rownum}|{sku}|{status}|{qid}|submitted {qid_date(qid)}")


if __name__ == "__main__":
    main()
