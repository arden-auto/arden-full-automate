"""READ-ONLY (2026-10-07): for the given SKUs - what the sheet says, what OnBuy shows for the listing, what the product queue
answered, and (via the OPC) whether two SKUs sit on the SAME OnBuy product.

Built for "the sheet is right but OnBuy shows another product's content". Prints per SKU:
  SHEET     every row carrying the SKU (tab, row, sync/OPC columns, title, brand, category, supplier link, image, description head),
            plus NEIGHBOURS rows above and below it (SKU / title / OPC only) - a shifted create shows up as the neighbour's content
  LISTING   the live OnBuy listing found with a filtered GET (name, OPC, price, stock, created/updated, product url)
  QUEUE     every entry of the product queue's visible history for the SKU (status, OPC, error, the queue id and the time it encodes -
            a queue id is a Mongo ObjectId whose first 4 bytes are the creation time in UTC seconds)
Never a cost or a shipping figure. Writes nothing anywhere.

Env: SKUS (comma-separated, exact), NEIGHBOURS (3), MAX_PAGES (queue pages of 50, default 40), SHEET_NAME.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import sheet_tabs
from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import RateLimitError, raise_for_status, with_retry

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
WANT = [s.strip() for s in (os.getenv("SKUS") or "").split(",") if s.strip()]
NEIGHBOURS = int(os.getenv("NEIGHBOURS") or "3")
MAX_PAGES = int(os.getenv("MAX_PAGES") or "40")
LISTING_KEYS = ("sku", "name", "price", "stock", "product_encoded_id", "opc", "product_codes", "product_listing_id",
                "created_at", "updated_at", "product_url", "condition")
SHEET_KEYS = ("Sync Status", "OnBuy Product Created", "OnBuy Listing Active", "OPC", "Last OnBuy Sync", "Last Checked Time",
              "EAN", "Brand", "Category", "Category ID", "Stock", "Status")


def queue_time(queue_id):
    """The creation time a queue id (a Mongo ObjectId) encodes, or ''."""
    try:
        return datetime.fromtimestamp(int(str(queue_id)[:8], 16), timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        return ""


def text_head(s, n=200):
    s = re.sub(r"<[^>]+>", " ", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


def read_tabs():
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
    out = []
    for ws in tabs:
        values = with_retry(lambda ws=ws: ws.get_all_values(), what=f"read {ws.title}", max_attempts=3)
        header = [str(h).strip() for h in values[0]]
        out.append((ws.title, header, values))
    return out


def show_sheet(tabs):
    found = {}
    for title, header, values in tabs:
        ix = {h: i for i, h in enumerate(header) if h}

        def cell(r, k):
            i = ix.get(k)
            return str(r[i]).strip() if i is not None and i < len(r) else ""
        for n, r in enumerate(values[1:], start=2):
            sku = cell(r, "SKU").replace(",", "").strip()
            if sku in WANT:
                found.setdefault(sku, []).append((title, n))
                print(f"SHEET {title} row {n} | SKU {sku}")
                print("  " + " | ".join(f"{k}: {cell(r, k)[:60]}" for k in SHEET_KEYS if k in ix and cell(r, k)))
                print(f"  title: {cell(r, 'Title')[:160]}")
                print(f"  link : {cell(r, 'Supplier URL')[:130]}")
                imgs = cell(r, "Image URL")
                extra = [u for u in cell(r, "Additional Images").split(",") if u.strip()]
                print(f"  image: {imgs[:120]} (+{len(extra)} more)")
                print(f"  desc : {text_head(cell(r, 'Description'))}")
                for k in range(max(2, n - NEIGHBOURS), min(len(values), n + NEIGHBOURS) + 1):
                    if k == n:
                        continue
                    rr = values[k - 1]
                    print(f"    near row {k:5d} | {cell(rr, 'SKU')[:34]:34s} | OPC {cell(rr, 'OPC')[:9]:9s} | {cell(rr, 'Title')[:70]}")
    for s in WANT:
        if s not in found:
            print(f"SHEET: {s} is on NO product tab")
        elif len(found[s]) > 1:
            print(f"SHEET: {s} is on {len(found[s])} rows: {found[s]}")
    return found


def show_listings(onbuy):
    got = {}
    for sku in WANT:
        def _do(sku=sku):
            r = onbuy._send("GET", f"{BASE_URL}/listings", what=f"listing {sku}",
                            params={"site_id": onbuy.site_id, "limit": 5, "offset": 0, "filter[sku]": sku}, timeout=60)
            raise_for_status(r, what=f"listing {sku}")
            return r
        try:
            body = with_retry(_do, what=f"listing {sku}", max_attempts=3).json()
        except RateLimitError:
            print(f"LISTING {sku}: RATE LIMITED")
            continue
        items = (body.get("results") if isinstance(body, dict) else body) or []
        hit = [i for i in items if str((i or {}).get("sku") or "").strip() == sku]
        if not hit:
            print(f"LISTING {sku}: none ({len(items)} item(s) answered; the filter may not match a SKU with letters)")
            continue
        got[sku] = hit[0]
        print("LISTING " + json.dumps({k: hit[0].get(k) for k in LISTING_KEYS if k in hit[0]}, ensure_ascii=False, default=str))
    opcs = {}
    for sku, it in got.items():
        opc = str(it.get("product_encoded_id") or it.get("opc") or "").strip().upper()
        if opc:
            opcs.setdefault(opc, []).append(sku)
    for opc, skus in opcs.items():
        if len(skus) > 1:
            print(f"SAME PRODUCT: {skus} are all attached to OnBuy product {opc}")
    return got


def show_queue(onbuy):
    hits = []
    for page in range(MAX_PAGES):
        try:
            result = onbuy.list_queue(limit=50, offset=page * 50)
        except RateLimitError:
            print(f"QUEUE: rate limited at page {page} - what was read so far:")
            break
        entries = result.get("results", []) if isinstance(result, dict) else []
        if not entries:
            break
        for e in entries:
            if str((e or {}).get("uid") or "").strip() in WANT:
                hits.append((page, e))
        if len(entries) < 50:
            break
        time.sleep(0.3)
    print(f"QUEUE: {len(hits)} entr(y/ies) for {len(WANT)} SKU(s) in the visible history")
    for page, e in hits:
        keep = {k: e.get(k) for k in ("uid", "status", "opc", "error_message", "warning_messages", "queue_id", "product_url",
                                      "permitted_write_levels") if k in e}
        print(f"QUEUE page {page} | {queue_time(e.get('queue_id'))} | " + json.dumps(keep, ensure_ascii=False, default=str)[:700])
    return hits


def main():
    if not WANT:
        raise SystemExit("SKUS required")
    tabs = read_tabs()
    show_sheet(tabs)
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    show_listings(onbuy)
    show_queue(onbuy)


if __name__ == "__main__":
    main()
