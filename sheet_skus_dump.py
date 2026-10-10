"""One-off, READ-ONLY: print every worksheet's SKU column of this store's
sheet, between per-tab BEGIN/END markers - the sheet-side pardon list for
cleanup joins against the OnBuy account's live listings (a dashboard
export can hold legacy listings no sheet row ever produced).
Reads the sheet only; writes nothing anywhere."""
import json
import os

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
SHEET_ID = (os.getenv("SHEET_ID") or "").strip()  # takes precedence: exact spreadsheet by key


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    book = client.open_by_key(SHEET_ID) if SHEET_ID else client.open(SHEET_NAME)
    print(f"spreadsheet: {book.title}")
    for tab in book.worksheets():
        headers = [str(x).strip() for x in tab.row_values(1)]
        print(f"TAB {tab.title} HEADERS: {headers}")
        if "SKU" not in headers:
            print(f"TAB {tab.title}: no SKU column")
            continue
        # SKU plus every identifier-ish column - the OnBuy listings may be
        # keyed by a barcode/decorated value rather than this sheet's SKU.
        wanted = ["SKU"] + [h for h in headers if h != "SKU" and any(
            k in h.lower() for k in ("ean", "gtin", "barcode", "product code", "onbuy", "brand"))]
        for col in wanted:
            vals = [str(v).replace(",", "").strip() for v in tab.col_values(headers.index(col) + 1)[1:]]
            vals = [v for v in vals if v]
            print(f"TAB {tab.title} COLUMN {col}: {len(vals)} value(s)")
            print(f"BEGIN-COL {tab.title}|{col}")
            for v in vals:
                print(v)
            print(f"END-COL {tab.title}|{col}")
        # Row-wise correlation lines - the per-column dumps drop empties, so
        # they cannot say which EAN belongs to which brand or SKU.
        for idx, r in enumerate(tab.get_all_records()):
            sku = str(r.get("SKU") or "").replace(",", "").strip()
            ean = str(r.get("EAN") or "").replace(",", "").strip()
            br = str(r.get("Brand") or "").strip()
            if sku or ean:
                print(f"ROW|{tab.title}|{idx + 2}|{sku}|{ean}|{br}")


if __name__ == "__main__":
    main()
