"""STORE SALE (user 2026-10-15): one click puts a sale percentage on every listing of the store, one click takes it off again - both through the API,
both within minutes (the OnBuy dashboard starts a sale at once but takes 1-2 days to end one).

How a sale works on OnBuy (our own findings, 2026-10-10 + the test of 2026-10-15): a listing carries price + sale_price + sale_start_date /
sale_end_date ("YYYY-MM-DD HH:MM:SS"); while the window is open the shop sells at the sale price and shows "On Sale". PUT /v2/listings/by-sku takes the
three sale fields on their own (no price, no stock, so it never races with the sync, which only ever sends price / stock). There is no empty spelling
to END a sale: send the listing's own sale price with a window in the past - OnBuy drops it within about a minute. Read-backs lag by seconds to minutes.

MODE=plan (default): sweep (or the nightly's shared sweep), census of the sales on the account, what apply would send - writes nothing.
MODE=test: ONE listing (TEST_SKU): put the sale on, read back until it shows, take it off, read back until it is gone - prints the timeline.
MODE=apply: PERCENT off every listing with a price (SCOPE=all | in_stock), window yesterday 00:00:00 .. SALE_END 23:59:59; idempotent (a listing that
already carries exactly this sale is skipped); writes store_sale.json - the state the nightly sale guard respects and the refresh step reads.
MODE=cancel: end the sale on every listing that carries one (past window) and delete store_sale.json.
MODE=refresh (nightly, while store_sale.json is active): listings created since and listings whose price changed get the sale recomputed; an expired
state does nothing (OnBuy drops an expired sale itself; the guard ends leftovers).
Guards: PERCENT 1..90, listings without a numeric price are skipped, MAX_CHANGES (0 = no cap) refuses bigger runs, sale price never below MIN_SALE_PRICE.
Env: MODE, PERCENT, SALE_END (YYYY-MM-DD, default today + 30 days), SCOPE, TEST_SKU, MAX_CHANGES, CHUNK (500), LISTINGS_CACHE, STATE_FILE
(store_sale.json), SAMPLE (8), MIN_SALE_PRICE (0.01). Prints SKUs, prices and counts - never a cost."""
import csv
import json
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import listing_sweep
import listings_cache
import sale_guard
from onbuy_client import OnBuyClient

MODE = (os.getenv("MODE") or "plan").strip().lower()
PERCENT = (os.getenv("PERCENT") or "").strip()
SALE_END = (os.getenv("SALE_END") or "").strip()
SCOPE = (os.getenv("SCOPE") or "all").strip().lower()
TEST_SKU = (os.getenv("TEST_SKU") or "").strip()
MAX_CHANGES = int(os.getenv("MAX_CHANGES") or "0")
CHUNK = int(os.getenv("CHUNK") or "500")
STATE_FILE = (os.getenv("STATE_FILE") or "store_sale.json").strip()
SAMPLE = int(os.getenv("SAMPLE") or "8")
MIN_SALE_PRICE = float(os.getenv("MIN_SALE_PRICE") or "0.01")
PLAN_CSV = "store_sale_plan.csv"


def to_float(v):
    try:
        return float(str(v).replace(",", "").replace("£", "").strip())
    except (TypeError, ValueError):
        return None


def percent_value(text):
    """The sale percentage as a number, 1..90, or None."""
    p = to_float(text)
    return p if p is not None and 1 <= p <= 90 else None


def sale_price_for(price, percent, floor=MIN_SALE_PRICE):
    """The sale price for a listing price at this percentage (2 decimals, never below the floor), or None without a usable price."""
    p = to_float(price)
    if p is None or p <= 0:
        return None
    return max(round(p * (1 - percent / 100.0) + 1e-9, 2), floor)


def window(end_date, today=None):
    """(sale_start_date, sale_end_date): yesterday 00:00:00 (a start in the past = on at once) to end_date 23:59:59."""
    today = today or date.today()
    end = end_date or (today + timedelta(days=30)).isoformat()
    datetime.strptime(end, "%Y-%m-%d")                    # a wrong date fails here, loudly
    return f"{(today - timedelta(days=1)).isoformat()} 00:00:00", f"{end} 23:59:59"


def sale_item(sku, sale, start, end):
    return {"sku": str(sku).strip(), "boost_marketing_commission": 0, "sale_price": f"{sale:.2f}", "sale_start_date": start, "sale_end_date": end}


def ending_item(sku, sale):
    """The update that ends a sale: the sale price with the past window (the spelling OnBuy accepted on 2026-10-10)."""
    return {"sku": str(sku).strip(), "boost_marketing_commission": 0, "sale_price": f"{max(sale or 0.01, 0.01):.2f}",
            "sale_start_date": sale_guard.PAST_START, "sale_end_date": sale_guard.PAST_END}


def on_this_sale(rec, sale, end):
    """True when the listing already carries exactly this sale (same sale price, same end day)."""
    rec = rec or {}
    cur = to_float(rec.get("sale_price"))
    return cur is not None and abs(cur - sale) < 0.005 and str(rec.get("sale_end_date") or "")[:10] == end[:10]


