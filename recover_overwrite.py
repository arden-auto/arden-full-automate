"""One-off, surgical (2026-11-24): undo the restore tool's append accident on the Amazon tab.

What happened: restore_backup_rows appended 35 rows with the Sheets append API; a hidden
empty row at 1917 made the API read the table as ending at 1916, so the 35 rows were
written OVER rows 1917-1951 instead of after row 5865. This tool reads the last Drive
revision from BEFORE that write (read-only export, like restore_sku_column on GTV),
proves the revision still matches today's tab above (1910-1916) and below (1952-1958)
the damage, then in ONE plan: writes the revision's rows 1917-1951 back, and writes the
35 backup-tab rows to explicit rows 5866-5900 (values update, never append).

Env: ACTION plan|apply (default plan), CUTOFF (ISO, default 2026-11-24T12:40:00Z),
     BACKUP_TAB (default 'Removed 2026-11-24'), CONFIRM must be 'recover' for apply.
"""
import io
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

from retry_utils import with_retry

SHEET_NAME = "Arden_Full_Feed_Master"
TAB = "Amazon"
DAMAGE_FIRST, DAMAGE_LAST = 1917, 1951          # sheet rows my append overwrote
TARGET_FIRST = 5866                             # where the 35 backup rows truly belong
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DRIVE = "https://www.googleapis.com/drive/v3"
ACTION = (os.getenv("ACTION") or "plan").strip().lower()
CUTOFF = (os.getenv("CUTOFF") or "2026-11-24T12:40:00Z").strip()
BACKUP_TAB = (os.getenv("BACKUP_TAB") or "Removed 2026-11-24").strip()
CONFIRM = (os.getenv("CONFIRM") or "").strip()
CLEAR_ON_RESTORE = ("Sync Status", "Last Checked Time", "Last Updated")


