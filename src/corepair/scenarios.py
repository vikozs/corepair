"""Cost scenarios over a horizon, and - more importantly - where the "savings"
in a multi-year commitment actually come from.

A three-year lock is routinely justified with a single headline number. That
number is usually the sum of three very different things:

  1. the *term* effect      - a better discount for committing longer
  2. an *inflation* effect  - avoiding assumed annual list-price growth
  3. a *decay* effect       - avoiding an assumed worse discount at renewal

Only (1) is a property of the contract. (2) and (3) are forecasts, and they are
frequently the majority of the headline. ``attribute_savings`` separates them so
a decision-maker can see how much of the case rests on assumptions rather than
on terms - and so the assumptions can be challenged, or written into the
contract as commitments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import SubscriptionModel, TermOption


@dataclass
class Layer:
    """A tranche of subscriptions bought on its own terms.

    Blended purchasing exists because commitment length and quantity certainty
    are different questions. A stable base you are sure of can take a long lock;
    speculative growth should not.
    """

    name: str
    units: int
    term: TermOption
    start_year: int = 1


@dataclass
class YearCost:
    year: int
    units: int
    unit_price: float
    cost: float
    note: str = ""


@dataclass
class ScenarioResult:
    name: str
    years: list[YearCost] = field(default_factory=list)
    model: Optional[SubscriptionModel] = None

    @property
    def total(self) -> float:
        return sum(y.cost for y in self.years)

    def npv(self, discount_rate_pct: float) -> float:
        r = discount_rate_pct / 100.0
        return sum(y.cost / ((1 + r) ** (y.year - 1)) for y in self.years)

    @property
    def peak_units(self) -> int:
        return max((y.units for y in self.years), default=0)


def unit_price(model: SubscriptionModel, term: TermOption, year: int, locked_year: int) -> float:
    """Price per unit in a given calendar year.

    ``locked_year`` is the year the current commitment started. Inside a
    commitment the price is held; when it lapses, list price has grown by the
    assumed rate and the renewal discount applies.
    """
    years_since_lock = year - locked_year
    grown_list = model.list_price * ((1 + term.annual_list_growth_pct / 100.0) ** (locked_year - 1))
    discount = term.discount_pct
    if locked_year > 1 and term.renewal_discount_pct is not None:
        discount = term.renewal_discount_pct
    assert years_since_lock >= 0
    return grown_list * (1 - discount / 100.0)


def run_layer(model: SubscriptionModel, layer: Layer, horizon: int) -> list[YearCost]:
    out: list[YearCost] = []
    locked_year = layer.start_year
    for year in range(layer.start_year, horizon + 1):
        if year > locked_year + layer.term.years - 1:
            locked_year = year  # commitment lapsed, re-price
        price = unit_price(model, layer.term, year, locked_year)
        note = "renewal" if locked_year > layer.start_year and year == locked_year else ""
        out.append(YearCost(year=year, units=layer.units, unit_price=price,
                            cost=price * layer.units, note=note))
    return out


def run_scenario(name: str, model: SubscriptionModel, layers: list[Layer],
                 horizon: int) -> ScenarioResult:
    per_year: dict[int, YearCost] = {}
    for layer in layers:
        for yc in run_layer(model, layer, horizon):
            if yc.year not in per_year:
                per_year[yc.year] = YearCost(year=yc.year, units=0, unit_price=yc.unit_price,
                                             cost=0.0)
            agg = per_year[yc.year]
            agg.units += yc.units
            agg.cost += yc.cost
            if yc.note:
                agg.note = yc.note
    for yc in per_year.values():
        yc.unit_price = yc.cost / yc.units if yc.units else 0.0
    return ScenarioResult(name=name, model=model,
                          years=[per_year[y] for y in sorted(per_year)])


# ------------------------------------------------------------- attribution


@dataclass
class Attribution:
    total_saving: float
    term_effect: float
    inflation_effect: float
    discount_decay_effect: float

    @property
    def assumption_driven_pct(self) -> float:
        """Share of the headline saving that rests on forecasts, not on terms."""
        if self.total_saving == 0:
            return 0.0
        return 100.0 * (self.inflation_effect + self.discount_decay_effect) / self.total_saving


def attribute_savings(model: SubscriptionModel, units: int, short: TermOption,
                      long: TermOption, horizon: int) -> Attribution:
    """Decompose the saving of ``long`` over ``short`` into its three sources.

    Each effect is measured by switching one assumption on at a time, so the
    three components always sum exactly to the headline number.
    """

    def cost(term: TermOption) -> float:
        return run_scenario("x", model, [Layer("all", units, term)], horizon).total

    def variant(t: TermOption, growth: float, renewal: Optional[float]) -> TermOption:
        return TermOption(years=t.years, discount_pct=t.discount_pct,
                          annual_list_growth_pct=growth, renewal_discount_pct=renewal,
                          prepaid=t.prepaid, label=t.label)

    # Step 0: no inflation, no discount decay. Pure term/discount differential.
    s0 = cost(variant(short, 0.0, None))
    l0 = cost(variant(long, 0.0, None))
    term_effect = s0 - l0

    # Step 1: add the assumed list-price growth to both sides.
    s1 = cost(variant(short, short.annual_list_growth_pct, None))
    l1 = cost(variant(long, long.annual_list_growth_pct, None))
    inflation_effect = (s1 - l1) - term_effect

    # Step 2: add the assumed renewal-discount decay.
    s2 = cost(short)
    l2 = cost(long)
    total = s2 - l2
    decay_effect = total - term_effect - inflation_effect

    return Attribution(total_saving=total, term_effect=term_effect,
                       inflation_effect=inflation_effect,
                       discount_decay_effect=decay_effect)


def sensitivity(model: SubscriptionModel, term: TermOption, horizon: int,
                unit_range: list[int]) -> list[tuple[int, float]]:
    """Total cost across a range of quantities.

    Quantity is almost always a bigger lever than term. This table makes the
    two comparable on one page.
    """
    return [
        (u, run_scenario("s", model, [Layer("all", u, term)], horizon).total)
        for u in unit_range
    ]
