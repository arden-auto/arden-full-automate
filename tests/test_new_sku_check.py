"""new_sku_check.classify: which freshly typed SKUs would fail on their first sync (2026-10-05)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import new_sku_check as nsc

GOOD, GOOD2 = "4006381333931", "5012345678900"          # GS1-valid EAN-13s (the second is GS1 UK "50", fine)


def r(tab, n, sku, ident="ebay:1", checked=""):
    return {"tab": tab, "row": n, "sku": sku, "url": "u", "ident": ident, "checked": checked, "status": ""}


def why(rows):
    out = {}
    for row, reason in nsc.classify(rows):
        out.setdefault((row["tab"], row["row"]), []).append(reason)
    return out


def test_a_clean_new_row_has_no_problem():
    assert nsc.classify([r("Sheet1", 2, GOOD, "ebay:1"), r("Sheet1", 3, GOOD2, "ebay:2")]) == []


def test_bad_check_digit_and_coupon_range_are_reported():
    out = why([r("Sheet1", 2, "4006381333932", "ebay:1"), r("Sheet1", 3, "512345678904", "ebay:2")])
    assert "not a usable barcode" in out[("Sheet1", 2)][0]
    assert "not a usable barcode" in out[("Sheet1", 3)][0]


def test_shared_digits_and_a_repeated_sku_are_reported():
    out = why([r("Sheet1", 2, GOOD, "ebay:1"), r("Amazon", 2, "GTV-" + GOOD, "ebay:2"), r("Sheet1", 4, GOOD2, "ebay:3"),
               r("Sheet1", 5, GOOD2, "ebay:4")])
    assert any("same barcode digits" in x for x in out[("Sheet1", 2)])
    assert any("same barcode digits" in x for x in out[("Amazon", 2)])
    assert any("exact same SKU" in x for x in out[("Sheet1", 5)])


def test_rows_the_sync_has_already_processed_are_left_alone():
    assert nsc.classify([r("Sheet1", 2, "4006381333932", checked="2026-10-05 10:00:00")]) == []


def test_a_link_an_earlier_row_owns_and_a_link_without_an_id_are_reported():
    rows = [r("Sheet1", 2, GOOD, "ebay:1", checked="2026-10-04 09:00:00"), r("Sheet1", 3, GOOD2, "ebay:1"),
            r("Sheet1", 4, "4006381333948", "")]
    out = why(rows)
    assert "already owned by Sheet1 row 2" in out[("Sheet1", 3)][0]
    assert "no recognisable" in out[("Sheet1", 4)][0]


def test_a_row_with_a_sku_but_no_link_yet_is_called_that():
    row = r("Amazon", 2, GOOD, "")
    row["url"] = ""
    assert why([row])[("Amazon", 2)] == ["no supplier link yet"]
