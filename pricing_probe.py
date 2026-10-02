"""READ-ONLY (2026-10-02): how does the sync read each row's Fee % / Profit % cells?

For the rows in ROWS (TAB = worksheet, default the first product tab) prints the two
cells as typed, what the Supabase mirror last stored, what resolve_pct_cell makes of
them (None = "the automation's own", a number = "a manual override that drives the
formula"), the category commission it expects, and the prices the formula gives with and
without that reading. No cost or shipping is printed (public logs); prices only.
Writes nothing.
"""
import json
import os

os.environ.setdefault("FEE_MODE", "category")

import gspread  # noqa: E402
from oauth2client.service_account import ServiceAccountCredentials  # noqa: E402

import fees  # noqa: E402
import pricing  # noqa: E402
import sheet_tabs  # noqa: E402
import supabase_db  # noqa: E402
from generate_xml import _to_float, resolve_pct_cell  # noqa: E402

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
TAB = (os.getenv("SHEET_TAB") or "").strip()
ROWS = (os.getenv("ROWS") or "").strip()


def parse_rows(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = gspread.authorize(creds).open(SHEET_NAME)
    ws = book.worksheet(TAB) if TAB else sheet_tabs.product_sheet(book)
    values = ws.get_all_values()
    header = [str(h).strip() for h in values[0]]
    ix = {h: i for i, h in enumerate(header) if h}

    def cell(r, k):
        i = ix.get(k)
        return str(r[i]).strip() if i is not None and i < len(r) else ""
    print(f"tab {ws.title!r} | top band profit now {pricing.TOP_BAND_PROFIT:g}% | uplift {pricing.FEE_UPLIFT_PERCENT:g} points")
    for n in parse_rows(ROWS):
        if n - 1 >= len(values):
            continue
        r = values[n - 1]
        sku = cell(r, "SKU")
        mirror = supabase_db.fetch_existing_fields([sku]).get(sku, {}) if sku else {}
        cost, ship = _to_float(cell(r, "Cost Price (£)")), _to_float(cell(r, "Shipping Cost (£)"))
        total = cost + ship
        rule = fees.rule_for_category_path(cell(r, "Category"))
        band_now = pricing.profit_percent(total) if cost > 0 else None
        prev_total = _to_float(mirror.get("Cost Price (£)")) + _to_float(mirror.get("Shipping Cost (£)"))
        band_prev = pricing.profit_percent(prev_total) if prev_total > 0 else None
        p_cell, f_cell = cell(r, "Profit %"), cell(r, "Fee %")
        p_ovr = resolve_pct_cell(p_cell, [band_now, band_prev], mirror.get("Profit %"), hi=500)
        fee_auto = [rule.lower_pct, rule.upper_pct] if rule is not None else [float(pricing.PLATFORM_FEE_PERCENT)]
        f_ovr = resolve_pct_cell(f_cell, fee_auto, mirror.get("Fee %"))
        used = p_ovr if p_ovr is not None else (band_now or 0)
        by_rule = pricing.price_for_profit(total, used, rule=rule) if cost > 0 else 0
        by_flat = pricing.price_for_profit(total, used, platform_fee_percent=f_ovr) if (cost > 0 and f_ovr is not None) else None
        print(f"row {n} SKU {sku}: cells Fee % {f_cell!r} Profit % {p_cell!r} | mirror Fee % {mirror.get('Fee %')!r} "
              f"Profit % {mirror.get('Profit %')!r} | rule {rule!r} (auto fee values {fee_auto}) | "
              f"read as: profit override {p_ovr}, FEE override {f_ovr} | price cell {cell(r, 'Selling Price (£)')} | "
              f"formula at {used:g}% by the category rule {by_rule:.2f}"
              + (f", by the FEE-OVERRIDE reading {by_flat:.2f}" if by_flat is not None else ""))


if __name__ == "__main__":
    main()
