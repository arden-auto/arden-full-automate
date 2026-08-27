"""One-off (2026-08-28, user report): a manual full sweep overwrote
manually-raised Selling Prices with the fixed-margin formula - root cause
was the OOS zeroing (fixed in generate_xml the same day: price is never
zeroed again). The live OnBuy listings still carry the manual price
wherever the lowered sheet value was never pushed, so restore the sheet
from the live catalog: for every row whose live listing price is HIGHER
than its sheet Selling Price, write the live price back into the cell.
This also imports any dashboard-set manual price into the sheet, where the
max(existing, formula) floor now protects it for good. Never lowers a
sheet price; protected SKUs skipped. DRY_RUN default on."""
import json
import os
import time

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import with_retry

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
SHEET_NAME = "Arden_Full_Feed_Master"
MIN_DELTA = float(os.getenv("MIN_DELTA") or "0.02")


def col_letter(idx0):
    s = ""
    idx0 += 1
    while idx0:
        idx0, rem = divmod(idx0 - 1, 26)
        s = chr(65 + rem) + s
    return s


def page_live_prices(onbuy):
    out = {}
    offset, limit = 0, 100
    while True:
        def _page(off=offset):
            r = onbuy._send("GET", f"{BASE_URL}/listings", what="listings page",
                            params={"site_id": onbuy.site_id, "limit": limit, "offset": off}, timeout=60)
            r.raise_for_status()
            return r
        body = with_retry(_page, what=f"listings page {offset}", max_attempts=4).json()
        items = body.get("results") if isinstance(body, dict) else body
        if not isinstance(items, list) or not items:
            break
        for it in items:
            it = it or {}
            sku = str(it.get("sku") or "").strip()
            try:
                price = float(str(it.get("price") or "").replace(",", "").strip() or 0)
            except (TypeError, ValueError):
                price = 0.0
            if sku and price > 0 and sku not in out:
                out[sku] = price
        if len(items) < limit:
            break
        offset += limit
        time.sleep(0.3)
    return out


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1, what="sheet open", max_attempts=3)
    headers = [str(h).strip() for h in with_retry(lambda: sheet.row_values(1), what="headers", max_attempts=3)]
    col = {h: i for i, h in enumerate(headers)}
    rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)
    disp = with_retry(lambda: sheet.col_values(col["SKU"] + 1), what="sku display col", max_attempts=3)

    protected = set()
    pp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "protected_skus.txt")
    if os.path.exists(pp):
        with open(pp, encoding="utf-8") as fh:
            protected = {ln.split("#", 1)[0].strip() for ln in fh if ln.split("#", 1)[0].strip()}

    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    live = page_live_prices(onbuy)
    print(f"live priced listings: {len(live)}")

    updates, shown = [], 0
    for i, r in enumerate(rows):
        rownum = i + 2
        sku = str(disp[rownum - 1]).strip() if rownum - 1 < len(disp) else ""
        if not sku or sku in protected or sku not in live:
            continue
        try:
            sheet_price = float(str(r.get("Selling Price (£)") or "").replace(",", "").strip() or 0)
        except (TypeError, ValueError):
            continue
        live_price = live[sku]
        if live_price > sheet_price + MIN_DELTA:
            if shown < 12:
                print(f"  RESTORE row {rownum} SKU {sku}: sheet {sheet_price:.2f} -> live {live_price:.2f}")
                shown += 1
            updates.append((f"{col_letter(col['Selling Price (£)'])}{rownum}", [[live_price]]))
    print(f"price cells to restore (live > sheet): {len(updates)}")
    if DRY_RUN:
        print("DRY RUN - nothing written")
        return
    if not updates:
        print("nothing to restore")
        return
    for c in range(0, len(updates), 400):
        chunk = updates[c:c + 400]
        with_retry(lambda ch=chunk: sheet.batch_update([{"range": rg, "values": v} for rg, v in ch]),
                   what=f"restore batch {c}", max_attempts=3)
        print(f"written {min(c + 400, len(updates))}/{len(updates)}")
    print(f"restored {len(updates)} Selling Price cell(s) from live listings")


if __name__ == "__main__":
    main()
