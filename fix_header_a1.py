"""One-off: repair Sheet1!A1 from ' SKU' (one leading space, came with the 2026-11-07 paste of ~8,300 rows) to exactly 'SKU'.

get_all_records keys rows by the RAW header text, so the padded cell made row.get("SKU") answer None on every row
for every dict-based reader (sheet_skus_dump's ROW lines proved it). Refuses to touch A1 unless its current value
still STRIPS to 'SKU' - nothing else is read or written.
"""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(os.environ["GOOGLE_CREDENTIALS"]),
    ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
ws = gspread.authorize(creds).open(SHEET_NAME).worksheet("Sheet1")
cur = ws.acell("A1").value
print(f"A1 now: {cur!r}")
if cur == "SKU":
    print("already clean - nothing to do")
elif isinstance(cur, str) and cur.strip() == "SKU":
    ws.update_acell("A1", "SKU")
    back = ws.acell("A1").value
    print(f"A1 after write: {back!r}")
    if back != "SKU":
        raise SystemExit("write did not stick")
    rec = ws.get_all_records(head=1)
    print("first row SKU via dict now:", repr(rec[0].get("SKU")) if rec else "(no rows)")
else:
    raise SystemExit(f"A1 is {cur!r}, not a padded 'SKU' - refusing")

