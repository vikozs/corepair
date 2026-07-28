"""Collectors.

Node inventory comes from kubectl/oc; demand comes from Prometheus (and VPA
recommendations where the CRD exists). Everything can also be read from, or
written to, a JSON snapshot so that measurements taken during a representative
window can be replayed, shared, and put under review - a licence proposal built
on numbers nobody can reproduce is not a proposal, it is an assertion.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Optional

import requests

from .models import Node, WorkloadDemand

# ------------------------------------------------------------------ kubectl


def _cli() -> str:
    for c in ("kubectl", "oc"):
        if shutil.which(c):
            return c
    raise RuntimeError("neither kubectl nor oc found on PATH (use --snapshot for offline mode)")


def _kget(resource: str, extra: Optional[list[str]] = None) -> dict:
    cmd = [_cli(), "get", resource, "-o", "json", *(extra or [])]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {out.stderr.strip()}")
    return json.loads(out.stdout)


_CPU_RE = re.compile(r"^(\d+(?:\.\d+)?)(m)?$")
_MEM_UNITS = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024,
              "K": 1000 / 1048576, "M": 1000 ** 2 / 1048576, "G": 1000 ** 3 / 1048576}


def parse_cpu(v: str) -> float:
    """Kubernetes CPU quantity -> millicores."""
    if v is None:
        return 0.0
    v = str(v).strip()
    m = _CPU_RE.match(v)
    if not m:
        return 0.0
    n = float(m.group(1))
    return n if m.group(2) else n * 1000.0


def parse_mem(v: str) -> float:
    """Kubernetes memory quantity -> MiB."""
    if v is None:
        return 0.0
    v = str(v).strip()
    for suffix, factor in _MEM_UNITS.items():
        if v.endswith(suffix):
            return float(v[: -len(suffix)]) * factor
    try:
        return float(v) / 1048576.0  # bare bytes
    except ValueError:
        return 0.0


def nodes_from_json(doc: dict) -> list[Node]:
    out = []
    for item in doc.get("items", []):
        meta = item.get("metadata", {})
        cap = item.get("status", {}).get("capacity", {})
        out.append(
            Node(
                name=meta.get("name", ""),
                vcpu=int(parse_cpu(cap.get("cpu", "0")) / 1000),
                memory_gib=round(parse_mem(cap.get("memory", "0")) / 1024, 2),
                labels=meta.get("labels", {}) or {},
                taints=item.get("spec", {}).get("taints", []) or [],
            )
        )
    return out


def collect_nodes() -> list[Node]:
    return nodes_from_json(_kget("nodes"))


def collect_requests() -> list[WorkloadDemand]:
    """Configured requests per workload, aggregated from running pods."""
    pods = _kget("pods", ["--all-namespaces", "--field-selector=status.phase=Running"])
    agg: dict[tuple, WorkloadDemand] = {}
    for p in pods.get("items", []):
        meta = p.get("metadata", {})
        ns = meta.get("namespace", "")
        owner = meta.get("ownerReferences", [{}])
        name = _workload_name(meta.get("name", ""), owner)
        cpu = mem = 0.0
        for c in p.get("spec", {}).get("containers", []):
            req = (c.get("resources", {}) or {}).get("requests", {}) or {}
            cpu += parse_cpu(req.get("cpu"))
            mem += parse_mem(req.get("memory"))
        key = (ns, name)
        if key in agg:
            agg[key].replicas += 1
        else:
            agg[key] = WorkloadDemand(name=name, namespace=ns, replicas=1,
                                      cpu_request_m=cpu, mem_request_mib=mem)
    return list(agg.values())


def _workload_name(pod_name: str, owners: list) -> str:
    if owners and owners[0].get("kind") == "ReplicaSet":
        return re.sub(r"-[a-z0-9]{6,10}-[a-z0-9]{5}$", "", pod_name)
    if owners and owners[0].get("name"):
        return owners[0]["name"]
    return pod_name


# --------------------------------------------------------------- prometheus


class Prometheus:
    """Minimal Prometheus query client.

    On OpenShift, point --prometheus at the thanos-querier route and export
    PROM_TOKEN=$(oc whoami -t).
    """

    def __init__(self, url: str, token: Optional[str] = None, insecure: bool = False,
                 timeout: int = 60):
        self.url = url.rstrip("/")
        self.token = token or os.environ.get("PROM_TOKEN")
        self.verify = not insecure
        self.timeout = timeout

    def query(self, expr: str) -> list[dict]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        r = requests.get(f"{self.url}/api/v1/query", params={"query": expr},
                         headers=headers, verify=self.verify, timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "success":
            raise RuntimeError(f"prometheus: {body.get('error', 'query failed')}")
        return body["data"]["result"]


# Percentile of actual usage over the window, per workload. quantile_over_time
# on the rate is what makes this a defensible "real demand" number rather than
# an instantaneous reading.
CPU_USAGE_Q = """
quantile_over_time(0.95,
  sum by (namespace, workload) (
    node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate
    * on (namespace, pod) group_left(workload)
      namespace_workload_pod:kube_pod_owner:relabel
  )[{window}:5m]
)
"""

MEM_USAGE_Q = """
quantile_over_time(0.95,
  sum by (namespace, workload) (
    container_memory_working_set_bytes{{container!="", image!=""}}
    * on (namespace, pod) group_left(workload)
      namespace_workload_pod:kube_pod_owner:relabel
  )[{window}:5m]
)
"""


def collect_usage(prom: Prometheus, window: str = "30d") -> dict[tuple, tuple[float, float]]:
    """Return {(namespace, workload): (cpu_millicores_p95, mem_mib_p95)}."""
    result: dict[tuple, list[float]] = {}
    for res in prom.query(CPU_USAGE_Q.format(window=window)):
        k = (res["metric"].get("namespace", ""), res["metric"].get("workload", ""))
        result.setdefault(k, [0.0, 0.0])[0] = float(res["value"][1]) * 1000.0
    for res in prom.query(MEM_USAGE_Q.format(window=window)):
        k = (res["metric"].get("namespace", ""), res["metric"].get("workload", ""))
        result.setdefault(k, [0.0, 0.0])[1] = float(res["value"][1]) / 1048576.0
    return {k: (v[0], v[1]) for k, v in result.items()}


def merge_usage(demands: list[WorkloadDemand],
                usage: dict[tuple, tuple[float, float]]) -> list[WorkloadDemand]:
    for d in demands:
        cpu, mem = usage.get((d.namespace, d.name), (0.0, 0.0))
        d.cpu_usage_p95_m = cpu / max(d.replicas, 1)
        d.mem_usage_p95_mib = mem / max(d.replicas, 1)
    return demands


# ----------------------------------------------------------------- snapshot


def save_snapshot(path: str, nodes: list[Node], demands: list[WorkloadDemand],
                  meta: dict) -> None:
    doc = {
        "meta": meta,
        "nodes": [n.__dict__ for n in nodes],
        "workloads": [w.__dict__ for w in demands],
    }
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)


def load_snapshot(path: str) -> tuple[list[Node], list[WorkloadDemand], dict]:
    with open(path) as f:
        doc = json.load(f)
    nodes = [Node(**n) for n in doc.get("nodes", [])]
    workloads = [WorkloadDemand(**w) for w in doc.get("workloads", [])]
    return nodes, workloads, doc.get("meta", {})