def norm(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    gc = gspread.authorize(creds)
    book = with_retry(lambda: gc.open(SHEET_NAME), what="sheet open", max_attempts=3)
    auth = {"Authorization": f"Bearer {creds.get_access_token().access_token}"}
    import requests

    # ---- newest revision before the damage
    revs, token = [], None
    while True:
        params = {"pageSize": 1000, "fields": "nextPageToken,revisions(id,modifiedTime,exportLinks,lastModifyingUser(displayName))"}
        if token:
            params["pageToken"] = token
        r = requests.get(f"{DRIVE}/files/{book.id}/revisions", params=params, headers=auth, timeout=120)
        r.raise_for_status()
        body = r.json()
        revs += body.get("revisions", [])
        token = body.get("nextPageToken")
        if not token:
            break
    before = [r for r in revs if r["modifiedTime"] <= CUTOFF and XLSX in (r.get("exportLinks") or {})]
    if not before:
        raise SystemExit("no exportable revision before the cutoff")
    rev = before[-1]
    print(f"revision {rev['id']} ({rev['modifiedTime']}, {(rev.get('lastModifyingUser') or {}).get('displayName', '?')}) of {len(revs)} total")

    blob = requests.get(rev["exportLinks"][XLSX], headers=auth, timeout=900)
    blob.raise_for_status()
    print(f"downloaded {len(blob.content) / 1e6:.1f} MB")
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(blob.content), read_only=True, data_only=True)
    old = [list(r) for r in wb[TAB].iter_rows(values_only=True)]
    old_header = [norm(c) for c in old[0]]

    ws = book.worksheet(TAB)
    live = with_retry(ws.get_all_values, what="read live tab", max_attempts=3)
    live_header = [str(c).strip() for c in live[0]]
    if old_header[:len(live_header)] != live_header[:len(old_header)]:
        raise SystemExit(f"header mismatch between revision and live tab:\n old={old_header[:8]}\n live={live_header[:8]}")
    sku_i = live_header.index("SKU")

    def old_row(n):      # sheet row n -> list of norm'd cells in live-header order
        r = old[n - 1] if n - 1 < len(old) else []
        return [norm(r[i]) if i < len(r) else "" for i in range(len(live_header))]

    def live_row(n):
        r = live[n - 1] if n - 1 < len(live) else []
        return [str(r[i]).strip() if i < len(r) else "" for i in range(len(live_header))]

    # ---- guards: revision must equal the live sheet just above and just below the damage (SKU + URL columns)
    url_i = live_header.index("Supplier URL")
    for n in list(range(DAMAGE_FIRST - 7, DAMAGE_FIRST)) + list(range(DAMAGE_LAST + 1, DAMAGE_LAST + 8)):
        o, l = old_row(n), live_row(n)
        if (o[sku_i], o[url_i][:60]) != (l[sku_i], l[url_i][:60]):
            raise SystemExit(f"ALIGNMENT GUARD FAILED at row {n}: revision SKU {o[sku_i]!r} vs live {l[sku_i]!r} - not writing anything")
    print(f"alignment verified: rows {DAMAGE_FIRST - 7}-{DAMAGE_FIRST - 1} and {DAMAGE_LAST + 1}-{DAMAGE_LAST + 7} match the revision")

    lost = [old_row(n) for n in range(DAMAGE_FIRST, DAMAGE_LAST + 1)]
    print(f"rows to put back at {DAMAGE_FIRST}-{DAMAGE_LAST}: {len(lost)}")
    for n, r in zip(range(DAMAGE_FIRST, DAMAGE_LAST + 1), lost):
        print(f"  {n}: SKU {r[sku_i] or '(blank)'}")

    # ---- the 35 backup rows -> explicit rows 5866+
    bk = book.worksheet(BACKUP_TAB)
    bvals = with_retry(bk.get_all_values, what="read backup", max_attempts=3)
    bhead = [str(h).strip() for h in bvals[0]]
    col_of = {h[3:]: i for i, h in enumerate(bhead) if h.startswith("bk_")}
    dups = []
    for r in bvals[1:]:
        dups.append(["" if h in CLEAR_ON_RESTORE else (str(r[col_of[h]]).strip() if h in col_of and col_of[h] < len(r) else "")
                     for h in live_header])
    print(f"backup rows to write at {TARGET_FIRST}-{TARGET_FIRST + len(dups) - 1}: {len(dups)}")

    # sanity: the target region must be EMPTY on the live sheet
    for k in range(len(dups)):
        n = TARGET_FIRST + k
        if any(c for c in live_row(n)):
            raise SystemExit(f"target row {n} is not empty on the live sheet - not writing anything")
    print("target region verified empty")

    if ACTION != "apply":
        print("PLAN ONLY - nothing written. Set ACTION=apply CONFIRM=recover to write.")
        return
    if CONFIRM != "recover":
        raise SystemExit("ACTION=apply needs CONFIRM=recover")

    if ws.row_count < TARGET_FIRST + len(dups):
        with_retry(lambda: ws.add_rows(TARGET_FIRST + len(dups) - ws.row_count), what="grow grid", max_attempts=3)
    end_col = gspread.utils.rowcol_to_a1(1, len(live_header)).rstrip("1")
    with_retry(lambda: ws.update(values=lost, range_name=f"A{DAMAGE_FIRST}:{end_col}{DAMAGE_LAST}", value_input_option="RAW"),
               what="write lost rows", max_attempts=3)
    with_retry(lambda: ws.update(values=dups, range_name=f"A{TARGET_FIRST}:{end_col}{TARGET_FIRST + len(dups) - 1}", value_input_option="RAW"),
               what="write backup rows", max_attempts=3)

    check = with_retry(lambda: ws.get(f"A{DAMAGE_FIRST}:A{DAMAGE_LAST}"), what="verify 1", max_attempts=3)
    check2 = with_retry(lambda: ws.get(f"A{TARGET_FIRST}:A{TARGET_FIRST + len(dups) - 1}"), what="verify 2", max_attempts=3)
    print(f"VERIFY: rows {DAMAGE_FIRST}-{DAMAGE_LAST} now start with {[c[0] if c else '' for c in check][:3]}...")
    print(f"VERIFY: rows {TARGET_FIRST}+ now hold {sum(1 for c in check2 if c and c[0])} SKU cells")
    print("DONE.")


if __name__ == "__main__":
    main()
