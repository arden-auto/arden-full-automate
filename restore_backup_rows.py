"""Restore rows from a removal BACKUP TAB back onto their product tab
(2026-11-24, user: "Restore the 35 removed rows" after Ardwick's new
deliberate-doubles rule made the earlier duplicate removal wrong there).

Reads the backup tab guarded_remove_rows wrote (bk_removed_utc, bk_tab,
bk_row, bk_reason, then bk_<original header> columns) and APPENDS each
row's original cells to the bottom of its bk_tab, mapped onto that tab's
CURRENT header order. A SKU already present on any product tab is
skipped (never a second restore). The OnBuy-state cells (Sync Status,
Last Checked Time, Last Updated) are cleared on the restored copy so the
oldest-first batch picks the row up at once and creates it fresh.
DRY_RUN default on; sheet only - no OnBuy, no Keepa.
"""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import sheet_tabs
from retry_utils import with_retry

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
BACKUP_TAB = (os.getenv("BACKUP_TAB") or "").strip()
ONLY_SKUS = {s.strip() for s in (os.getenv("SKUS") or "").replace("\n", ",").split(",") if s.strip()}
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
CLEAR_ON_RESTORE = ("Sync Status", "Last Checked Time", "Last Updated")


def main():
    if not BACKUP_TAB:
        raise SystemExit("BACKUP_TAB is required")
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME), what="sheet open", max_attempts=3)
    bk = book.worksheet(BACKUP_TAB)
    values = with_retry(bk.get_all_values, what="read backup", max_attempts=3)
    header = [str(h).strip() for h in values[0]]
    if header[:4] != ["bk_removed_utc", "bk_tab", "bk_row", "bk_reason"]:
        raise SystemExit(f"{BACKUP_TAB!r} does not look like a removal backup tab: {header[:4]}")
    col_of = {h[3:]: i for i, h in enumerate(header) if h.startswith("bk_") and h not in
              ("bk_removed_utc", "bk_tab", "bk_row", "bk_reason")}

    # every SKU currently on the product tabs (never restore over one)
    present = set()
    tabs = {}
    for ws in [sheet_tabs.product_sheet(book)] + ([book.worksheet("Amazon")] if "Amazon" in
                                                  [w.title for w in book.worksheets()] else []):
        if ws.title in tabs:
            continue
        tabs[ws.title] = ws
        head = [str(h).strip() for h in with_retry(lambda w=ws: w.row_values(1), what="header", max_attempts=3)]
        sku_i = head.index("SKU") + 1
        present |= {str(v).replace(",", "").strip()
                    for v in with_retry(lambda w=ws, c=sku_i: w.col_values(c), what="skus", max_attempts=3)[1:]}

    plan, skipped = {}, []
    for r in values[1:]:
        def cell(name):
            i = col_of.get(name)
            return str(r[i]).strip() if i is not None and i < len(r) else ""
        sku = cell("SKU").replace(",", "").strip()
        tab = str(r[1]).strip()
        if ONLY_SKUS and sku not in ONLY_SKUS:
            continue
        if not sku or tab not in tabs:
            skipped.append((sku or "(blank)", f"tab {tab!r} unknown" if sku else "no SKU"))
            continue
        if sku in present:
            skipped.append((sku, "already on a product tab"))
            continue
        present.add(sku)
        plan.setdefault(tab, []).append((sku, r))

    total = sum(len(v) for v in plan.values())
    print(f"backup rows: {len(values) - 1} | to restore: {total} | skipped: {len(skipped)} | {'DRY RUN' if DRY_RUN else 'LIVE RUN'}")
    for sku, why in skipped[:20]:
        print(f"  skip {sku}: {why}")
    for tab, items in plan.items():
        ws = tabs[tab]
        head = [str(h).strip() for h in with_retry(lambda w=ws: w.row_values(1), what="header", max_attempts=3)]
        before = len(with_retry(lambda w=ws: w.col_values(head.index('SKU') + 1), what="count", max_attempts=3))
        out = []
        for sku, r in items:
            def cell(name):
                i = col_of.get(name)
                return str(r[i]).strip() if i is not None and i < len(r) else ""
            out.append(["" if h in CLEAR_ON_RESTORE else cell(h) for h in head])
        print(f"{tab}: restoring {len(out)} row(s) after current row {before}")
        if DRY_RUN:
            for sku, _r in items[:40]:
                print(f"  would restore {sku}")
            continue
        with_retry(lambda w=ws, o=out: w.append_rows(o, value_input_option="RAW",
                                                     table_range=f"A1"), what=f"{tab} append", max_attempts=3)
        after = len(with_retry(lambda w=ws: w.col_values(head.index('SKU') + 1), what="count", max_attempts=3))
        print(f"{tab}: now {after} SKU rows (was {before}) -> restored rows {before + 1}-{after}")
    if DRY_RUN:
        print("DRY RUN - nothing written.")


if __name__ == "__main__":
    main()
