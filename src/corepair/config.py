"""Loading of the two user-supplied inputs: a pricing file and a plan file.

corepair ships **no prices**. Vendor pricing is confidential and specific to
your contract; a number copied from someone else's repository is worse than no
number. ``examples/pricing.example.yaml`` documents the schema with obvious
placeholders that you replace from your own quote.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from .models import NodeShape, SubscriptionModel, TermOption, WorkloadDemand


@dataclass
class Pricing:
    currency: str
    models: dict[str, SubscriptionModel]
    terms: dict[str, TermOption]
    discount_rate_pct: float = 5.0   # for NPV

    def model(self, name: str) -> SubscriptionModel:
        if name not in self.models:
            raise KeyError(f"unknown subscription model {name!r}; have {list(self.models)}")
        return self.models[name]

    def term(self, name: str) -> TermOption:
        if name not in self.terms:
            raise KeyError(f"unknown term {name!r}; have {list(self.terms)}")
        return self.terms[name]


def load_pricing(path: str) -> Pricing:
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    if _looks_like_placeholder(doc):
        raise ValueError(
            f"{path} still contains placeholder prices. Replace them with the "
            "figures from your own quote before relying on any output."
        )
    models = {
        k: SubscriptionModel(name=k, **v) for k, v in (doc.get("models") or {}).items()
    }
    terms = {k: TermOption(label=k, **v) for k, v in (doc.get("terms") or {}).items()}
    return Pricing(
        currency=doc.get("currency", "EUR"),
        models=models,
        terms=terms,
        discount_rate_pct=float(doc.get("discount_rate_pct", 5.0)),
    )


def _looks_like_placeholder(doc: dict) -> bool:
    for m in (doc.get("models") or {}).values():
        if str(m.get("list_price", "")).upper().startswith("REPLACE"):
            return True
    return False


@dataclass
class Plan:
    """Planned future demand, by wave."""

    shape: NodeShape
    waves: dict[str, list[WorkloadDemand]]

    def cumulative_through(self, wave: str) -> list[WorkloadDemand]:
        out: list[WorkloadDemand] = []
        for name, items in self.waves.items():
            out.extend(items)
            if name == wave:
                break
        return out

    @property
    def all_demand(self) -> list[WorkloadDemand]:
        return [w for items in self.waves.values() for w in items]


def load_plan(path: str) -> Plan:
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    shape_doc = doc.get("node_shape") or {}
    shape = NodeShape(
        vcpu=int(shape_doc.get("vcpu", 4)),
        memory_gib=float(shape_doc.get("memory_gib", 16)),
        name=shape_doc.get("name", "worker"),
    )
    waves: dict[str, list[WorkloadDemand]] = {}
    for wave in doc.get("waves", []):
        name = wave["name"]
        waves[name] = [
            WorkloadDemand(
                name=a["name"],
                namespace=a.get("namespace", ""),
                replicas=int(a.get("replicas", 1)),
                cpu_request_m=float(a.get("cpu_m", 0)),
                mem_request_mib=float(a.get("memory_mib", 0)),
                cpu_usage_p95_m=float(a.get("cpu_usage_m", 0)),
                mem_usage_p95_mib=float(a.get("memory_usage_mib", 0)),
                wave=name,
                source="planned",
            )
            for a in wave.get("applications", [])
        ]
    return Plan(shape=shape, waves=waves)
