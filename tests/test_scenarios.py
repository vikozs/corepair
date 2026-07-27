import pytest

from corepair.models import SubscriptionModel, TermOption
from corepair.scenarios import Layer, attribute_savings, run_scenario, sensitivity

MODEL = SubscriptionModel("core_pair", "core_pair", list_price=1000.0)


def term(years, discount, growth=0.0, renewal=None):
    return TermOption(years=years, discount_pct=discount,
                      annual_list_growth_pct=growth, renewal_discount_pct=renewal,
                      label=f"{years}y")


def test_flat_pricing_when_no_assumptions():
    t = term(1, 0.0)
    r = run_scenario("s", MODEL, [Layer("all", 10, t)], horizon=3)
    assert r.total == pytest.approx(30_000.0)
    assert [y.year for y in r.years] == [1, 2, 3]


def test_long_term_holds_price_through_the_commitment():
    short = term(1, 20.0, growth=10.0, renewal=20.0)
    long = term(3, 20.0, growth=10.0)
    s = run_scenario("s", MODEL, [Layer("a", 1, short)], 3).total
    l = run_scenario("l", MODEL, [Layer("a", 1, long)], 3).total
    assert l == pytest.approx(3 * 800.0)   # price held for all three years
    assert s > l                            # short term re-prices upward each year


def test_attribution_components_sum_to_the_headline():
    short = term(1, 19.0, growth=5.0, renewal=12.0)
    long = term(3, 25.6, growth=5.0)
    a = attribute_savings(MODEL, 32, short, long, horizon=3)
    assert (a.term_effect + a.inflation_effect + a.discount_decay_effect
            == pytest.approx(a.total_saving))


def test_attribution_isolates_assumption_driven_savings():
    """With no growth and no discount decay, the whole saving is contractual."""
    short = term(1, 19.0, growth=0.0, renewal=None)
    long = term(3, 25.6, growth=0.0)
    a = attribute_savings(MODEL, 32, short, long, horizon=3)
    assert a.inflation_effect == pytest.approx(0.0)
    assert a.discount_decay_effect == pytest.approx(0.0)
    assert a.assumption_driven_pct == pytest.approx(0.0)

    # Turn the two forecasts on and most of the headline moves into them.
    short2 = term(1, 19.0, growth=5.0, renewal=12.0)
    b = attribute_savings(MODEL, 32, short2, long, horizon=3)
    assert b.total_saving > a.total_saving
    assert b.assumption_driven_pct > 50.0


def test_blended_beats_locking_speculative_growth_when_it_does_not_arrive():
    """The blended case: buy the base long, add growth later.

    If the growth tranche is only needed from year 2, buying it up front for
    three years pays for a year of nothing.
    """
    long = term(3, 25.0)
    short = term(1, 19.0)
    all_upfront = run_scenario("all", MODEL, [Layer("all", 32, long)], 3).total
    blended = run_scenario("blend", MODEL, [
        Layer("base", 8, long),
        Layer("growth", 24, short, start_year=2),
    ], 3).total
    assert blended < all_upfront


def test_npv_discounts_later_years():
    t = term(1, 0.0)
    r = run_scenario("s", MODEL, [Layer("a", 1, t)], 3)
    assert r.npv(0.0) == pytest.approx(r.total)
    assert r.npv(10.0) < r.total


def test_quantity_is_usually_the_bigger_lever():
    """Halving quantity beats any plausible term discount. The core argument."""
    long = term(3, 25.0)
    short = term(1, 19.0)
    term_saving = (run_scenario("s", MODEL, [Layer("a", 32, short)], 3).total
                   - run_scenario("l", MODEL, [Layer("a", 32, long)], 3).total)
    quantity_saving = (run_scenario("l32", MODEL, [Layer("a", 32, long)], 3).total
                       - run_scenario("l16", MODEL, [Layer("a", 16, long)], 3).total)
    assert quantity_saving > term_saving


def test_sensitivity_is_monotonic():
    rows = sensitivity(MODEL, term(3, 20.0), 3, [8, 12, 16, 24, 32])
    costs = [c for _, c in rows]
    assert costs == sorted(costs)
    assert len(rows) == 5
