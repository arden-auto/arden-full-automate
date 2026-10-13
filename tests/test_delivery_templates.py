"""delivery_templates.py - every listing on the delivery template of its supplier (user 2026-10-13): eBay rows -> the eBay template, Amazon rows -> the Amazon
template, everything else (legacy / AliExpress catalogue) stays where it is."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import delivery_templates as dt


def test_classify_reads_the_supplier_off_the_link():
    assert dt.classify("https://www.ebay.co.uk/itm/123456789012") == "ebay"
    assert dt.classify("https://www.amazon.co.uk/dp/B0ABCDEFGH") == "amazon"
    assert dt.classify("https://amzn.eu/d/abc") == "amazon"
    assert dt.classify("https://www.aliexpress.com/item/1005.html") == "ali"
    assert dt.classify("") == "none" and dt.classify("https://example.com/x") == "other"


def test_only_ebay_and_amazon_listings_have_a_wanted_template():
    assert dt.wanted_template("ebay", "54810", "54809") == "54810"
    assert dt.wanted_template("amazon", "54810", "54809") == "54809"
    for cls in ("ali", "none", "other", "legacy"):
        assert dt.wanted_template(cls, "54810", "54809") is None
    assert dt.wanted_template("ebay", "", "54809") is None                       # no id configured = nothing moves


def listing(sku, template, price="9.99", stock=3, handling=0):
    return {"sku": sku, "delivery_template_id": template, "price": price, "stock": stock, "handling_time": handling}


def test_plan_moves_only_what_is_off_its_suppliers_template():
    listings = [listing("E1", 52506), listing("E2", 54810), listing("A1", 52506), listing("A2", 54810), listing("L1", 52506), listing("", 52506)]
    classes = {"E1": "ebay", "E2": "ebay", "A1": "amazon", "A2": "amazon"}
    moves = dt.plan(listings, classes, "54810", "54809")
    assert [(m[0], m[2], m[3]) for m in moves] == [("E1", "52506", "54810"), ("A1", "52506", "54809"), ("A2", "54810", "54809")]
    assert moves[0][4:] == ("9.99", 3)                                            # the listing's own price and stock travel with the move


def test_census_counts_templates_and_handling_per_supplier_class():
    listings = [listing("E1", 52506, handling=0), listing("E2", 54810, handling=0), listing("L1", 52506, handling=5)]
    lines = dt.census(listings, {"E1": "ebay", "E2": "ebay"})
    assert "listings: 3" in lines[0] and "'52506': 2" in lines[0] and "'5': 1" in lines[0]
    assert any(l.strip().startswith("ebay") and "2 listings" in l and "'54810': 1" in l for l in lines)
    assert any(l.strip().startswith("legacy") and "1 listings" in l for l in lines)


def test_put_templates_sends_the_listings_own_price_and_stock_plus_the_template_and_reads_each_answer():
    sent = {}

    def _send(method, url, what=None, json=None, timeout=None):
        sent.update(method=method, url=url, body=json)
        return SimpleNamespace(status_code=200, text="ok",
                               json=lambda: {"results": [{"sku": "E1", "status": "ok"}, {"sku": "E2", "error": "SKU does not exist"}]})

    onbuy = SimpleNamespace(_send=_send, site_id=2000, seller_id=7)
    status, outcome, _ = dt.put_templates(onbuy, [("E1", 9.99, 3, "54810"), ("E2", 5.0, 0, "54810")])
    assert status == 200 and outcome == {"E1": "", "E2": "SKU does not exist"}
    assert sent["method"] == "PUT" and sent["url"].endswith("/listings/by-sku")
    assert sent["body"]["listings"][0] == {"sku": "E1", "price": 9.99, "stock": 3, "boost_marketing_commission": 0, "delivery_template_id": 54810}


def test_num_tolerates_strings_and_blanks():
    assert dt.num("243.83", float) == 243.83 and dt.num("6", int) == 6 and dt.num("", int) is None and dt.num(None, float) is None


def test_the_workflow_is_plan_by_default_and_serialised_with_the_other_onbuy_jobs():
    text = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "delivery_templates.yml").read_text(encoding="utf-8")
    assert "group: onbuy-api" in text and 'default: "plan"' in text and "python -u delivery_templates.py" in text
