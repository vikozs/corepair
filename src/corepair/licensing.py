"""Subscription counting rules.

This module is the point of the project. The arithmetic is simple; what is
non-obvious - and what people get wrong in both directions - is *what counts*:

1. Only nodes that can run application workloads are subscribed. Control-plane
   nodes are exempt. Infra nodes are exempt only if they are genuinely reserved
   for infrastructure (labelled AND tainted).

2. A virtual core-pair covers 2 cores = 4 vCPU, and rounding happens
   **per node**, not across the cluster. Ten nodes of 6 vCPU need 20 core-pairs,
   not 15. Node shape therefore drives licence cost independently of how much
   work the cluster actually does. This is the single biggest lever most people
   never look at.

3. Required node count is driven by whichever resource saturates first. For
   memory-bound workloads - which most enterprise Java and .NET estates are -
   that is RAM, not CPU, so buying CPU-rich nodes raises the licence bill
   without removing the actual constraint.

None of this is pricing advice, and no price list ships with this tool. See
README.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .models import Node, NodeShape, SubscriptionModel, WorkloadDemand


# ------------------------------------------------------------------ counting


def core_pairs_for_node(vcpu: int, vcpu_per_unit: int = 4) -> int:
    """Core-pairs needed for a single node. Always rounds up, minimum 1."""
    if vcpu <= 0:
        return 0
    return max(1, math.ceil(vcpu / vcpu_per_unit))


@dataclass
class NodeAssessment:
    node: Node
    subscribed: bool
    reason: str
    core_pairs: int


@dataclass
class ClusterCount:
    assessments: list[NodeAssessment]
    vcpu_per_unit: int

    @property
    def core_pairs(self) -> int:
        return sum(a.core_pairs for a in self.assessments if a.subscribed)

    @property
    def subscribed_nodes(self) -> list[NodeAssessment]:
        return [a for a in self.assessments if a.subscribed]

    @property
    def exempt_nodes(self) -> list[NodeAssessment]:
        return [a for a in self.assessments if not a.subscribed]

    @property
    def subscribed_vcpu(self) -> int:
        return sum(a.node.vcpu for a in self.subscribed_nodes)

    @property
    def rounding_waste_vcpu(self) -> int:
        """vCPU you are paying for but do not have, purely from per-node rounding."""
        covered = self.core_pairs * self.vcpu_per_unit
        return covered - self.subscribed_vcpu

    def unlabelled_infra_candidates(self) -> list[NodeAssessment]:
        return [
            a for a in self.assessments
            if a.subscribed and a.node.is_infra_labelled and not a.node.has_infra_taint
        ]


def count_cluster(nodes: Iterable[Node], vcpu_per_unit: int = 4) -> ClusterCount:
    """Count subscriptions for a live node inventory."""
    out = []
    for n in nodes:
        subscribed, reason = n.subscription_status()
        cp = core_pairs_for_node(n.vcpu, vcpu_per_unit) if subscribed else 0
        out.append(NodeAssessment(node=n, subscribed=subscribed, reason=reason, core_pairs=cp))
    return ClusterCount(assessments=out, vcpu_per_unit=vcpu_per_unit)


# ------------------------------------------------------------------- sizing


@dataclass
class Sizing:
    """How many nodes a given demand needs on a given node shape."""

    shape: NodeShape
    nodes_for_cpu: int
    nodes_for_memory: int
    nodes: int
    binding_resource: str
    cpu_vcpu: float
    memory_gib: float
    headroom_pct: float
    ha_spare_nodes: int

    @property
    def cpu_utilisation_pct(self) -> float:
        cap = self.nodes * self.shape.vcpu
        return 100.0 * self.cpu_vcpu / cap if cap else 0.0

    @property
    def memory_utilisation_pct(self) -> float:
        cap = self.nodes * self.shape.memory_gib
        return 100.0 * self.memory_gib / cap if cap else 0.0


def size_cluster(
    cpu_vcpu: float,
    memory_gib: float,
    shape: NodeShape,
    headroom_pct: float = 25.0,
    ha_spare_nodes: int = 1,
    usable_fraction: float = 0.90,
) -> Sizing:
    """Required node count for a demand, on a node shape.

    ``usable_fraction`` accounts for kubelet/system reserved and daemonsets:
    a 16 vCPU node does not offer 16 vCPU to pods. ``headroom_pct`` is growth
    and burst room on top of demand. ``ha_spare_nodes`` is the N+1 (or N+2)
    capacity you must be able to lose without eviction - and which you must
    also license.
    """
    factor = 1.0 + headroom_pct / 100.0
    usable_cpu = shape.vcpu * usable_fraction
    usable_mem = shape.memory_gib * usable_fraction

    n_cpu = math.ceil(cpu_vcpu * factor / usable_cpu) if usable_cpu else 0
    n_mem = math.ceil(memory_gib * factor / usable_mem) if usable_mem else 0
    base = max(n_cpu, n_mem, 1)
    binding = "memory" if n_mem >= n_cpu else "cpu"
    return Sizing(
        shape=shape,
        nodes_for_cpu=n_cpu,
        nodes_for_memory=n_mem,
        nodes=base + ha_spare_nodes,
        binding_resource=binding,
        cpu_vcpu=cpu_vcpu,
        memory_gib=memory_gib,
        headroom_pct=headroom_pct,
        ha_spare_nodes=ha_spare_nodes,
    )


def units_for_sizing(sizing: Sizing, model: SubscriptionModel) -> int:
    """Subscription units required for a sized cluster."""
    if model.unit == "bare_metal":
        cores = sizing.shape.vcpu / 2  # 1 physical core = 2 vCPU with SMT on
        per_node = 1
        if model.max_cores_per_unit:
            per_node = max(1, math.ceil(cores / model.max_cores_per_unit))
        return sizing.nodes * per_node
    return sizing.nodes * core_pairs_for_node(sizing.shape.vcpu, model.vcpu_per_unit)


# -------------------------------------------------------------- right-sizing


@dataclass
class DemandTotals:
    cpu_request_vcpu: float
    cpu_usage_vcpu: float
    mem_request_gib: float
    mem_usage_gib: float
    workloads: int

    @property
    def cpu_overcommit_ratio(self) -> float:
        return self.cpu_request_vcpu / self.cpu_usage_vcpu if self.cpu_usage_vcpu else 0.0

    @property
    def mem_overcommit_ratio(self) -> float:
        return self.mem_request_gib / self.mem_usage_gib if self.mem_usage_gib else 0.0


def totals(demands: Iterable[WorkloadDemand]) -> DemandTotals:
    d = list(demands)
    return DemandTotals(
        cpu_request_vcpu=sum(w.total_cpu_request_m for w in d) / 1000.0,
        cpu_usage_vcpu=sum(w.total_cpu_usage_m for w in d) / 1000.0,
        mem_request_gib=sum(w.total_mem_request_mib for w in d) / 1024.0,
        mem_usage_gib=sum(w.total_mem_usage_mib for w in d) / 1024.0,
        workloads=len(d),
    )


def right_size(
    demands: Iterable[WorkloadDemand],
    cpu_target_pct: float = 150.0,
    mem_target_pct: float = 130.0,
    min_cpu_m: float = 50.0,
    min_mem_mib: float = 128.0,
) -> list[WorkloadDemand]:
    """Recommend requests as a margin over measured p95 usage.

    This is a deliberately conservative, transparent heuristic, not a
    replacement for VPA. Where VPA recommendations are available, use those:
    ``corepair measure`` reads them when the VPA CRD is present.
    """
    out = []
    for w in demands:
        cpu = max(min_cpu_m, w.cpu_usage_p95_m * cpu_target_pct / 100.0)
        mem = max(min_mem_mib, w.mem_usage_p95_mib * mem_target_pct / 100.0)
        out.append(
            WorkloadDemand(
                name=w.name,
                namespace=w.namespace,
                replicas=w.replicas,
                cpu_request_m=round(cpu),
                cpu_usage_p95_m=w.cpu_usage_p95_m,
                mem_request_mib=round(mem),
                mem_usage_p95_mib=w.mem_usage_p95_mib,
                wave=w.wave,
                source=w.source,
            )
        )
    return out
