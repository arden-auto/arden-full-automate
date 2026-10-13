"""Put every listing on the delivery template of its supplier (user 2026-10-13, Arden first): eBay-sourced listings on the eBay template, Amazon-sourced on the
Amazon template, everything else (the legacy / AliExpress catalogue) stays on the account's default (AliExpress) template. The templates carry the handling and
delivery times the user set on the OnBuy dashboard; this tool only ASSIGNS listings to them.

MODE=plan (default): sweep the listings (or reuse the nightly's shared sweep, LISTINGS_CACHE), join them to the sheet (first product tab = eBay rows by their
link, Amazon tab = Amazon rows), print the template / handling-time census and how many listings would move - writes nothing.
MODE=test: ONE PUT /v2/listings/by-sku for TEST_SKU carrying the listing's own price and stock plus delivery_template_id, then a filtered read-back: proves
whether the API accepts the field at all (only that listing's template changes).
MODE=apply: batched PUTs (500 per call; the listing's own price and stock, only the template changes) for every listing that is off its supplier's template,
then a read-back of a sample. MAX_CHANGES caps one run.
Env: TEMPLATE_EBAY, TEMPLATE_AMAZON (template ids - test/apply refuse without them), SHEET_NAME, TEST_SKU, MAX_CHANGES (3000), SAMPLE (12), LISTINGS_CACHE.
Prints SKUs, template ids, handling times and counts - never a cost."""
import csv
import json
import os
import time
from collections import Counter

import gspread
from oauth2client.service_account import ServiceAccountCredentials

import listing_sweep
import listings_cache
import sheet_tabs
from onbuy_client import BASE_URL, OnBuyClient
from retry_utils import with_retry

SHEET_NAME = os.getenv("SHEET_NAME") or "Arden_Full_Feed_Master"
MODE = (os.getenv("MODE") or "plan").strip().lower()
TEMPLATE_EBAY = (os.getenv("TEMPLATE_EBAY") or "").strip()
TEMPLATE_AMAZON = (os.getenv("TEMPLATE_AMAZON") or "").strip()
TEST_SKU = (os.getenv("TEST_SKU") or "").strip()
MAX_CHANGES = int(os.getenv("MAX_CHANGES") or "3000")
SAMPLE = int(os.getenv("SAMPLE") or "12")
BATCH = 500


def classify(url):
    """The supplier a sheet row's link points at: ebay | amazon | ali | none | other."""
    u = str(url or "").strip().lower()
    if not u:
        return "none"
    if "/itm/" in u or "ebay." in u:
        return "ebay"
    if "amazon." in u or "/dp/" in u or "amzn." in u:
        return "amazon"
    if "aliexpress" in u:
        return "ali"
    return "other"


def wanted_template(cls, ebay=TEMPLATE_EBAY, amazon=TEMPLATE_AMAZON):
    """The template a listing of this supplier class belongs on, or None (leave it where it is)."""
    if cls == "ebay" and ebay:
        return str(ebay)
    if cls == "amazon" and amazon:
        return str(amazon)
    return None


def plan(listings, classes, ebay=TEMPLATE_EBAY, amazon=TEMPLATE_AMAZON):
    """-> [(sku, cls, current template, wanted template, price, stock)] for every listing that is off its supplier's template."""
    out = []
    for it in listings:
        it = it or {}
        sku = str(it.get("sku") or "").strip()
        if not sku:
            continue
        cls = classes.get(sku, "legacy")
        want = wanted_template(cls, ebay, amazon)
        cur = str(it.get("delivery_template_id") or "").strip()
        if want and cur != want:
            out.append((sku, cls, cur, want, it.get("price"), it.get("stock")))
    return out


def census(listings, classes):
    """Printable facts: templates and handling times overall and per supplier class."""
    lines = [f"listings: {len(listings)} | delivery_template_id: {dict(Counter(str((it or {}).get('delivery_template_id')) for it in listings).most_common())} | "
             f"handling_time: {dict(Counter(str((it or {}).get('handling_time')) for it in listings).most_common())}"]
    by_cls = {}
    for it in listings:
        sku = str((it or {}).get("sku") or "").strip()
        by_cls.setdefault(classes.get(sku, "legacy"), []).append(it)
    for cls in ("ebay", "amazon", "ali", "other", "none", "legacy"):
        its = by_cls.get(cls) or []
        if not its:
            continue
        lines.append(f"  {cls:7s} {len(its):6d} listings | templates {dict(Counter(str(i.get('delivery_template_id')) for i in its).most_common())} | "
                     f"handling {dict(Counter(str(i.get('handling_time')) for i in its).most_common())}")
    return lines


def put_templates(onbuy, items):
    """One PUT /v2/listings/by-sku for [(sku, price, stock, template)] -> (status, {sku: error text or ''}, raw text)."""
    payload = {"site_id": onbuy.site_id, "seller_id": onbuy.seller_id,
               "listings": [{"sku": s, "price": p, "stock": st, "boost_marketing_commission": 0, "delivery_template_id": int(t)} for s, p, st, t in items]}
    resp = onbuy._send("PUT", f"{BASE_URL}/listings/by-sku", what=f"onbuy template update({len(items)})", json=payload, timeout=120)
    outcome = {}
    try:
        body = resp.json()
        results = body.get("results") if isinstance(body, dict) else body
        for r in results or []:
            r = r or {}
            if r.get("sku") is not None:
                outcome[str(r["sku"]).strip()] = str(r.get("error") or "").strip()
    except ValueError:
        pass
    return resp.status_code, outcome, resp.text[:600]