def plan_apply(listings, percent, start, end, scope="all"):
    """-> (items to send, rows for the plan CSV, counters). A listing without a numeric price, out of stock under scope=in_stock, or already on exactly
    this sale sends nothing."""
    items, rows, c = [], [], Counter()
    for rec in listings:
        rec = rec or {}
        sku = str(rec.get("sku") or "").strip()
        if not sku:
            continue
        sale = sale_price_for(rec.get("price"), percent)
        stock = int(to_float(rec.get("stock")) or 0)
        if sale is None:
            c["no price"] += 1
            action = "skip: no price"
        elif scope == "in_stock" and stock <= 0:
            c["out of stock"] += 1
            action = "skip: out of stock"
        elif on_this_sale(rec, sale, end):
            c["already on this sale"] += 1
            action = "already on this sale"
        else:
            c["to send"] += 1
            action = "sale on" if not listing_sweep.has_sale(rec) else "sale changed"
            items.append(sale_item(sku, sale, start, end))
        rows.append([sku, rec.get("price"), stock, rec.get("sale_price"), "" if sale is None else f"{sale:.2f}", action])
    return items, rows, c


def plan_cancel(listings):
    """-> (items ending every sale on the account, rows for the plan CSV)."""
    items, rows = [], []
    for rec in listings:
        rec = rec or {}
        sku = str(rec.get("sku") or "").strip()
        if sku and listing_sweep.has_sale(rec):
            items.append(ending_item(sku, to_float(rec.get("sale_price"))))
            rows.append([sku, rec.get("price"), rec.get("stock"), rec.get("sale_price"), "", "sale off"])
    return items, rows


def load_state(path=STATE_FILE):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def state_active(state, today=None):
    today = (today or date.today()).isoformat()
    return bool(state) and str(state.get("sale_end_date") or "")[:10] >= today


def save_state(state, path=STATE_FILE):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)


def clear_state(path=STATE_FILE):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def write_plan(rows, path=PLAN_CSV):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sku", "price", "stock", "sale_price_now", "sale_price_wanted", "action"])
        w.writerows(rows)


def send(onbuy, items, chunk=CHUNK):
    """PUT the items in chunks -> (ok, failed, Counter of error texts)."""
    ok = failed = 0
    errors = Counter()
    for i in range(0, len(items), chunk):
        part = items[i:i + chunk]
        results, err = sale_guard.put_chunk(onbuy, part)
        if results is None:
            failed += len(part)
            errors[str(err)[:80]] += len(part)
            print(f"chunk {i // chunk + 1}: request failed - {err}", flush=True)
            continue
        answered = {str((r or {}).get("sku") or "").strip(): (r or {}) for r in results}
        bad = 0
        for it in part:
            r = answered.get(it["sku"])
            e = str((r or {}).get("error") or "").strip() if r is not None else "no answer"
            if e:
                bad += 1
                errors[e[:80]] += 1
            else:
                ok += 1
        failed += bad
        print(f"chunk {i // chunk + 1}: {len(part) - bad} ok of {len(part)}", flush=True)
        time.sleep(2)
    return ok, failed, errors


def read_back(onbuy, sku):
    rec = onbuy.get_listing(sku) or {}
    return {k: rec.get(k) for k in ("price", "stock", "sale_price", "sale_start_date", "sale_end_date", "updated_at")}


def wait_until(onbuy, sku, test, waits, label):
    """Read the listing back after each wait until test(rec) holds; prints the timeline, returns the seconds it took or None."""
    total = 0
    for w in waits:
        time.sleep(w)
        total += w
        rec = onbuy.get_listing(sku) or {}
        state = {k: rec.get(k) for k in ("price", "sale_price", "sale_start_date", "sale_end_date", "updated_at")}
        print(f"  {label}: read-back after {total}s: {json.dumps(state, default=str)}", flush=True)
        if test(rec):
            return total
    return None


