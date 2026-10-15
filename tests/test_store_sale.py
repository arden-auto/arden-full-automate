"""store_sale.py - the one-click store sale (user 2026-10-15): PERCENT off every priced listing through PUT /v2/listings/by-sku (sale fields only),
cancel = the past window, state in store_sale.json which the nightly sale guard respects."""
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sale_guard
import store_sale as ss


def rec(sku, price, stock=5, sale=None, end=None):
    return {"sku": sku, "price": price, "stock": stock, "sale_price": sale, "sale_end_date": end}


def test_sale_price_is_the_percentage_off_rounded_to_pence_never_below_the_floor():
    assert ss.sale_price_for("9.99", 10) == 8.99 and ss.sale_price_for(100, 12.5) == 87.5 and ss.sale_price_for("0.02", 90) == 0.01
    assert ss.sale_price_for(None, 10) is None and ss.sale_price_for("", 10) is None and ss.sale_price_for("0", 10) is None
    assert ss.percent_value("10") == 10 and ss.percent_value("0.5") is None and ss.percent_value("95") is None and ss.percent_value("") is None


def test_window_starts_yesterday_and_ends_on_the_last_day():
    start, end = ss.window("2026-11-01", today=date(2026, 10, 15))
    assert (start, end) == ("2026-10-14 00:00:00", "2026-11-01 23:59:59")
    assert ss.window("", today=date(2026, 10, 15))[1] == "2026-11-14 23:59:59"          # 30 days by default


def test_plan_apply_skips_priceless_out_of_stock_under_scope_and_listings_already_on_this_sale():
    listings = [rec("A", "10.00"), rec("B", None), rec("C", "20.00", stock=0), rec("D", "10.00", sale="9.00", end="2026-11-01 23:59:59"),
                rec("E", "10.00", sale="9.50", end="2026-11-01 23:59:59"), rec("", "5")]
    items, rows, c = ss.plan_apply(listings, 10, "2026-10-14 00:00:00", "2026-11-01 23:59:59", scope="in_stock")
    assert [it["sku"] for it in items] == ["A", "E"] and c == {"to send": 2, "no price": 1, "out of stock": 1, "already on this sale": 1}
    assert items[0] == {"sku": "A", "boost_marketing_commission": 0, "sale_price": "9.00", "sale_start_date": "2026-10-14 00:00:00", "sale_end_date": "2026-11-01 23:59:59"}
    assert [r[5] for r in rows] == ["sale on", "skip: no price", "skip: out of stock", "already on this sale", "sale changed"]
    items_all, _, c_all = ss.plan_apply(listings, 10, "2026-10-14 00:00:00", "2026-11-01 23:59:59", scope="all")
    assert [it["sku"] for it in items_all] == ["A", "C", "E"] and c_all["out of stock"] == 0


def test_plan_cancel_ends_every_sale_with_the_past_window():
    items, rows = ss.plan_cancel([rec("A", "10.00", sale="9.00"), rec("B", "10.00"), rec("C", "10.00", sale="0")])
    assert items == [{"sku": "A", "boost_marketing_commission": 0, "sale_price": "9.00", "sale_start_date": sale_guard.PAST_START, "sale_end_date": sale_guard.PAST_END}]
    assert rows[0][5] == "sale off"


def test_state_is_active_until_its_last_day(tmp_path):
    path = tmp_path / "store_sale.json"
    ss.save_state({"percent": 10, "sale_end_date": "2026-11-01 23:59:59"}, str(path))
    state = ss.load_state(str(path))
    assert ss.state_active(state, today=date(2026, 11, 1)) and not ss.state_active(state, today=date(2026, 11, 2)) and not ss.state_active(None)
    assert ss.clear_state(str(path)) and ss.load_state(str(path)) is None and not ss.clear_state(str(path))


def test_send_counts_per_sku_answers(monkeypatch):
    calls = []

    def put_chunk(onbuy, items, sleep=None):
        calls.append(len(items))
        return [{"sku": it["sku"], "error": "SKU does not exist"} if it["sku"] == "B" else {"sku": it["sku"]} for it in items], None

    monkeypatch.setattr(sale_guard, "put_chunk", put_chunk)
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    ok, failed, errors = ss.send(SimpleNamespace(), [ss.sale_item(s, 9.0, "x", "y") for s in "ABC"], chunk=2)
    assert (ok, failed, dict(errors), calls) == (2, 1, {"SKU does not exist": 1}, [2, 1])


def test_the_sale_guard_leaves_an_active_store_sale_alone(tmp_path):
    path = tmp_path / "store_sale.json"
    assert sale_guard.store_sale_state(str(path)) is None
    path.write_text(json.dumps({"percent": 10, "sale_end_date": "2026-11-01 23:59:59"}), encoding="utf-8")
    assert sale_guard.store_sale_state(str(path), today="2026-10-15")["percent"] == 10
    assert sale_guard.store_sale_state(str(path), today="2026-11-02") is None                 # expired = the guard ends leftovers again


def test_the_workflow_is_plan_by_default_commits_the_state_and_the_nightly_refreshes():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github" / "workflows" / "store_sale.yml").read_text(encoding="utf-8")
    assert 'default: "plan"' in text and "group: onbuy-api" in text and "contents: write" in text and "python -u store_sale.py" in text
    assert "git add -A -- store_sale.json" in text and "inputs.mode == 'apply' || inputs.mode == 'cancel'" in text
    nightly = (root / ".github" / "workflows" / "nightly_reconcile.yml").read_text(encoding="utf-8")
    assert "MODE: refresh" in nightly and nightly.index("python -u sale_guard.py") < nightly.index("python -u store_sale.py")
