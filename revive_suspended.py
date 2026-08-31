"""One-off (2026-08-31, user request): revive the "Price below minimum"
suspended listings by pushing a valid price/stock per SKU.

Source of values, per SKU (margin-safe, user-approved):
  1) the sheet row's Selling Price/Stock when the SKU exists there with a
     usable price (the managed truth - never undercut it with export data);
  2) else the suspended-export CSV's own price/stock when price > 0;
  3) else reported as NO-SOURCE (team worklist - no push).

Probe first: LIMIT=5 tests whether OnBuy accepts by-SKU updates on
suspended listings at all (the pipeline's suspended-locked class says
edits are rejected until reactivation - support's 1,000-per-call note
suggests it may work now). Batched via update_listings_by_sku_batch,
500 per call. Writes NOTHING to the sheet; read-only against it.
"""
import csv
import io
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from onbuy_client import OnBuyClient
from retry_utils import with_retry

DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
LIMIT = int(os.getenv("LIMIT") or "0")  # 0 = all
CSV_PATH = os.getenv("CSV_PATH") or "suspended_arden.csv"
SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"


def fnum(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    sus = list(csv.DictReader(io.open(CSV_PATH, encoding="utf-8-sig", newline="")))
    print(f"suspended export rows: {len(sus)}")

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        __import__("json").loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME).sheet1,
                       what="sheet open", max_attempts=3)
    hdrs = [str(h).strip() for h in sheet.row_values(1)]
    sku_col = hdrs.index("SKU") + 1
    # Bracketed consistent read (see generate_xml.py, 2026-08-31).
    for _ in range(3):
        col_a = with_retry(lambda: sheet.col_values(sku_col), what="sku col", max_attempts=3)
        rows = with_retry(lambda: sheet.get_all_records(), what="sheet read", max_attempts=3)
        col_b = with_retry(lambda: sheet.col_values(sku_col), what="sku col recheck", max_attempts=3)
        if col_a == col_b:
            break
        print("Sheet changed during the read - re-reading")
    else:
        raise SystemExit("sheet still being edited - aborting")
    by_sku = {}
    for i, r in enumerate(rows):
        if i + 1 < len(col_a):
            key = str(col_a[i + 1]).replace(",", "").strip()
            if key:
                by_sku[key] = r

    plan, no_source = [], []
    for r in sus:
        sku = r["sku"].strip()
        srow = by_sku.get(sku)
        sp = fnum(srow.get("Selling Price (£)")) if srow else 0.0
        sst = int(fnum(srow.get("Stock"))) if srow else 0
        cp, cst = fnum(r.get("price")), int(fnum(r.get("stock")))
        if sp > 0:
            plan.append((sku, sp, sst if sst > 0 else max(cst, 0), "sheet"))
        elif cp > 0:
            plan.append((sku, cp, cst, "csv"))
        else:
            no_source.append(sku)
    from collections import Counter
    print(f"pushable: {len(plan)} ({Counter(s for *_, s in plan)}) | no-source: {len(no_source)}")
    for s in no_source[:20]:
        print(f"  NO-SOURCE {s}")
    if len(no_source) > 20:
        print(f"  ... plus {len(no_source) - 20} more")
    if LIMIT:
        plan = plan[:LIMIT]
        print(f"LIMIT: probing first {len(plan)}")
    if DRY_RUN:
        for sku, p, st, src in plan[:15]:
            print(f"  would push {sku}: price {p:.2f} stock {st} [{src}]")
        print("DRY RUN - nothing pushed")
        return

    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")
    ok = failed = 0
    fail_reasons = Counter()
    for c in range(0, len(plan), 500):
        chunk = plan[c:c + 500]
        listings = [{"sku": s, "price": round(p, 2), "stock": st} for s, p, st, _ in chunk]
        try:
            res = onbuy.update_listings_by_sku_batch(listings)
        except Exception as exc:
            print(f"batch {c} call failed outright: {str(exc)[:160]}")
            failed += len(chunk)
            fail_reasons[str(exc)[:60]] += len(chunk)
            continue
        items = res.get("results", []) if isinstance(res, dict) else []
        seen = {}
        for it in items:
            it = it or {}
            seen[str(it.get("sku") or "").strip()] = str(it.get("error") or "").strip()
        for s, p, st, _ in chunk:
            err = seen.get(s, "no per-item answer")
            if err:
                failed += 1
                fail_reasons[err[:60]] += 1
                if failed <= 12:
                    print(f"  BOUNCED {s}: {err[:100]}")
            else:
                ok += 1
    print(f"DONE: {ok} accepted, {failed} bounced")
    for reason, n in fail_reasons.most_common(8):
        print(f"  reason x{n}: {reason}")


if __name__ == "__main__":
    main()
