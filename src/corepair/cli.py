"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import collect, licensing, report, scenarios
from .config import load_plan, load_pricing
from .models import NodeShape
from .scenarios import Layer

__version__ = "0.1.0"


def _p(msg: str = "") -> None:
    print(msg)


# ------------------------------------------------------------------ measure


def cmd_measure(a) -> int:
    if a.snapshot_in:
        nodes, demands, meta = collect.load_snapshot(a.snapshot_in)
        _p(f"# loaded snapshot from {a.snapshot_in} ({meta.get('taken_at', 'unknown time')})")
    else:
        nodes = collect.collect_nodes()
        demands = collect.collect_requests()
        if a.prometheus:
            prom = collect.Prometheus(a.prometheus, insecure=a.insecure)
            demands = collect.merge_usage(demands, collect.collect_usage(prom, a.window))
        else:
            _p("# no --prometheus given: requests only, no usage. Overcommit cannot be "
               "measured without it.", )
        if a.snapshot_out:
            collect.save_snapshot(a.snapshot_out, nodes, demands,
                                  {"taken_at": datetime.now(timezone.utc).isoformat(),
                                   "window": a.window, "tool": f"corepair {__version__}"})
            _p(f"# snapshot written to {a.snapshot_out}")

    count = licensing.count_cluster(nodes, a.vcpu_per_unit)
    tot = licensing.totals(demands)

    if a.json:
        print(json.dumps({
            "core_pairs": count.core_pairs,
            "subscribed_nodes": len(count.subscribed_nodes),
            "subscribed_vcpu": count.subscribed_vcpu,
            "rounding_waste_vcpu": count.rounding_waste_vcpu,
            "cpu_request_vcpu": round(tot.cpu_request_vcpu, 2),
            "cpu_usage_vcpu": round(tot.cpu_usage_vcpu, 2),
            "mem_request_gib": round(tot.mem_request_gib, 2),
            "mem_usage_gib": round(tot.mem_usage_gib, 2),
        }, indent=2))
        return 0

    _p(report.markdown_summary(count, tot, None))
    return 0


# -------------------------------------------------------------------- audit


def cmd_audit(a) -> int:
    nodes = (collect.load_snapshot(a.snapshot_in)[0] if a.snapshot_in
             else collect.collect_nodes())
    count = licensing.count_cluster(nodes, a.vcpu_per_unit)

    _p(f"Subscribed nodes : {len(count.subscribed_nodes)} / {len(count.assessments)}")
    _p(f"Core-pairs       : {count.core_pairs}")
    _p(f"Rounding waste   : {count.rounding_waste_vcpu} vCPU paid for but not present")
    _p()

    findings = 0
    bad = count.unlabelled_infra_candidates()
    if bad:
        findings += len(bad)
        _p("FINDING  infra nodes without an infra taint (application pods can still land "
           "here, so the exemption does not apply):")
        for x in bad:
            _p(f"  - {x.node.name}: {x.core_pairs} core-pairs would be released by tainting it")
        _p()

    odd = [x for x in count.subscribed_nodes if x.node.vcpu % a.vcpu_per_unit]
    if odd:
        findings += len(odd)
        _p(f"FINDING  node sizes that are not a multiple of {a.vcpu_per_unit} vCPU. Rounding "
           "is per node, so the remainder is paid for and unusable:")
        for x in odd:
            waste = x.core_pairs * a.vcpu_per_unit - x.node.vcpu
            _p(f"  - {x.node.name}: {x.node.vcpu} vCPU -> {x.core_pairs} core-pairs "
               f"({waste} vCPU wasted)")
        _p()

    shapes = {(x.node.vcpu, x.node.memory_gib) for x in count.subscribed_nodes}
    if len(shapes) > 1:
        _p("NOTE     workers are not uniform: " +
           ", ".join(f"{v}vCPU/{m:.0f}GiB" for v, m in sorted(shapes)) +
           ". Mixed shapes make the licence count harder to predict as the estate grows.")
        _p()

    if not findings:
        _p("No subscription-count findings.")
    return 1 if findings and a.fail_on_findings else 0


# --------------------------------------------------------------------- plan


def cmd_plan(a) -> int:
    plan = load_plan(a.plan)
    shape = NodeShape(a.node_vcpu, a.node_memory_gib) if a.node_vcpu else plan.shape

    _p(f"# Node shape: {shape.vcpu} vCPU / {shape.memory_gib:.0f} GiB\n")
    _p("| Through wave | Apps | CPU vCPU | Mem GiB | Nodes (cpu) | Nodes (mem) | "
       "Binding | Nodes | Core-pairs |")
    _p("|---|---|---|---|---|---|---|---|---|")

    cumulative_units = 0
    for wave in plan.waves:
        demand = plan.cumulative_through(wave)
        use_measured = a.basis == "usage"
        cpu = sum((w.total_cpu_usage_m if use_measured else w.total_cpu_request_m)
                  for w in demand) / 1000.0
        mem = sum((w.total_mem_usage_mib if use_measured else w.total_mem_request_mib)
                  for w in demand) / 1024.0
        s = licensing.size_cluster(cpu, mem, shape, headroom_pct=a.headroom,
                                   ha_spare_nodes=a.ha_spare)
        cp = s.nodes * licensing.core_pairs_for_node(shape.vcpu, a.vcpu_per_unit)
        cumulative_units = cp
        _p(f"| {wave} | {len(demand)} | {cpu:.1f} | {mem:.0f} | {s.nodes_for_cpu} | "
           f"{s.nodes_for_memory} | {s.binding_resource} | {s.nodes} | {cp} |")

    _p()
    _p(f"Core-pairs at full build-out: **{cumulative_units}**")
    _p()
    _p("Each row is cumulative: it is what you need in place *by* that wave, which is "
       "what you should be buying then - not at the start.")
    return 0


