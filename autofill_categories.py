"""One-off: apply HAND-CURATED categories for rows the strict matcher
refused. The relaxed auto-scorer tried first and produced DisplayPort-
grade mistakes (Smart TV -> TV Smart Glasses), so each entry below was
chosen by a human eye against the official category file (2026-08-01).
Rows not in the map stay on the employee worklist. DRY_RUN honoured.
"""
import json
import logging
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("onbuy_sync")

SHEET_NAME = "Arden_Full_Feed_Master"
DRY_RUN = (os.getenv("DRY_RUN") or "1").strip().lower() not in ("0", "no", "false", "")

CURATED = {}  # fresh store - fill as the matcher's refusals get diagnosed

# SKUs whose curated value must overwrite whatever Category is currently in
# the row, regardless of Sync Status. ONLY for repairing a wrong category
# that AUTOMATION itself wrote - never list a SKU here to override a
# human's choice. 913619975150: the first (token-subset) version of the
# leaf-in-title fallback filed an Opsite Post-Op wound dressing under
# Garden Decor > Post Boxes on 2026-08-05 ("post" from Post-Op + "box"
# from Box of 20); the matcher is fixed, this repairs the row it poisoned.
FORCE_RECATEGORIZE = set()


def main():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]), scope)
    sheet = gspread.authorize(creds).open(SHEET_NAME).sheet1
    data = sheet.get_all_records()
    headers = [str(h).strip() for h in sheet.row_values(1)]
    col_map = {col: idx + 1 for idx, col in enumerate(headers) if col}

    def col_letter(n):
        out = ""
        while n:
            n, rem = divmod(n - 1, 26)
            out = chr(65 + rem) + out
        return out

    updates, applied = [], []
    for idx, row in enumerate(data, start=2):
        sku = str(row.get("SKU") or "").strip()
        if sku not in CURATED:
            continue
        refused = "no matching OnBuy category" in str(row.get("Sync Status") or "")
        # A mapped SKU whose Category cell is empty is fillable regardless
        # of what Sync Status says - a later run may have overwritten the
        # refusal text, and writing into a blank cell can't clobber a
        # human's choice (2026-08-06: 26 blank-category YRA rows matched
        # the map but not the status gate, so nothing applied).
        blank = not str(row.get("Category") or "").strip()
        force = sku in FORCE_RECATEGORIZE and str(row.get("Category") or "").strip() != CURATED[sku]
        if refused or blank or force:
            path = CURATED[sku]
            applied.append((idx, sku, path + (" [FORCED]" if force and not refused else "")))
            updates.append({"range": f"{col_letter(col_map['Category'])}{idx}", "values": [[path]]})
            updates.append({"range": f"{col_letter(col_map['Sync Status'])}{idx}", "values": [[""]]})
    for idx, sku, path in applied:
        logger.info("row %d %s -> %s", idx, sku, path)
    logger.info("curated categories to apply: %d", len(applied))
    if DRY_RUN:
        logger.info("DRY RUN - nothing written")
        return
    if updates:
        sheet.batch_update(updates)
        logger.info("Written - these rows retry on the next scheduled run")


if __name__ == "__main__":
    main()
