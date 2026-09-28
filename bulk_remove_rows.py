"""Bulk row removal (2026-09-14): deletes an explicit SKU list's rows from
the Sheet (every tab that has a SKU column) and from Supabase. Does NOT
touch OnBuy - run delete_listings on the same SKUs for the listing side;
run THIS first: with the rows gone, no sync can re-create a deleted
listing through the Failed-row create fallback.

SKUs come from REMOVE_SKUS (comma-separated) or REMOVE_SKUS_FILE (one per
line, # comments allowed) - explicit list only, no default, no scan.
Matching is exact-as-typed (after the usual comma/space strip); a
digits-only near-miss is reported but never deleted. DRY_RUN is on unless
set to 0/no. Supabase first, then Sheet rows in descending order (the
deleteDimension gotcha), chunked.
"""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import supabase_db
from retry_utils import with_retry

SHEET_NAME = "Arden_Full_Feed_Master"
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")


def load_skus():
    raw = os.getenv("REMOVE_SKUS") or ""
    skus = [s.strip() for s in raw.split(",") if s.strip()]
    path = (os.getenv("REMOVE_SKUS_FILE") or "").strip()
    if path:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    skus.append(line)
    return list(dict.fromkeys(skus))  # de-dupe, order kept


def main():
    targets = load_skus()
    if not targets:
        raise SystemExit("No SKUs given (REMOVE_SKUS / REMOVE_SKUS_FILE) - this tool never runs without an explicit list")
    target_set = set(targets)
    digits_of = {"".join(ch for ch in s if ch.isdigit()): s for s in targets}
    print(f"SKUs to remove: {len(targets)}{' (DRY RUN)' if DRY_RUN else ''}")

    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    book = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME), what="sheet open", max_attempts=3)

    found = {}          # sku -> (tab title, row number)
    per_tab = {}        # tab title -> [row numbers]
    near_misses = []
    was_on_onbuy = 0
    for tab in book.worksheets():
        header = [str(h).strip() for h in tab.row_values(1)]
        if "SKU" not in header:
            continue
        data = tab.get_all_records()
        hits = []
        for idx, row in enumerate(data):
            sku = str(row.get("SKU") or "").replace(",", "").strip()
            if not sku:
                continue
            if sku in target_set:
                n = idx + 2
                hits.append(n)
                found[sku] = (tab.title, n)
                if str(row.get("OnBuy Product Created") or "").strip().upper() == "TRUE" \
                        or str(row.get("Sync Status") or "").startswith(("Synced", "Pending Approval", "Awaiting")):
                    was_on_onbuy += 1
            else:
                d = "".join(ch for ch in sku if ch.isdigit())
                if d and d in digits_of and digits_of[d] not in found:
                    near_misses.append(f"tab {tab.title} row {idx + 2}: sheet SKU {sku!r} vs list {digits_of[d]!r}")
        if hits:
            per_tab[tab.title] = hits
            print(f"Tab '{tab.title}': {len(hits)} row(s) matched")

    missing = sorted(target_set - set(found))
    print(f"\nMatched {len(found)} of {len(targets)}; ~{was_on_onbuy} look created on OnBuy (need the listing delete too)")
    if near_misses:
        print(f"NEAR-MISSES (digits match, text differs - NOT touched):")
        for m in near_misses[:20]:
            print("  " + m)
    if missing:
        print(f"NOT FOUND in any tab ({len(missing)}):")
        for s in missing:
            print("  " + s)

    if DRY_RUN:
        print("\nDRY RUN - nothing deleted.")
        return
    if not found:
        print("Nothing to delete - done.")
        return

    # Supabase first (remove_brand_rejected_skus.py ordering rationale),
    # chunked to keep each PostgREST in.() filter comfortably small.
    skus_sorted = sorted(found)
    ok_all = True
    for c in range(0, len(skus_sorted), 100):
        chunk = skus_sorted[c:c + 100]
        ok = supabase_db.delete_products(chunk)
        ok_all = ok_all and bool(ok)
        print(f"Supabase delete {c + 1}-{c + len(chunk)}: {'OK' if ok else 'FAILED'}")
    if not ok_all:
        print("Supabase had failures - Sheet rows are still being removed; re-run later for stragglers.")

    for title, rows in per_tab.items():
        tab = book.worksheet(title)
        ordered = sorted(set(rows), reverse=True)
        for c in range(0, len(ordered), 200):
            chunk = ordered[c:c + 200]
            reqs = [{"deleteDimension": {"range": {
                "sheetId": tab.id, "dimension": "ROWS",
                "startIndex": n - 1, "endIndex": n}}} for n in chunk]
            tab.spreadsheet.batch_update({"requests": reqs})
        print(f"Sheet delete tab '{title}': {len(ordered)} row(s) removed")
    print("Done.")


if __name__ == "__main__":
    main()