# --------------------------------------------------------------------- cost


def cmd_cost(a) -> int:
    pricing = load_pricing(a.pricing)
    model = pricing.model(a.model)
    short = pricing.term(a.short_term)
    long = pricing.term(a.long_term)

    results = [
        scenarios.run_scenario(f"All {a.units} units, {short.name()}", model,
                               [Layer("all", a.units, short)], a.horizon),
        scenarios.run_scenario(f"All {a.units} units, {long.name()}", model,
                               [Layer("all", a.units, long)], a.horizon),
    ]

    if a.base_units:
        growth = max(a.units - a.base_units, 0)
        layers = [Layer("stable base", a.base_units, long)]
        if growth:
            layers.append(Layer("growth", growth, short, start_year=a.growth_start_year))
        results.append(scenarios.run_scenario(
            f"Blended: {a.base_units} on {long.name()}, {growth} on {short.name()} "
            f"from year {a.growth_start_year}", model, layers, a.horizon))

    attribution = scenarios.attribute_savings(model, a.units, short, long, a.horizon)

    _p(report.markdown_scenarios(results, attribution, pricing.currency,
                                 pricing.discount_rate_pct))

    if a.sensitivity:
        lo, hi = a.sensitivity
        _p("### Sensitivity to quantity")
        _p()
        _p(f"| Units | Total {pricing.currency} over {a.horizon}y |")
        _p("|---|---|")
        for u, c in scenarios.sensitivity(model, long, a.horizon, list(range(lo, hi + 1))):
            _p(f"| {u} | {c:,.0f} |")
        _p()
        _p("Compare the spread of this column against the saving from the longer term "
           "above. Quantity is usually the larger lever, and it is the one that "
           "right-sizing actually moves.")

    if a.xlsx:
        report.write_xlsx(a.xlsx, currency=pricing.currency, model=model, short=short,
                          long=long, units=a.units, horizon=a.horizon,
                          discount_rate_pct=pricing.discount_rate_pct)
        _p()
        _p(f"Workbook written to {a.xlsx} (live formulas - change the shaded inputs).")
    return 0


# --------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="corepair",
        description="Turn measured cluster demand into a defensible OpenShift "
                    "subscription count and cost case.",
        epilog="corepair ships no vendor prices. Supply your own quote in a pricing file.",
    )
    p.add_argument("--version", action="version", version=f"corepair {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vcpu-per-unit", type=int, default=4,
                        help="vCPU covered by one subscription unit (default 4)")
    common.add_argument("--snapshot-in", help="read a saved snapshot instead of a cluster")

    m = sub.add_parser("measure", parents=[common], help="measure real demand")
    m.add_argument("--prometheus", help="Prometheus/Thanos base URL")
    m.add_argument("--window", default="30d", help="measurement window (default 30d)")
    m.add_argument("--insecure", action="store_true", help="skip TLS verification")
    m.add_argument("--snapshot-out", help="write the measurement to a JSON snapshot")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_measure)

    au = sub.add_parser("audit", parents=[common],
                        help="find nodes that are subscribed but need not be")
    au.add_argument("--fail-on-findings", action="store_true", help="exit 1 if findings")
    au.set_defaults(func=cmd_audit)

    pl = sub.add_parser("plan", parents=[common], help="project demand across migration waves")
    pl.add_argument("--plan", required=True, help="plan YAML")
    pl.add_argument("--basis", choices=["request", "usage"], default="request")
    pl.add_argument("--headroom", type=float, default=25.0, help="growth headroom %% (25)")
    pl.add_argument("--ha-spare", type=int, default=1, help="spare nodes for HA (1)")
    pl.add_argument("--node-vcpu", type=int, help="override node shape vCPU")
    pl.add_argument("--node-memory-gib", type=float, default=32.0)
    pl.set_defaults(func=cmd_plan)

    co = sub.add_parser("cost", help="cost scenarios and savings attribution")
    co.add_argument("--pricing", required=True, help="pricing YAML (your own figures)")
    co.add_argument("--model", default="core_pair")
    co.add_argument("--units", type=int, required=True)
    co.add_argument("--horizon", type=int, default=3)
    co.add_argument("--short-term", default="1y")
    co.add_argument("--long-term", default="3y")
    co.add_argument("--base-units", type=int,
                    help="units to lock long-term in a blended scenario")
    co.add_argument("--growth-start-year", type=int, default=2)
    co.add_argument("--sensitivity", nargs=2, type=int, metavar=("LO", "HI"))
    co.add_argument("--xlsx", help="write a formula-driven workbook here")
    co.set_defaults(func=cmd_cost)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError, KeyError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
