"""One-off (2026-08-15): the user deleted the conflicted (wrong-content)
listings from OnBuy via the dashboard so the pipeline can RECREATE them
with correct information. Deletion alone is not enough: the rows still
carry Synced status + an OPC, which the anti-duplicate guard reads as
"already created" and routes to update-only forever. Clear the OnBuy
state on exactly those rows - Sync Status, OPC, Last OnBuy Sync, OnBuy
Product Created/Listing Active/Product ID, and Last Checked Time (so the
oldest-first batch picks them up immediately) - and the next run creates
them fresh, with the activation pass pushing price/stock right after.
SKUs come from the RESET_SKUS env (default: the store's audited conflict
list). DRY_RUN default on."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = "Arden_Full_Feed_Master"
DEFAULT_SKUS = (
    "250265394790,250550967258,250552820179,250648987557,250682239827,"
    "250688243538,250699754337,250781776469,250909649439,250937928131,"
    "250979509046,251113832396,251211226394,251245976913,251368045398,"
    "251372327954,251459395326,251461144158,251477918187,251630389311,"
    "251662196451,251786268249,251810214594,251941622947,251963523574,"
    "252004125634,252018979094,252127633726,252579055343,252660067224,"
    "252949493270,252981368130,253064075792,253114267337,253367421715,"
    "253784639014,253899853633,254098446602,254115512204,254369357422,"
    "254540755948,254602033595,255048354190,255630430790,256078821447,"
    "256199052171,256297515592,256798498400,257165076894,257515810451,"
    "257536225098,257746965128,257809252769,257908007246,258224939112,"
    "258318223523,258448831186,258674293949,258749477946,258789540525,"
    "258932772506,258996247767,259271682679,259377508699,259447744309,"
    "259559513336,259640620530,259731169108,259757474781,259856081064,"
    "260328945173"
)
SKUS = {s.strip() for s in (os.getenv("RESET_SKUS") or DEFAULT_SKUS).split(",") if s.strip()}
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")
CLEAR_COLS = ["Sync Status", "OPC", "Last OnBuy Sync", "OnBuy Product Created",
              "OnBuy Listing Active", "OnBuy Product ID", "Last Checked Time"]


def col_letter(n):
    s = ""
    while n >= 0:
        s = chr(n % 26 + 65) + s
        n = n // 26 - 1
    return s


def main():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    sheet = gspread.authorize(creds).open(SHEET_NAME).sheet1
    headers = [str(h).strip() for h in sheet.row_values(1)]
    col_map = {h: i for i, h in enumerate(headers)}
    missing = [c for c in CLEAR_COLS if c not in col_map]
    cols = [c for c in CLEAR_COLS if c in col_map]
    if missing:
        print(f"note: sheet lacks {missing} - clearing {cols}")
    rows = sheet.get_all_records()
    updates, found = [], []
    for i, r in enumerate(rows):
        sku = str(r.get("SKU") or "").strip()
        if sku not in SKUS:
            continue
        found.append(sku)
        rownum = i + 2
        for c in cols:
            updates.append({"range": f"{col_letter(col_map[c])}{rownum}", "values": [[""]]})
        print(f"  reset row {rownum} SKU {sku} (status was: {str(r.get('Sync Status') or '')[:40]!r})")
    absent = SKUS - set(found)
    print(f"rows to reset: {len(found)} of {len(SKUS)} requested" +
          (f" | NOT IN SHEET: {sorted(absent)}" if absent else ""))
    if DRY_RUN:
        print("DRY RUN - nothing cleared")
        return
    CHUNK = 200
    for i in range(0, len(updates), CHUNK):
        sheet.batch_update([dict(u) for u in updates[i:i + CHUNK]], value_input_option="RAW")
    print(f"CLEARED {len(cols)} column(s) on {len(found)} row(s) - next runs re-create them fresh")

    # The anti-duplicate guard reads the Supabase mirror too (existing.get
    # ("OPC")) - clearing only the sheet leaves already_created TRUE and the
    # rows defer forever instead of re-creating (seen live 2026-08-15).
    # Fetch-merge-upsert the same SKUs with their OnBuy state cleared.
    import supabase_db
    full = supabase_db.fetch_full_rows(sorted(found))
    if full:
        for row in full.values():
            row["Sync Status"] = ""
            row["OPC"] = ""
            row["OnBuy Product ID"] = ""
            row["OnBuy Product Created"] = None
            row["OnBuy Listing Active"] = None
            row["Last OnBuy Sync"] = None
        supabase_db.upsert_products(list(full.values()))
        print(f"SUPABASE cleared on {len(full)} row(s)")
    else:
        print("SUPABASE: no rows found for these SKUs - nothing to clear")



if __name__ == "__main__":
    main()
