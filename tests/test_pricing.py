"""Pricing ranges: every edge pinned. The schedule is user policy
(2026-09-11 rewrite: plain profit percentages 100/80/40/20, the older
total-markup notation retired) - a failing test here means the policy
changed on purpose (update the cases) or a regression (fix it)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import pricing  # noqa: E402


PROFIT_CASES = [
    # (cost + shipping, expected PROFIT %)
    (0.01, 100), (4.99, 100),                    # under 5
    (5.00, 80), (7.50, 80), (10.00, 80),         # 5-10 inclusive
    (10.01, 40), (30.00, 40), (50.00, 40),       # over 10 to 50 inclusive
    (50.01, 20), (100.00, 20), (150.00, 20), (999.00, 20),  # above 50
]


def test_profit_range_edges():
    for total_cost, expected in PROFIT_CASES:
        got = pricing.profit_percent(total_cost)
        assert got == expected, f"cost {total_cost}: profit {got}% != {expected}%"


PRICE_CASES = [
    # (kwargs, expected price at the flat fallback fee: the 20% commission plus
    # the 20% VAT OnBuy adds to it (2026-10-07) = 24% -> /0.76
    (dict(cost_price=3.0), 7.89, "GBP 3 -> 100% profit / 0.76"),
    (dict(cost_price=8.0), 18.95, "GBP 8 -> 80% profit / 0.76"),
    (dict(cost_price=20.0), 36.84, "GBP 20 -> 40% profit / 0.76"),
    (dict(cost_price=28.0, shipping_cost=4.0), 58.95, "28+4 ship = 32 total"),
    (dict(cost_price=45.0, shipping_cost=5.0), 92.11, "45+5 = 50 total -> still the 40% range"),
    (dict(cost_price=60.0), 94.74, "GBP 60 -> 20% profit / 0.76"),
    (dict(cost_price=150.0), 236.84, "GBP 150 -> 20% profit / 0.76"),
    (dict(cost_price=95.0, shipping_cost=10.0), 165.79, "95+10 = 105 total"),
    (dict(cost_price=9.0, shipping_cost=0.5), 22.50, "9.50 total -> 80% profit range"),
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
    assert price == round(150 * 1.20 / 0.916, 2)         # 196.51
    assert abs(price * 0.916 - 150 * 1.20) < 0.02
    assert abs(pricing.effective_fee_percent(price, rule) - 8.4) < 0.01


def test_higher_category_fee_still_pays_the_range_profit():
    # A 25% commission (30% with the VAT on it) widens the divisor instead
    # of eating the margin: 20 x 1.40 / 0.70 = 40.00.
    price = pricing.calculate_selling_price(20.0, platform_fee_percent=25)
    assert price == 40.00
    assert abs(price * 0.70 - 28.0) < 0.01


def test_legacy_profit_percents_expose_only_changed_ranges():
    # The 2026-09-11 rewrite: GBP 50-100 dropped 40 -> 20, above 100
    # 30/25 -> 20, under 5 rose to 100. Old DOWNWARD values must stay
    # recognisable so existing prices reprice down.
    # (the top band also lists the temporary 15% of 2026-10-02: whichever of 20 / 15 is live, the other is superseded)
    assert pricing.legacy_profit_percents(150.0) == [30, 25, 15]
    assert pricing.legacy_profit_percents(60.0) == [40, 15]
    assert pricing.legacy_profit_percents(100.0) == [40, 15]
    assert pricing.legacy_profit_percents(3.0) == [80]
    # Ranges that kept their value offer no legacy - and never the current one.
    assert pricing.legacy_profit_percents(7.5) == []
    assert pricing.legacy_profit_percents(20.0) == []
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


def test_unset_env_keeps_the_standing_20(top_band):
    p = top_band(None)
    assert p.TOP_BAND_PROFIT == 20 and p.profit_percent(60.0) == 20
    # the old 40% schedule and the temporary 15% top band are both "superseded" - recognised, never the live value
    assert p.legacy_profit_percents(60.0) == [40, 15]
    assert p.legacy_profit_percents(30.0) == []              # below the top band nothing ever moved


def test_fifteen_moves_only_the_band_above_50(top_band):
    p = top_band("15")
    assert p.profit_percent(50.01) == 15 and p.profit_percent(999.0) == 15
    assert p.profit_percent(50.00) == 40                      # exactly 50 stays in the 40% range
    for cost, expected in ((4.99, 100), (5.0, 80), (10.0, 80), (10.01, 40), (30.0, 40)):
        assert p.profit_percent(cost) == expected


def test_fifteen_price_is_the_lower_formula_price(top_band):
    p = top_band("15")
    assert abs(p.calculate_selling_price(60.0) - 90.79) < 0.01    # 60 x 1.15 / 0.76 (was 94.74 at 20%)
    assert p.calculate_selling_price(30.0) == pytest.approx(55.26, abs=0.01)  # 40% range untouched: 30 x 1.4 / 0.76


def test_prices_set_at_20_stay_recognisable_so_they_follow_down_and_come_back_up(top_band):
    cost, ship = 80.0, 0.0
    p = top_band(None)
    price20 = p.calculate_selling_price(cost, ship)
    p = top_band("15")
    assert 20.0 in p.legacy_profit_percents(cost)
    assert _formula_priced()(price20, cost, ship, None) is True          # the sync lowers a price it set at 20%
    price15 = p.calculate_selling_price(cost, ship)
    assert price15 < price20
    p = top_band(None)                                                    # reverted: the 15% price is below the formula,
    assert p.calculate_selling_price(cost, ship) == price20               # so max(existing, formula) raises it back


def test_after_the_revert_a_price_set_at_15_is_still_recognised_and_rises_back(top_band):
    cost, ship = 80.0, 0.0
    p = top_band("15")
    price15 = p.calculate_selling_price(cost, ship)
    p = top_band(None)                                                    # reverted to the standing 20
    assert 15.0 in p.legacy_profit_percents(cost)                          # so a Profit % cell of 15.00 is the automation's own
    assert _formula_priced()(price15, cost, ship, None) is True
    assert p.calculate_selling_price(cost, ship) > price15                # and max(existing, formula) raises it


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