def num(v, cast):
    try:
        return cast(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def sheet_classes(book):
    """{sku: supplier class} from the product tabs: the first tab by its link, the Amazon tab = amazon."""
    classes = {}
    first = sheet_tabs.product_sheet(book)
    tabs = [(first, None)]
    try:
        amz = book.worksheet(sheet_tabs.AMAZON_TAB)
        if amz.title != first.title:
            tabs.append((amz, "amazon"))
    except gspread.exceptions.WorksheetNotFound:
        pass
    for ws, fixed in tabs:
        values = with_retry(ws.get_all_values, what=f"read {ws.title}", max_attempts=3)
        if not values:
            continue
        h = [str(x).strip() for x in values[0]]
        if "SKU" not in h:
            raise SystemExit(f"tab {ws.title!r} has no SKU column")
        i_s, i_u = h.index("SKU"), (h.index("Supplier URL") if "Supplier URL" in h else None)
        for r in values[1:]:
            sku = str(r[i_s]).replace(",", "").strip() if i_s < len(r) else ""
            if not sku:
                continue
            url = r[i_u] if i_u is not None and i_u < len(r) else ""
            classes.setdefault(sku, fixed or classify(url))
    return classes


def main():
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        json.loads(os.environ["GOOGLE_CREDENTIALS"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    book = with_retry(lambda: gspread.authorize(creds).open(SHEET_NAME), what="sheet open", max_attempts=3)
    classes = sheet_classes(book)
    print(f"MODE {MODE} | templates: eBay {TEMPLATE_EBAY or '-'}, Amazon {TEMPLATE_AMAZON or '-'} | sheet SKUs by supplier: {dict(Counter(classes.values()).most_common())}")
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")

    if MODE == "test":
        if not TEST_SKU or not (TEMPLATE_EBAY or TEMPLATE_AMAZON):
            raise SystemExit("test needs TEST_SKU and a template id")
        before = onbuy.get_listing(TEST_SKU)
        if not before:
            raise SystemExit(f"{TEST_SKU}: no listing")
        cls = classes.get(TEST_SKU, "legacy")
        want = wanted_template(cls) or TEMPLATE_EBAY or TEMPLATE_AMAZON
        print(f"{TEST_SKU}: class {cls} | before: template {before.get('delivery_template_id')} handling {before.get('handling_time')} price {before.get('price')} stock {before.get('stock')} | want {want}")
        status, outcome, text = put_templates(onbuy, [(TEST_SKU, num(before.get("price"), float), num(before.get("stock"), int) or 0, want)])
        print(f"PUT -> HTTP {status} | per-SKU answer {outcome} | {text}")
        for wait in (10, 30, 60):
            time.sleep(wait)
            after = onbuy.get_listing(TEST_SKU) or {}
            print(f"read-back after {wait}s: template {after.get('delivery_template_id')} handling {after.get('handling_time')} price {after.get('price')} stock {after.get('stock')} updated_at {after.get('updated_at')}")
            if str(after.get("delivery_template_id")) == str(want):
                print("RESULT: the API accepts delivery_template_id - the template moved")
                return
        print("RESULT: the template did NOT change through the API (field ignored) - the dashboard / a support ticket is the way")
        return

    raw = listings_cache.load() if os.getenv("LISTINGS_CACHE") else None
    if raw is None:
        listings, short, meta, pages = listing_sweep.sweep(onbuy)
        print(f"sweep: {len(listings)} listings in {pages} page(s), short pages {len(short)}, metadata {(meta or {}).get('metadata')}")
    else:
        listings = raw
        print(f"shared sweep: {len(listings)} listings")
    for line in census(listings, classes):
        print(line)
    moves = plan(listings, classes)
    by = Counter((m[1], m[2] or "none", m[3]) for m in moves)
    print(f"TO MOVE: {len(moves)} listing(s): " + ", ".join(f"{c} {cur}->{want}: {n}" for (c, cur, want), n in by.most_common()))
    with open("delivery_template_plan.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sku", "supplier", "current_template", "wanted_template", "stock"])
        for s, c, cur, want, _p, st in moves:
            w.writerow([s, c, cur, want, st])
    if MODE != "apply":
        print("PLAN ONLY - nothing changed.")
        return
    if not moves:
        print("nothing to move")
        return
    if len(moves) > MAX_CHANGES:
        raise SystemExit(f"{len(moves)} moves exceed MAX_CHANGES={MAX_CHANGES} - refusing")
    ok = failed = 0
    errors = Counter()
    for c in range(0, len(moves), BATCH):
        chunk = [(s, num(p, float), num(st, int) or 0, want) for s, _c, _cur, want, p, st in moves[c:c + BATCH] if num(p, float)]
        status, outcome, text = put_templates(onbuy, chunk)
        for s, _p, _st, _t in chunk:
            err = outcome.get(s, "no answer")
            if err:
                failed += 1
                errors[err[:60]] += 1
            else:
                ok += 1
        print(f"batch {c // BATCH + 1}: HTTP {status}, {sum(1 for s, *_ in chunk if not outcome.get(s, 'x'))} ok of {len(chunk)}")
        time.sleep(2)
    print(f"APPLIED: {ok} ok, {failed} failed {dict(errors.most_common(5))}")
    time.sleep(60)
    sample = moves[:SAMPLE]
    moved = 0
    for s, _c, _cur, want, _p, _st in sample:
        after = onbuy.get_listing(s) or {}
        moved += str(after.get("delivery_template_id")) == str(want)
        time.sleep(0.5)
    print(f"read-back sample: {moved} of {len(sample)} already show the new template (OnBuy applies updates with a delay - re-run MODE=plan later for the full count)")


if __name__ == "__main__":
    main()
