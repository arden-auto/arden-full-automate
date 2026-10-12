"""even_margins.py - the one-off that puts every priced row on the profit schedule (user 2026-10-12: "the old already listed listings ... have more
margins than we fixed ... apply the profit margins evenly on all the product rows").

The tool reads the sync's own decision (pricing_probe.decision -> generate_xml.decide_price) and acts on the Selling Price: lower a price above the schedule,
raise one below it, HOLD an extreme outlier, leave typed overrides / defended / frozen rows / an Amazon price raised by hand alone. What it writes must read back
as the automation's own values (never as typed overrides) so the next sync visit keeps the price it set.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import even_margins as em  # noqa: E402
import pricing  # noqa: E402
import pricing_probe  # noqa: E402

RULE15 = pricing.FeeRule("Test 15%", 15)
TIERED = pricing.FeeRule("Test tiered", 15, upper_pct=8, threshold=100, min_fee=0.25)


def cells_of(**over):
    base = {"SKU": "111", "Title": "A product", "Cost Price (£)": "30", "Shipping Cost (£)": "", "Selling Price (£)": "0", "Fee %": "", "Profit %": "",
            "Category": "", "Stock": "5", "Sync Status": "Synced", "Price Check Flag": "Normal"}
    base.update(over)
    return lambda k: str(base.get(k, ""))


def decide(amazon=False, mirror=None, **over):
    return pricing_probe.decision(cells_of(**over), mirror or {}, amazon)


def schedule_price(cost=30.0, rule=None):
    return pricing.calculate_selling_price(cost, 0.0, fee_rule=rule, platform_fee_percent=None if rule else pricing.PLATFORM_FEE_PERCENT)


# ---------------------------------------------------------------- plan_row
def test_a_legacy_high_price_is_lowered_to_the_schedule():
    sched = schedule_price(30)
    # the cell shows the REAL profit of the old price (74.06); the mirror holds the whole number the sync stored with it, which is how the cell is
    # recognised as the automation's own and not as a typed override (production: row 32 reads "profit override None")
    d = decide(mirror={"Profit %": "74"}, **{"Selling Price (£)": f"{sched * 1.4:.2f}", "Profit %": "74.06"})
    assert d["how"] == "kept" and d["existing"] > d["formula_price"]                          # the sync would keep it (never-lower rule)
    action, note = em.plan_row(d)
    assert action == "lower" and note.startswith("x1.4")
    assert em.new_cells(d, 5)["Selling Price (£)"] == round(d["formula_price"], 2) == sched


def test_a_price_on_the_schedule_is_left_alone():
    assert em.plan_row(decide(**{"Selling Price (£)": f"{schedule_price(30):.2f}"}))[0] == "ok"
    assert em.plan_row(decide(**{"Selling Price (£)": f"{schedule_price(30) + 0.01:.2f}"}))[0] == "ok"      # a penny is the sync's own tolerance


def test_a_price_below_the_schedule_is_raised():
    action, note = em.plan_row(decide(**{"Selling Price (£)": f"{schedule_price(30) * 0.9:.2f}"}))
    assert action == "raise" and note.startswith("x0.9")


def test_a_typed_profit_percent_stays_exactly_as_typed():
    d = decide(**{"Selling Price (£)": f"{schedule_price(30) * 1.5:.2f}", "Profit %": "150"})
    assert d["profit_override"] == 150
    assert em.plan_row(d) == ("skip", "typed Profit % / Fee % override")


def test_a_typed_fee_percent_stays_exactly_as_typed():
    d = decide(**{"Selling Price (£)": f"{schedule_price(30) * 1.5:.2f}", "Fee %": "12"})
    assert d["fee_override"] == 12
    assert em.plan_row(d)[0] == "skip"


def test_frozen_wrong_content_listings_are_never_touched():
    d = decide(**{"Selling Price (£)": f"{schedule_price(30) * 1.4:.2f}"})
    assert em.plan_row(d, frozen=True) == ("skip", "frozen wrong-content listing")
    assert em.plan_row(d)[0] == "lower"


def test_an_extreme_outlier_is_held_for_review_not_lowered():
    d = decide(**{"Selling Price (£)": f"{schedule_price(30) * 3:.2f}"})
    action, note = em.plan_row(d)
    assert action == "hold" and "review" in note
    assert em.plan_row(d, outlier_ratio=4.0)[0] == "lower"                                     # the threshold is the operator's call
    just_under = decide(**{"Selling Price (£)": f"{schedule_price(30) * 1.99:.2f}"})
    assert em.plan_row(just_under)[0] == "lower"


def test_an_amazon_price_raised_by_hand_stays_but_the_automations_own_price_is_evened():
    sched = schedule_price(30)
    by_hand = decide(amazon=True, **{"Selling Price (£)": f"{sched * 1.2:.2f}"})
    assert by_hand["how"] == "manual"
    assert em.plan_row(by_hand, amazon=True)[0] == "skip"                                      # the user's 2026-10-07 rule: it sticks
    old_schedule = pricing.price_for_profit(30, 40 + 0, platform_fee_percent=pricing.PLATFORM_FEE_PERCENT)     # an automation-set price (same band, so equal)
    assert em.plan_row(decide(amazon=True, **{"Selling Price (£)": f"{old_schedule:.2f}"}), amazon=True)[0] == "ok"
    # a price the automation set under a SUPERSEDED band (cost > 50: 20% before the 25%) is the sync's own and follows up
    cost = 60.0
    at_20 = pricing.price_for_profit(cost, 20, platform_fee_percent=pricing.PLATFORM_FEE_PERCENT)
    action, _ = em.plan_row(decide(amazon=True, **{"Cost Price (£)": "60", "Selling Price (£)": f"{at_20:.2f}"}), amazon=True)
    assert action == "raise"


def test_a_row_without_a_cost_or_price_is_skipped():
    assert em.plan_row(None) == ("skip", "no cost")
    assert em.plan_row(decide(**{"Selling Price (£)": "0"}))[0] == "skip"


def test_the_band_boundaries_pick_the_scheduled_profit():
    for cost, band in ((3, 100), (7, 80), (30, 40), (80, 25)):
        d = decide(**{"Cost Price (£)": str(cost), "Selling Price (£)": "9999"})
        assert d["band_now"] == band
        assert em.new_cells(d, 5)["Profit %"] == f"{band:.2f}"


# ---------------------------------------------------------------- the cells it writes
def test_new_cells_are_what_the_sync_itself_writes():
    d = decide(**{"Cost Price (£)": "60", "Selling Price (£)": "300"})
    cells = em.new_cells(d, 5)
    price = round(d["formula_price"], 2)
    assert cells["Selling Price (£)"] == price
    assert cells["Profit %"] == "25.00"
    assert cells["Fee %"] == f"{pricing.effective_fee_percent(price, d['rule']):.2f}"
    assert cells["Price Check Flag"] == "Normal"
    assert em.new_cells(d, 0)["Price Check Flag"] == ""                                         # an out-of-stock row carries no flag, like the sync's


@pytest.mark.parametrize("rule", [None, RULE15, TIERED])
@pytest.mark.parametrize("cost", [3.0, 8.0, 30.0, 49.0, 55.0, 90.0, 400.0])
@pytest.mark.parametrize("stale_mirror", [False, True])
def test_what_it_writes_reads_back_as_the_automations_own_so_the_next_visit_keeps_the_price(monkeypatch, rule, cost, stale_mirror):
    fake_fees = SimpleNamespace(enabled=lambda: rule is not None, rule_for_category_path=lambda p: rule, rule_for_category_id=lambda i: rule)
    monkeypatch.setattr(pricing_probe, "fees", fake_fees)
    old_price = f"{pricing.calculate_selling_price(cost, 0.0, fee_rule=rule) * 1.45:.2f}"
    before = decide(**{"Cost Price (£)": str(cost), "Selling Price (£)": old_price, "Category": "Some > Path"})
    assert em.plan_row(before)[0] == "lower"
    new = em.new_cells(before, 5)
    # the mirror still holds the OLD whole-number cells until the next visit upserts it (or nothing at all)
    mirror = {"Profit %": "74", "Fee %": "18", "Cost Price (£)": cost, "Shipping Cost (£)": ""} if stale_mirror else {}
    after = decide(mirror=mirror, **{"Cost Price (£)": str(cost), "Category": "Some > Path", "Selling Price (£)": str(new["Selling Price (£)"]),
                                     "Profit %": new["Profit %"], "Fee %": new["Fee %"]})
    assert after["profit_override"] is None and after["fee_override"] is None
    assert after["selling_price"] == pytest.approx(new["Selling Price (£)"], abs=0.011)         # the sync keeps exactly what the tool set
    assert em.plan_row(after)[0] == "ok"                                                         # and a second run changes nothing


def test_evening_is_idempotent_on_a_row_the_sync_already_priced_on_the_schedule():
    d = decide(**{"Selling Price (£)": f"{schedule_price(30):.2f}", "Profit %": "40.00", "Fee %": "24.00"})
    assert em.plan_row(d)[0] == "ok"


# ---------------------------------------------------------------- backup rows + the write
def row(n, sku, action, d, stock=5):
    return {"n": n, "sku": sku, "title": "T" + sku, "action": action, "note": "x", "d": d, "stock": stock, "status": "Synced",
            "old": {"Selling Price (£)": f"{d['existing']:.2f}", "Profit %": "74.06", "Fee %": "18.00", "Price Check Flag": "High"}}


def test_the_backup_tab_keeps_every_changed_and_held_row_with_old_and_new_values():
    high = decide(**{"Selling Price (£)": f"{schedule_price(30) * 1.4:.2f}"})
    out = decide(**{"Selling Price (£)": f"{schedule_price(30) * 3:.2f}"})
    ok = decide(**{"Selling Price (£)": f"{schedule_price(30):.2f}"})
    skipped = {"n": 8, "sku": "D", "title": "TD", "action": "skip", "note": "no cost", "d": None, "stock": 5, "status": "", "old": {}}
    rows = [row(5, "A", "lower", high), row(6, "B", "hold", out), row(7, "C", "ok", ok), skipped]
    body = em.backup_rows("Sheet1", rows)
    assert [b[2] for b in body] == ["A", "B"]                                                   # ok / skip rows are not backed up
    assert all(len(b) == len(em.BACKUP_HEADER) for b in body)
    a, b = body
    assert a[4] == "lower" and a[6] == round(high["formula_price"], 2) and a[8] == "74.06" and a[9] == "40.00"
    assert b[4] == "hold" and b[6] == ""                                                        # a held row has no new price


class FakeSheet:
    """Just enough of a worksheet for apply_tab: column reads and batch writes."""

    def __init__(self, title, values, header):
        self.title, self.values, self.header = title, values, header
        self.writes = []

    def col_values(self, n):
        return [r[n - 1] if n - 1 < len(r) else "" for r in self.values]

    def batch_update(self, data, **kw):
        self.writes.extend((d["range"], d["values"][0][0]) for d in data)


def make_sheet(prices):
    header = ["SKU", "Title", "Cost Price (£)", "Selling Price (£)", "Profit %", "Fee %", "Price Check Flag", "Stock"]
    values = [header] + [[f"S{i}", "T", "30", str(p), "74.06", "18.00", "High", "5"] for i, p in enumerate(prices, start=2)]
    ix = {h: i for i, h in enumerate(header)}
    snapshot = [list(r) for r in values]                                                        # what the tool read at its start; the live sheet may change after
    return FakeSheet("Sheet1", values, header), (ix, snapshot)


def targets_for(prices):
    out = []
    for i, p in enumerate(prices, start=2):
        d = decide(**{"SKU": f"S{i}", "Selling Price (£)": str(p)})
        out.append(row(i, f"S{i}", "lower", d))
    return out


def test_apply_writes_price_profit_fee_and_flag_for_every_changed_row():
    ws, snap = make_sheet([80.0, 90.0])
    n_written, n_skipped = em.apply_tab(ws, targets_for([80.0, 90.0]), snap)
    assert (n_written, n_skipped) == (2, 0)
    written = dict(ws.writes)
    sched = round(schedule_price(30), 2)
    assert written["D2"] == sched and written["D3"] == sched                                    # Selling Price (£)
    assert written["E2"] == "40.00" and written["F2"].replace(".", "").isdigit()                # Profit %, Fee %
    assert written["G2"] == "Normal"                                                            # Price Check Flag


def test_a_row_edited_while_the_tool_ran_is_left_entirely_alone():
    ws, snap = make_sheet([80.0, 90.0])
    ws.values[2][3] = "95.5"                                                                    # somebody typed a new price into row 3 after the snapshot
    n_written, n_skipped = em.apply_tab(ws, targets_for([80.0, 90.0]), snap)
    assert (n_written, n_skipped) == (1, 1)
    rows_written = {rng.lstrip("ABCDEFGHIJ") for rng, _ in ws.writes}
    assert rows_written == {"2"}                                                                # no half-written row: price, Profit %, Fee % and flag all skipped


def test_a_row_that_moved_is_not_written():
    ws, snap = make_sheet([80.0, 90.0])
    ws.values[1], ws.values[2] = ws.values[2], ws.values[1]                                     # rows swapped under us: SKUs no longer match their snapshot rows
    n_written, n_skipped = em.apply_tab(ws, targets_for([80.0, 90.0]), snap)
    assert n_written == 0 and n_skipped == 2 and ws.writes == []


# ---------------------------------------------------------------- analyse_tab on a whole (fake) worksheet
class ReadSheet:
    def __init__(self, title, values):
        self.title, self._values = title, values

    def get_all_values(self):
        return self._values


def test_analyse_tab_plans_every_row_labels_defended_ones_and_skips_frozen_ones(monkeypatch):
    monkeypatch.setattr(em.supabase_db, "fetch_existing_fields", lambda skus: {})
    sched = round(schedule_price(30), 2)
    header = ["SKU", "Title", "Cost Price (£)", "Shipping Cost (£)", "Selling Price (£)", "Profit %", "Fee %", "Category", "Stock", "Sync Status"]

    def line(sku, price, cost="30"):
        return [sku, "T " + sku, cost, "", str(price), "", "", "", "5", "Synced"]

    ws = ReadSheet("Sheet1", [header, line("A", sched * 1.4), line("B", sched), line("C", sched * 1.4), line("D", sched * 1.4), line("E", sched * 3), line("F", 50, cost=""),
                              [""] * len(header)])
    rows, (ix, values) = em.analyse_tab(ws, defended={"C"}, frozen={"D"})
    by = {r["sku"]: r for r in rows}
    assert sorted(by) == ["A", "B", "C", "D", "E", "F"]                                         # the blank line is no row
    assert [by[s]["action"] for s in "ABCDEF"] == ["lower", "ok", "lower", "skip", "hold", "skip"]
    assert by["C"]["defended"] and "Buy Box-defended" in by["C"]["note"]                        # evened like any other row, labelled
    assert not by["A"]["defended"] and by["A"]["n"] == 2 and by["F"]["n"] == 7                  # sheet row numbers (header = row 1)
    assert by["D"]["note"] == "frozen wrong-content listing" and by["F"]["note"] == "no cost"
    assert ix["Selling Price (£)"] == 4 and values[0] == header


# ---------------------------------------------------------------- the report helpers
def test_bucket_labels():
    assert em._bucket(1.01) == "under 5% off" and em._bucket(1.07) == "5-10%" and em._bucket(1.3) == "25-50%" and em._bucket(2.5) == "100%+"


def test_col_letter():
    assert [em.col_letter(n) for n in (1, 26, 27, 52, 53)] == ["A", "Z", "AA", "AZ", "BA"]


def test_the_tool_never_prints_a_cost(capsys):
    d = decide(**{"Cost Price (£)": "77.77", "Selling Price (£)": "9999"})
    r = row(5, "S5", "hold", d)
    r["note"] = em.plan_row(d)[1]
    em.report("Sheet1", [r], amazon=False)
    out = capsys.readouterr().out
    assert "77.77" not in out and "HOLD row 5" in out
