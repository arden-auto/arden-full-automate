"""adoption_guard: a live listing is only adopted by a row that is plausibly the same product."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adoption_guard as ag

ROXEL_ROW = "Roxel Wired Passive Bookshelf Speaker, Wood Effect Cabinet with 4 inch Enhanced Carbon Fibre Woofer, 1Inch Silk Dome Tweeter"
SHARK_PAGE = "Shark WandVac 2.0 Cordless Handheld Vacuum Cleaner, Small & Lightweight, Powerful Suction Handheld Vacuum with Boost Mode"
ROXEL_PAGE = "ROXEL RBS 300 Book Shelf Speaker, Cherry Wood Effect Cabinet with 4 inch Enhanced Carbon Fibre Woofer, 1Inch Silk Dome Tweeter"


def test_the_real_case_is_blocked_with_a_sentence_for_the_sheet():
    msg = ag.adoption_conflict(SHARK_PAGE, ROXEL_ROW)
    assert msg.startswith("SKU already live on OnBuy as a different product (Shark WandVac")
    assert "NEW SKU" in msg and len(msg) < 300


def test_the_same_product_with_a_different_wording_is_allowed():
    assert ag.adoption_conflict(ROXEL_PAGE, ROXEL_ROW) == ""
    assert ag.adoption_conflict("Beldray Smart Window Rechargeable Handheld Vacuum Dust Dirt Tiles Mirror",
                                "Beldray Window Vac - Cordless Condensation Removal Vacuum, 30 Minutes") == ""


@pytest.mark.parametrize("a,b", [
    (ROXEL_PAGE, "Salter Digital Coffee Machine With 420 Ml Travel Mug, 750 W"),
    ("Hand Vacuum Pump Held Brake Bleeder Tester Set", "MPOW Flame Wireless Bluetooth Sports Headphones In-Ear Earbuds"),
    ("OUKITEL WP53S Rugged Smartphone Android 15 16GB", "Shark CryoGlow LED Face Mask with Under-Eye Cooling"),
])
def test_clearly_different_products_are_blocked(a, b):
    assert ag.adoption_conflict(a, b) != ""
    assert ag.adoption_conflict(b, a) != ""                                   # symmetric


@pytest.mark.parametrize("name,title", [("", ROXEL_ROW), (ROXEL_ROW, ""), (None, None), ("  ", "  ")])
def test_an_unknown_name_never_blocks(name, title):
    assert ag.adoption_conflict(name, title) == ""


def test_similarity_is_the_share_of_the_shorter_names_words():
    assert ag.title_similarity("a b c d", "a b") == 1.0
    assert ag.title_similarity("a b c d", "e f") == 0.0
    assert ag.title_similarity("", "a") == 0.0
    assert ag.title_similarity("Shark WandVac", "shark wandvac 2.0 cordless") == 1.0
    assert ag.title_similarity("kettle with lid", "kettle for the lid") == 1.0         # filler words do not count either way


def test_the_threshold_is_below_the_nightly_scans_half_and_above_what_different_products_share():
    assert 0.0 < ag.MIN_SIMILARITY < 0.5
    assert ag.title_similarity(SHARK_PAGE, ROXEL_ROW) < ag.MIN_SIMILARITY


# ---------------------------------------------------------------- the sync uses it
def test_the_sync_checks_a_live_listing_before_adopting_it_and_flags_the_row_without_failing_the_run():
    text = (Path(__file__).resolve().parents[1] / "generate_xml.py").read_text(encoding="utf-8")
    adopt = text.index("adopting via update instead of creating a duplicate")
    guard = text.index("adoption_guard.adoption_conflict(")
    assert guard < adopt                                                   # checked BEFORE the update goes out
    assert "onbuy.get_listing(sku)" in text[guard - 400:guard + 200]
    assert "raise PermanentError(_conflict)" in text[guard:adopt]
    handler = text.index('elif "already live on OnBuy as a different product" in str(exc):')
    assert "onbuy_needs_category += 1" in text[handler:handler + 400]       # worklist, not run_had_errors
    assert "run_had_errors" not in text[handler:handler + 400]
    assert "already live on OnBuy as a different product" in ag.adoption_conflict(SHARK_PAGE, ROXEL_ROW)


# ---------------------------------------------------------------- OnBuyClient.get_listing (the read the guard uses)
class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.headers, self.text = status, body, {}, str(body)

    def json(self):
        return self._body


def _client(answer):
    from onbuy_client import OnBuyClient
    c = OnBuyClient.__new__(OnBuyClient)              # no credentials needed: only _send and site_id are used
    c.site_id = "2000"
    c.calls = []

    def _send(method, url, **kw):
        c.calls.append((method, kw.get("params")))
        return answer
    c._send = _send
    return c


def test_get_listing_returns_the_one_listing_with_that_exact_sku():
    rec = {"sku": "2258075893340-Messam", "name": SHARK_PAGE, "product_encoded_id": "PHMMVQF"}
    c = _client(_Resp(200, {"results": [{"sku": "OTHER"}, rec]}))
    assert c.get_listing("2258075893340-Messam") == rec
    assert c.calls == [("GET", {"site_id": "2000", "limit": 5, "offset": 0, "filter[sku]": "2258075893340-Messam"})]


def test_get_listing_is_none_when_nothing_answers_to_the_sku():
    assert _client(_Resp(200, {"results": []})).get_listing("X-1") is None
    assert _client(_Resp(200, {"results": [{"sku": "NOT-X-1"}]})).get_listing("X-1") is None      # a filter that ignores the SKU


def test_get_listing_refuses_to_guess_between_two_listings():
    from retry_utils import PermanentError
    c = _client(_Resp(200, {"results": [{"sku": "X-1"}, {"sku": "X-1"}]}))
    with pytest.raises(PermanentError):
        c.get_listing("X-1")