def main():
    percent = percent_value(PERCENT)
    state = load_state()
    if MODE in ("apply", "test") and percent is None:
        raise SystemExit("PERCENT must be a number from 1 to 90")
    if MODE == "refresh":
        if not state_active(state):
            print("no active store sale (store_sale.json absent or expired) - nothing to refresh")
            return
        percent = percent_value(state.get("percent"))
        start, end = state["sale_start_date"], state["sale_end_date"]
    else:
        start, end = window(SALE_END)
    print(f"MODE {MODE} | percent {percent if percent is not None else '-'} | window {start} .. {end} | scope {SCOPE} | state: "
          + (f"ACTIVE {state.get('percent')}% until {str(state.get('sale_end_date'))[:10]}" if state_active(state) else ("expired" if state else "none")))
    onbuy = OnBuyClient()
    if not onbuy.authenticate():
        raise SystemExit("OnBuy auth failed")

    if MODE == "test":
        if not TEST_SKU:
            raise SystemExit("test needs TEST_SKU")
        before = onbuy.get_listing(TEST_SKU)
        if not before:
            raise SystemExit(f"{TEST_SKU}: no listing")
        sale = sale_price_for(before.get("price"), percent)
        if sale is None:
            raise SystemExit(f"{TEST_SKU}: no numeric price ({before.get('price')})")
        print(f"{TEST_SKU}: before {json.dumps(read_back(onbuy, TEST_SKU), default=str)} -> sale {sale:.2f} ({percent}% off {before.get('price')})")
        t0 = time.time()
        ok, failed, errors = send(onbuy, [sale_item(TEST_SKU, sale, start, end)])
        print(f"SALE ON sent: {ok} ok, {failed} failed {dict(errors)} ({time.time() - t0:.0f}s)")
        shown = wait_until(onbuy, TEST_SKU, lambda r: on_this_sale(r, sale, end), (15, 15, 30, 60, 60, 120), "sale on")
        print(f"RESULT sale on: {'visible after ' + str(shown) + 's' if shown else 'NOT visible within 5 min'}")
        t1 = time.time()
        ok, failed, errors = send(onbuy, [ending_item(TEST_SKU, sale)])
        print(f"SALE OFF sent: {ok} ok, {failed} failed {dict(errors)} ({time.time() - t1:.0f}s)")
        gone = wait_until(onbuy, TEST_SKU, lambda r: not listing_sweep.has_sale(r), (30, 30, 60, 60, 120, 120), "sale off")
        print(f"RESULT sale off: {'gone after ' + str(gone) + 's' if gone else 'STILL THERE after 7 min'}")
        print(f"{TEST_SKU}: after {json.dumps(read_back(onbuy, TEST_SKU), default=str)}")
        return

    raw = listings_cache.load() if os.getenv("LISTINGS_CACHE") else None
    if raw is None:
        listings, short, meta, pages = listing_sweep.sweep(onbuy)
        print(f"sweep: {len(listings)} listings in {pages} page(s), short pages {len(short)}, metadata {(meta or {}).get('metadata')}")
    else:
        listings = raw
        print(f"shared sweep: {len(listings)} listings")
    on_sale = [r for r in listings if listing_sweep.has_sale(r)]
    print(f"sales on the account now: {len(on_sale)} of {len(listings)} listings | end dates {dict(Counter(str(r.get('sale_end_date'))[:10] for r in on_sale).most_common(4))}")

    if MODE == "cancel":
        items, rows = plan_cancel(listings)
        write_plan(rows)
        print(f"TO END: {len(items)} sale(s)")
        if MAX_CHANGES and len(items) > MAX_CHANGES:
            raise SystemExit(f"{len(items)} exceed MAX_CHANGES={MAX_CHANGES} - refusing")
        ok, failed, errors = send(onbuy, items) if items else (0, 0, Counter())
        removed = clear_state()
        print(f"CANCELLED: {ok} ok, {failed} failed {dict(errors.most_common(5))} | store_sale.json {'removed' if removed else 'was not there'}")
        if items:
            sample = [it["sku"] for it in items[:SAMPLE]]
            time.sleep(90)
            gone = sum(1 for s in sample if not listing_sweep.has_sale(onbuy.get_listing(s) or {}))
            print(f"read-back after 90s: {gone} of {len(sample)} already show no sale (OnBuy drops an ended sale within a few minutes)")
        return

    if MODE in ("plan", "apply", "refresh"):
        if percent is None:
            print("no PERCENT given - census only")
            write_plan([[r.get("sku"), r.get("price"), r.get("stock"), r.get("sale_price"), "", "on sale now"] for r in on_sale])
            print("PLAN ONLY - nothing changed.")
            return
        items, rows, c = plan_apply(listings, percent, start, end, SCOPE)
        write_plan(rows)
        print(f"PLAN {percent}% off: {dict(c)} -> {len(items)} update(s) in {-(-len(items) // CHUNK)} request(s)")
        if MODE == "plan":
            print("PLAN ONLY - nothing changed.")
            return
        if MAX_CHANGES and len(items) > MAX_CHANGES:
            raise SystemExit(f"{len(items)} updates exceed MAX_CHANGES={MAX_CHANGES} - refusing")
        if MODE == "apply":
            save_state({"percent": percent, "scope": SCOPE, "sale_start_date": start, "sale_end_date": end,
                        "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"), "listings": len(items)})
            print(f"store_sale.json written ({percent}% until {end[:10]})")
        if not items:
            print("nothing to send - every listing already carries this sale")
            return
        ok, failed, errors = send(onbuy, items)
        print(f"APPLIED: {ok} ok, {failed} failed {dict(errors.most_common(5))}")
        sample = [it["sku"] for it in items[:SAMPLE]]
        time.sleep(60)
        shown = sum(1 for s in sample if on_this_sale(onbuy.get_listing(s) or {}, to_float(next(it["sale_price"] for it in items if it["sku"] == s)), end))
        print(f"read-back after 60s: {shown} of {len(sample)} already show the sale (OnBuy applies updates with a delay)")
        return
    raise SystemExit(f"unknown MODE {MODE!r}")


if __name__ == "__main__":
    main()
