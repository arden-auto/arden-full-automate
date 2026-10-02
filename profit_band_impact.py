"""READ-ONLY (2026-10-02): what moving the top profit band (cost + shipping ABOVE
GBP 50, today 20%) to 15% touches on this store.

For every priced row in that band it prices the row at 20% and at 15% (the
category's real commission when FEE_MODE=category) and says what the sync does
with the sheet's Selling Price cell:
  - the cell holds a price the automation set (the sync's own test: flat-20, category tier or
    any superseded schedule)                                        -> LOWERED to the 15% price
  - the cell holds a price above the formula that no schedule explains
    (a person set it)                                               -> KEPT (never-lower rule)
  - the cell holds a price below the new formula                    -> raised to the new price
  - a typed Profit % that is neither 20 nor 15 is a per-row override -> untouched
The Amazon tab is re-derived from cost in both directions, so every priced row there moves.

Prints aggregates only - no cost or price of an individual product (the
repositories, and so their run logs, are public). Writes nothing.
"""
import json
import os
import statistics

os.environ.setdefault("FEE_MODE", "category")

import gspread  # noqa: E402
from oauth2client.service_account import ServiceAccountCredentials  # noqa: E402

import fees  # noqa: E402
import pricing  # noqa: E402
import sheet_tabs  # noqa: E402
from generate_xml import _formula_priced  # noqa: E402  - the sync's own "did the automation set this price?" test

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
FROM_PCT = float(os.getenv("FROM_PCT") or "20")
TO_PCT = float(os.getenv("TO_PCT") or "15")
NEEDED = ("SKU", "Stock", "Status", "Sync Status", "OnBuy Product Created", "Selling Price (£)",
          "Cost Price (£)", "Shipping Cost (£)", "Category", "Profit %")


def col_letter(n):
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def to_f(v):
    try:
        return float(str(v).replace(",", "").replace("£", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def read_tab(ws):
    header = [str(h).strip() for h in ws.row_values(1)]
    idx = {h: i for i, h in enumerate(header) if h}
    present = [c for c in NEEDED if c in idx]
    ranges = [f"{col_letter(idx[c] + 1)}2:{col_letter(idx[c] + 1)}" for c in present]
    cols = dict(zip(present, ws.batch_get(ranges)))
    n = max((len(v) for v in cols.values()), default=0)

    def cell(c, i):
        v = cols.get(c)
        return "" if v is None or i >= len(v) or not v[i] else str(v[i][0]).strip()
    rows = []
    for i in range(n):
        sku = cell("SKU", i)
        if sku:
            rows.append({"row": i + 2, **{c: cell(c, i) for c in present}})
    return rows


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else 0.0


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = gspread.authorize(creds).open(SHEET_NAME)
    tabs = [sheet_tabs.product_sheet(book)]
    try:
        amz = book.worksheet(sheet_tabs.AMAZON_TAB)
        if amz.title != tabs[0].title:
            tabs.append(amz)
    except gspread.exceptions.WorksheetNotFound:
        pass
    print(f"fee mode: {'category (real commission per category)' if fees.enabled() else 'flat'} | "
          f"band above GBP 50: {FROM_PCT:g}% -> {TO_PCT:g}%")
    for ws in tabs:
        rows = read_tab(ws)
        is_amazon = ws.title == sheet_tabs.AMAZON_TAB
        band, no_cost, override = [], 0, 0
        for r in rows:
            cost, ship = to_f(r.get("Cost Price (£)")), to_f(r.get("Shipping Cost (£)")) or 0.0
            if not cost or cost <= 0:
                no_cost += 1
                continue
            total = cost + ship
            if total <= 50.0:
                continue
            typed = to_f(r.get("Profit %"))
            if typed is not None and abs(typed - FROM_PCT) > 0.05 and abs(typed - TO_PCT) > 0.05:
                override += 1                      # a per-row Profit % override: never touched
                continue
            rule = fees.rule_for_category_path(r.get("Category")) if fees.enabled() else None
            kw = {"rule": rule} if rule is not None else {"platform_fee_percent": pricing.PLATFORM_FEE_PERCENT}
            p_from, p_to = pricing.price_for_profit(total, FROM_PCT, **kw), pricing.price_for_profit(total, TO_PCT, **kw)
            have = to_f(r.get("Selling Price (£)")) or 0.0
            live_in_stock = r.get("OnBuy Product Created", "").upper() == "TRUE" and (to_f(r.get("Stock")) or 0) > 0
            if is_amazon:
                kind = "re-derived" if have > 0 else "unpriced"
                new = p_to
            elif have <= 0:
                kind, new = "unpriced", p_to
            elif abs(have - p_to) < 0.011:
                kind, new = "already at the new price", have
            elif _formula_priced(have, cost, ship, rule) and 0 < p_to < have:
                # the sync lowers a price the automation set itself (flat-20 / category / any superseded schedule)
                kind, new = "lowered", p_to
            elif have >= p_to:
                kind, new = "kept (price above the formula - set by a person)", have
            else:
                kind, new = "raised to the new price", p_to
            band.append({"row": r["row"], "kind": kind, "have": have, "new": new, "live": live_in_stock,
                         "total": total})
        movers = [b for b in band if b["kind"] in ("lowered", "re-derived") and b["have"] > 0 and b["new"] < b["have"] - 0.005]
        print(f"\n=== tab {ws.title!r}: {len(rows)} rows with a SKU, {no_cost} without a cost")
        print(f"rows with cost + shipping above GBP 50 (the band): {len(band) + override} "
              f"(per-row Profit % override, untouched: {override})")
        kinds = {}
        for b in band:
            kinds[b["kind"]] = kinds.get(b["kind"], 0) + 1
        for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"  {n:5d}  {k}")
        if movers:
            drops = [(b["have"] - b["new"]) for b in movers]
            drops_pct = [(b["have"] - b["new"]) / b["have"] * 100 for b in movers]
            live_movers = [b for b in movers if b["live"]]
            print(f"prices that go DOWN: {len(movers)} (live and in stock: {len(live_movers)}) | "
                  f"average drop GBP {statistics.mean(drops):.2f} ({statistics.mean(drops_pct):.1f}%), "
                  f"median {statistics.median(drops_pct):.1f}%, p90 {pct(drops_pct, 0.9):.1f}%, max GBP {max(drops):.2f}")
            sample = [b["row"] for b in live_movers if 50 < b["total"] <= 120][:3]
            print(f"smoke-test rows (live, in stock, price follows the band): {sample}")


if __name__ == "__main__":
    main()
