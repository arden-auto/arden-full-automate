"""Pricing ranges: every edge pinned. The schedule is user policy
(2026-09-11 rewrite: plain profit percentages 100/80/40/20, the older
total-markup notation retired; top band 13% above GBP 30 since 2026-10-19, 25% above 50 before) - a failing test here means the policy
changed on purpose (update the cases) or a regression (fix it)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import pricing  # noqa: E402


PROFIT_CASES = [
    # (cost + shipping, expected PROFIT %)
    # a flat 20 on every band since 2026-10-22 (100 / 80 / 40 / 13 before, the 13 above 30 since 2026-10-19)
    (0.01, 20), (4.99, 20),                      # under 5
    (5.00, 20), (7.50, 20), (10.00, 20),         # 5-10 inclusive
    (10.01, 20), (20.00, 20), (30.00, 20),       # over 10 to 30 inclusive
    (30.01, 20), (50.00, 20), (50.01, 20), (100.00, 20), (150.00, 20), (999.00, 20),  # above 30
]


def test_profit_range_edges():
    for total_cost, expected in PROFIT_CASES:
        got = pricing.profit_percent(total_cost)
        assert got == expected, f"cost {total_cost}: profit {got}% != {expected}%"


PRICE_CASES = [
    # (kwargs, expected price at the flat fallback fee: the 20% commission plus
    # the 20% VAT OnBuy adds to it (2026-10-07) = 24% -> /0.76
    (dict(cost_price=3.0), 4.74, "GBP 3 -> 20% profit / 0.76 (7.89 at the 100% of 2026-09-11 .. 10-22)"),
    (dict(cost_price=8.0), 12.63, "GBP 8 -> 20% profit / 0.76 (18.95 at 80%)"),
    (dict(cost_price=20.0), 31.58, "GBP 20 -> 20% profit / 0.76 (36.84 at 40%)"),
    (dict(cost_price=26.0, shipping_cost=4.0), 47.37, "26+4 ship = 30 total -> 20%"),
    (dict(cost_price=28.0, shipping_cost=4.0), 50.53, "28+4 ship = 32 total -> 20% profit / 0.76 (47.58 at 13%)"),
    (dict(cost_price=45.0, shipping_cost=5.0), 78.95, "45+5 = 50 total -> 20% profit"),
    (dict(cost_price=60.0), 94.74, "GBP 60 -> 20% profit / 0.76"),
    (dict(cost_price=150.0), 236.84, "GBP 150 -> 20% profit / 0.76"),
    (dict(cost_price=95.0, shipping_cost=10.0), 165.79, "95+10 = 105 total -> 20% profit"),
    (dict(cost_price=9.0, shipping_cost=0.5), 15.00, "9.50 total -> 20% (22.50 at 80%)"),
]


def test_selling_prices():
    for kwargs, expected, label in PRICE_CASES:
        got = pricing.calculate_selling_price(**kwargs)
        assert abs(got - expected) < 0.001, f"{label}: got {got}, expected {expected}"


def test_fee_comes_out_of_the_selling_price():
    """After OnBuy takes its cut of the SELLING price, what is retained
    must be exactly cost x (1 + profit%) - the fee never eats the profit
    and never stacks on top of it."""
    for base in (3.0, 8.0, 20.0, 60.0, 150.0, 400.0):
        sell = pricing.calculate_selling_price(base)
        retained = sell * (1 - pricing.effective_rate(pricing.PLATFORM_FEE_PERCENT) / 100)
        expected = base * (1 + pricing.profit_percent(base) / 100)
        assert abs(retained - expected) < 0.02, (
            f"cost {base}: retained {retained:.2f} != {expected:.2f}")


def test_category_fee_keeps_the_same_profit():
    # A nominal 7% category really deducts 8.4% (commission + 20% VAT on it,
    # read off OnBuy's own order records); the divisor uses the REAL rate so
    # the retained amount still equals cost x (1 + profit) after the true deduction.
    rule = pricing.FeeRule("Consumer Electronics", 7)
    price = pricing.calculate_selling_price(150.0, fee_rule=rule)
    assert price == round(150 * 1.20 / 0.916, 2)         # 196.51 (flat 20 since 2026-10-22; 185.04 at 13%, 204.69 at 25%)
    assert abs(price * 0.916 - 150 * 1.20) < 0.02
    assert abs(pricing.effective_fee_percent(price, rule) - 8.4) < 0.01


def test_higher_category_fee_still_pays_the_range_profit():
    # A 25% commission (30% with the VAT on it) widens the divisor instead
    # of eating the margin: 20 x 1.20 / 0.70 = 34.29 (flat 20 since 2026-10-22; 40.00 at the 40% band before).
    price = pricing.calculate_selling_price(20.0, platform_fee_percent=25)
    assert price == 34.29
    assert abs(price * 0.70 - 24.0) < 0.01


def test_legacy_profit_percents_expose_only_changed_ranges():
    # The 2026-09-11 rewrite: GBP 50-100 dropped 40 -> 20, above 100
    # 30/25 -> 20, under 5 rose to 100. Old DOWNWARD values must stay
    # recognisable so existing prices reprice down.
    # (the top band also lists its recent values 20 (standing until 2026-10-09) and 15 (temporary): whichever value is live,
    # the others are superseded - and the live 25 is never listed)
    # (flat 20 since 2026-10-22: the live 20 is never listed, every outgoing value is)
    assert pricing.legacy_profit_percents(150.0) == [30, 25, 15, 13]
    assert pricing.legacy_profit_percents(60.0) == [40, 25, 15, 13]
    assert pricing.legacy_profit_percents(100.0) == [40, 25, 15, 13]
    assert pricing.legacy_profit_percents(35.0) == [40, 13]              # GBP 30-50: 40% until 2026-10-19, 13% until 2026-10-22
    assert pricing.legacy_profit_percents(3.0) == [80, 100]
    assert pricing.legacy_profit_percents(7.5) == [80]
    assert pricing.legacy_profit_percents(20.0) == [40]
    assert pricing.legacy_profit_percents(0) == []


def test_absurd_fee_is_clamped_not_divided_by_zero():
    assert pricing.calculate_selling_price(20.0, platform_fee_percent=100) > 0
    assert pricing.calculate_selling_price(20.0, platform_fee_percent=250) > 0


def test_zero_and_negative_cost():
    assert pricing.calculate_selling_price(0) == 0.0
    assert pricing.calculate_selling_price(-5) == 0.0


# ---- the temporary top-band override (user 2026-10-02: 15% for some days, reverted on command) ----
import ast  # noqa: E402
import importlib  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def top_band(monkeypatch):
    """Reload pricing with PROFIT_ABOVE_50_PERCENT set (None = unset) and restore the standing schedule after."""
    def _set(value):
        if value is None:
            monkeypatch.delenv("PROFIT_ABOVE_50_PERCENT", raising=False)
        else:
            monkeypatch.setenv("PROFIT_ABOVE_50_PERCENT", value)
        return importlib.reload(pricing)
    yield _set
    monkeypatch.delenv("PROFIT_ABOVE_50_PERCENT", raising=False)
    importlib.reload(pricing)


def _formula_priced():
    src = Path(__file__).resolve().parents[1] / "generate_xml.py"
    fn = next(n for n in ast.parse(src.read_text(encoding="utf-8")).body
              if isinstance(n, ast.FunctionDef) and n.name == "_formula_priced")
    ns = {"pricing": pricing}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(src), "exec"), ns)
    return ns["_formula_priced"]


def test_unset_env_keeps_the_standing_20_everywhere(top_band):
    p = top_band(None)
    assert p.TOP_BAND_PROFIT == 20 and p.profit_percent(30.01) == 20 and p.profit_percent(60.0) == 20 and p.profit_percent(3.0) == 20
    # the old 40% range to 50, the previous 25, the temporary 15 and the 13 of 2026-10-19 are all "superseded" - recognised, never the live value
    assert p.legacy_profit_percents(60.0) == [40, 25, 15, 13]
    assert p.legacy_profit_percents(35.0) == [40, 13]
    assert p.legacy_profit_percents(30.0) == [40]            # exactly 30 was in the 40% range until 2026-10-22


def test_fifteen_moves_only_the_band_above_30(top_band):
    p = top_band("15")
    assert p.profit_percent(30.01) == 15 and p.profit_percent(50.0) == 15 and p.profit_percent(999.0) == 15
    assert p.profit_percent(30.00) == 20                      # exactly 30 stays in the flat band below the knob
    for cost, expected in ((4.99, 20), (5.0, 20), (10.0, 20), (10.01, 20), (30.0, 20)):
        assert p.profit_percent(cost) == expected


def test_fifteen_price_is_the_formula_price_at_15(top_band):
    p = top_band("15")
    assert abs(p.calculate_selling_price(60.0) - 90.79) < 0.01    # 60 x 1.15 / 0.76 (94.74 at the standing 20%)
    assert p.calculate_selling_price(40.0) == pytest.approx(60.53, abs=0.01)  # 40 x 1.15 / 0.76 (63.16 at 20%; 73.68 at the old 40%)
    assert p.calculate_selling_price(30.0) == pytest.approx(47.37, abs=0.01)  # the flat band untouched: 30 x 1.2 / 0.76


def test_prices_set_at_25_15_and_13_stay_recognisable_so_they_follow_the_formula_to_the_20(top_band):
    cost, ship = 80.0, 0.0
    price25 = top_band("25").calculate_selling_price(cost, ship)          # the band until 2026-10-19
    price15 = top_band("15").calculate_selling_price(cost, ship)          # the temporary one
    price13 = top_band("13").calculate_selling_price(cost, ship)          # 2026-10-19 .. 2026-10-22
    p = top_band(None)                                                    # the standing flat 20 (2026-10-22)
    assert all(v in p.legacy_profit_percents(cost) for v in (25.0, 15.0, 13.0))
    for price in (price25, price15, price13):
        assert _formula_priced()(price, cost, ship, None) is True          # all three are the automation's own...
    price20 = p.calculate_selling_price(cost, ship)
    assert price13 < price15 < price20 < price25                          # ...and they follow the formula down (25) or up (15, 13) to the 20% price
    # a GBP 40 row priced at the old 40% (the 10-50 range until 2026-10-19) is the automation's own too and follows down to 20%
    old40 = pricing.price_for_profit(40.0, 40)
    assert _formula_priced()(old40, 40.0, 0.0, None) is True and p.calculate_selling_price(40.0, 0.0) < old40
    # ...as is a GBP 3 row priced at the old 100% (2026-09-11 .. 10-22) - it follows down to the flat 20
    old100 = pricing.price_for_profit(3.0, 100)
    assert _formula_priced()(old100, 3.0, 0.0, None) is True and p.calculate_selling_price(3.0, 0.0) < old100


def test_a_price_set_at_20_stays_recognisable_when_the_band_is_moved_again(top_band):
    cost, ship = 80.0, 0.0
    price20 = top_band(None).calculate_selling_price(cost, ship)
    p = top_band("25")                                                    # a temporary move of the top band on command
    assert 20.0 in p.legacy_profit_percents(cost)                         # so a Profit % cell of 20.00 is the automation's own
    assert _formula_priced()(price20, cost, ship, None) is True            # and the price follows the formula (up, this time)
    assert p.calculate_selling_price(cost, ship) > price20
    p = top_band("15")
    assert 20.0 in p.legacy_profit_percents(cost) and 13.0 in p.legacy_profit_percents(cost) and 25.0 in p.legacy_profit_percents(cost)
    p = top_band(None)                                                    # reverted: the 20% price is the formula again
    assert _formula_priced()(price20, cost, ship, None) is True
    assert p.calculate_selling_price(cost, ship) == price20


@pytest.mark.parametrize("bad", ["garbage", "150", "-3", " ", "nan"])
def test_a_bad_value_falls_back_to_the_standing_20_instead_of_breaking_the_run(top_band, bad):
    assert top_band(bad).TOP_BAND_PROFIT == 20


# ---------------------------------------------------------------- the fee model (2026-10-07): commission + the VAT OnBuy adds
def test_effective_rate_is_the_commission_plus_vat():
    # read off OnBuy's order records: sales_fee_inc_VAT == sales_fee_ex_VAT x 1.2, ex VAT == the listed rate x the price
    assert pricing.effective_rate(15) == pytest.approx(18.0)
    assert pricing.effective_rate(7) == pytest.approx(8.4)
    assert pricing.effective_rate(20) == pytest.approx(24.0)
    assert pricing.effective_rate(13) == pytest.approx(15.6)
    assert pricing.effective_rate(15, legacy=True) == pytest.approx(16.5)        # the 2026-09-17 model: nominal + 1.5 points


def test_the_real_order_is_reproduced():
    # GTV order T6Q7N88 (2026-10-05): a 71.84 sale in the 15% "Standard Selling Fee" category - OnBuy took
    # sales_fee_ex_VAT 10.78 (15.01%) and sales_fee_inc_VAT 12.94 (18.01%)
    rule = pricing.FeeRule("Standard Selling Fee", 15, min_fee=0.25)
    assert pricing.fee_amount(71.84, rule) == pytest.approx(12.94, abs=0.011)
    assert pricing.fee_amount(71.84, rule, legacy=True) == pytest.approx(11.85, abs=0.011)     # what the old model assumed
    # ...and the price that keeps the 20% profit on a 54.99 cost after that deduction
    assert pricing.price_for_profit(54.99, 20, rule) == 80.47
    assert pricing.price_for_profit(54.99, 20, rule, legacy=True) == 79.03
    assert abs(pricing.price_for_profit(54.99, 20, rule) * 0.82 - 54.99 * 1.2) < 0.01


def _old_fee_amount(price, rule, mode="marginal"):
    """The fee model as it stood 2026-09-17 .. 2026-10-07 (nominal + 1.5 points), written out independently."""
    up = 1.5
    if rule is None:
        return price * (20 + up) / 100
    r1 = (rule.lower_pct + up) / 100
    if not rule.tiered or price <= rule.threshold:
        fee = price * r1
    elif mode == "step":
        fee = price * (rule.upper_pct + up) / 100
    else:
        fee = rule.threshold * r1 + (price - rule.threshold) * (rule.upper_pct + up) / 100
    return max(fee, rule.min_fee)


def test_the_legacy_flag_reproduces_the_old_model_exactly():
    # recognition of prices the automation set before 2026-10-07 depends on this
    import random
    rng = random.Random(20261007)
    rules = [None, pricing.FeeRule("a", 7), pricing.FeeRule("b", 15, min_fee=0.25), pricing.FeeRule("c", 20),
             pricing.FeeRule("d", 15, 8, 100.0, 0.25), pricing.FeeRule("e", 20, 5, 225.0, 0.25)]
    for _ in range(2000):
        rule = rng.choice(rules)
        price = round(rng.uniform(0.5, 600), 2)
        for mode in ("marginal", "step"):
            assert pricing.fee_amount(price, rule, mode, legacy=True) == pytest.approx(_old_fee_amount(price, rule, mode))
        retained = round(rng.uniform(0.3, 400), 2)
        got = pricing.price_for_retained(retained, rule, legacy=True)
        if got > 0 and not (rule is not None and rule.tiered and abs(got - rule.threshold) < 0.02):
            # the price that leaves `retained` under the old model: the old fee on it plus `retained` is the price
            assert got - _old_fee_amount(got, rule) == pytest.approx(retained, abs=0.02)


def test_the_vat_model_leaves_the_retained_amount_after_the_real_deduction():
    rng = __import__("random").Random(7)
    for _ in range(1000):
        rule = rng.choice([None, pricing.FeeRule("a", 7), pricing.FeeRule("b", 15, min_fee=0.25), pricing.FeeRule("d", 15, 8, 100.0, 0.25)])
        retained = round(rng.uniform(1.0, 500), 2)
        price = pricing.price_for_retained(retained, rule)
        assert price - pricing.fee_amount(price, rule) == pytest.approx(retained, abs=0.01)


def test_effective_profit_percent_matches_the_hand_calculation():
    rule = pricing.FeeRule("Standard Selling Fee", 15, min_fee=0.25)
    # 89.99 on a 54.99 cost in a 15% category: 18% of the price goes to OnBuy (commission + VAT)
    assert pricing.effective_profit_percent(89.99, 54.99, rule) == pytest.approx(((89.99 * 0.82) / 54.99 - 1) * 100)
    assert pricing.effective_profit_percent(89.99, 54.99, rule) == pytest.approx(34.19, abs=0.005)
    # a typed fee (the flat path) and the flat fallback
    assert pricing.effective_profit_percent(50.0, 20.0, None, platform_fee_percent=10) == pytest.approx((50 * (1 - 0.12) / 20 - 1) * 100)
    assert pricing.effective_profit_percent(50.0, 20.0) == pytest.approx((50 * (1 - 0.24) / 20 - 1) * 100)
    # the formula's own price earns exactly the profit it was priced for
    for profit in (15, 20, 40, 100):
        price = pricing.price_for_profit(100.0, profit, rule)
        assert pricing.effective_profit_percent(price, 100.0, rule) == pytest.approx(profit, abs=0.01)
