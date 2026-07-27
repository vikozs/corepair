"""Domain models for corepair.

Everything here is a plain dataclass so that the licensing rules can be unit
tested without a cluster, a Prometheus, or a price list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------- node roles

CONTROL_PLANE_LABELS = (
    "node-role.kubernetes.io/master",
    "node-role.kubernetes.io/control-plane",
)
INFRA_LABEL = "node-role.kubernetes.io/infra"
WORKER_LABEL = "node-role.kubernetes.io/worker"

# Red Hat's exemption for infra nodes is conditional: the node must be marked as
# infra *and* must not run application workloads. A label alone is not enough -
# without a taint, the scheduler will happily place application pods there, and
# the exemption no longer holds. corepair therefore treats an untainted infra
# node as subscribed and tells you why.
INFRA_TAINT_KEYS = (
    "node-role.kubernetes.io/infra",
    "infra",
)


@dataclass
class Node:
    name: str
    vcpu: int
    memory_gib: float
    labels: dict = field(default_factory=dict)
    taints: list = field(default_factory=list)

    @property
    def is_control_plane(self) -> bool:
        return any(l in self.labels for l in CONTROL_PLANE_LABELS)

    @property
    def is_infra_labelled(self) -> bool:
        return INFRA_LABEL in self.labels

    @property
    def has_infra_taint(self) -> bool:
        return any(t.get("key") in INFRA_TAINT_KEYS for t in self.taints)

    @property
    def role(self) -> str:
        if self.is_control_plane:
            return "control-plane"
        if self.is_infra_labelled:
            return "infra"
        return "worker"

    def subscription_status(self) -> tuple[bool, str]:
        """Return (counts_toward_subscriptions, reason)."""
        if self.is_control_plane:
            # A node that is both control-plane and worker (compact cluster)
            # does carry application workloads and is not exempt.
            if WORKER_LABEL in self.labels and not self.is_infra_labelled:
                return True, "control-plane node also labelled worker (schedulable)"
            return False, "control-plane nodes are not subscribed"
        if self.is_infra_labelled:
            if self.has_infra_taint:
                return False, "infra node, labelled and tainted"
            return True, (
                "labelled infra but NOT tainted - application pods can still be "
                "scheduled here, so the infra exemption does not apply"
            )
        return True, "worker node"


@dataclass
class WorkloadDemand:
    """Measured or planned demand for one workload."""

    name: str
    namespace: str = ""
    replicas: int = 1
    cpu_request_m: float = 0.0     # millicores, as configured
    cpu_usage_p95_m: float = 0.0   # millicores, as measured
    mem_request_mib: float = 0.0
    mem_usage_p95_mib: float = 0.0
    wave: str = ""
    source: str = "measured"       # measured | planned

    @property
    def total_cpu_request_m(self) -> float:
        return self.cpu_request_m * self.replicas

    @property
    def total_cpu_usage_m(self) -> float:
        return self.cpu_usage_p95_m * self.replicas

    @property
    def total_mem_request_mib(self) -> float:
        return self.mem_request_mib * self.replicas

    @property
    def total_mem_usage_mib(self) -> float:
        return self.mem_usage_p95_mib * self.replicas


@dataclass
class NodeShape:
    """The VM template workers are cut from."""

    vcpu: int
    memory_gib: float
    name: str = "worker"

    def __post_init__(self):
        if self.vcpu <= 0 or self.memory_gib <= 0:
            raise ValueError("node shape must have positive vcpu and memory")


@dataclass
class SubscriptionModel:
    """One way of buying the platform.

    ``unit`` is what you buy:
      core_pair  - virtual: 1 unit covers ``vcpu_per_unit`` vCPU on one node
      bare_metal - physical: 1 unit covers one node up to ``max_cores_per_unit``
    """

    name: str
    unit: str                      # "core_pair" | "bare_metal"
    list_price: float              # per unit, per year, in your currency
    vcpu_per_unit: int = 4         # virtual core-pair = 2 cores = 4 vCPU
    max_cores_per_unit: Optional[int] = None   # bare metal only
    sku: str = ""
    notes: str = ""


@dataclass
class TermOption:
    """A commitment length and the commercial assumptions attached to it."""

    years: int
    discount_pct: float                 # negotiated discount off list, year 1
    # Assumptions - these are the numbers that quietly drive most "savings".
    annual_list_growth_pct: float = 0.0  # assumed list price increase per year
    renewal_discount_pct: Optional[float] = None  # discount available at renewal
    prepaid: bool = False
    label: str = ""

    def name(self) -> str:
        return self.label or f"{self.years}y"
