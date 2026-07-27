import pytest

from corepair.licensing import (
    core_pairs_for_node,
    count_cluster,
    right_size,
    size_cluster,
    totals,
    units_for_sizing,
)
from corepair.models import Node, NodeShape, SubscriptionModel, WorkloadDemand


def worker(name, vcpu, mem=32.0, **labels):
    return Node(name=name, vcpu=vcpu, memory_gib=mem,
                labels={"node-role.kubernetes.io/worker": "", **labels})


def test_core_pair_rounds_up_per_node():
    assert core_pairs_for_node(4) == 1
    assert core_pairs_for_node(5) == 2      # the whole point: no partial units
    assert core_pairs_for_node(8) == 2
    assert core_pairs_for_node(1) == 1      # minimum one
    assert core_pairs_for_node(0) == 0


def test_rounding_is_per_node_not_per_cluster():
    """Ten 6-vCPU nodes need 20 core-pairs, not 15. This is the lever people miss."""
    nodes = [worker(f"w{i}", 6) for i in range(10)]
    c = count_cluster(nodes)
    assert c.subscribed_vcpu == 60
    assert c.core_pairs == 20
    assert c.rounding_waste_vcpu == 20


def test_control_plane_is_exempt():
    nodes = [
        Node("m1", 8, 32, labels={"node-role.kubernetes.io/control-plane": ""}),
        worker("w1", 8),
    ]
    c = count_cluster(nodes)
    assert c.core_pairs == 2
    assert len(c.exempt_nodes) == 1


def test_schedulable_control_plane_is_not_exempt():
    """A compact cluster runs applications on the masters, so they count."""
    n = Node("m1", 8, 32, labels={
        "node-role.kubernetes.io/control-plane": "",
        "node-role.kubernetes.io/worker": "",
    })
    subscribed, reason = n.subscription_status()
    assert subscribed
    assert "schedulable" in reason


def test_infra_node_needs_the_taint_to_be_exempt():
    labelled_only = Node("i1", 8, 48, labels={"node-role.kubernetes.io/infra": ""})
    tainted = Node("i2", 8, 48, labels={"node-role.kubernetes.io/infra": ""},
                   taints=[{"key": "node-role.kubernetes.io/infra", "effect": "NoSchedule"}])

    sub1, reason1 = labelled_only.subscription_status()
    sub2, _ = tainted.subscription_status()
    assert sub1 is True and "NOT tainted" in reason1
    assert sub2 is False

    c = count_cluster([labelled_only, tainted])
    assert c.core_pairs == 2
    assert [a.node.name for a in c.unlabelled_infra_candidates()] == ["i1"]


def test_memory_can_be_the_binding_resource():
    """The finding that changes purchasing decisions for memory-bound estates."""
    shape = NodeShape(vcpu=16, memory_gib=32)
    s = size_cluster(cpu_vcpu=20, memory_gib=400, shape=shape, headroom_pct=0, ha_spare_nodes=0)
    assert s.binding_resource == "memory"
    assert s.nodes_for_memory > s.nodes_for_cpu
    # Doubling vCPU per node does not reduce the node count at all...
    bigger_cpu = size_cluster(20, 400, NodeShape(32, 32), headroom_pct=0, ha_spare_nodes=0)
    assert bigger_cpu.nodes == s.nodes
    # ...but it does double the licence bill.
    m = SubscriptionModel("cp", "core_pair", 1000.0)
    assert units_for_sizing(bigger_cpu, m) == 2 * units_for_sizing(s, m)


def test_more_memory_per_node_reduces_licences_for_memory_bound_work():
    cpu, mem = 20, 400
    small = size_cluster(cpu, mem, NodeShape(8, 32), headroom_pct=0, ha_spare_nodes=0)
    ramful = size_cluster(cpu, mem, NodeShape(8, 64), headroom_pct=0, ha_spare_nodes=0)
    m = SubscriptionModel("cp", "core_pair", 1000.0)
    assert units_for_sizing(ramful, m) < units_for_sizing(small, m)


def test_headroom_and_ha_are_licensed_too():
    shape = NodeShape(8, 32)
    bare = size_cluster(40, 100, shape, headroom_pct=0, ha_spare_nodes=0)
    real = size_cluster(40, 100, shape, headroom_pct=25, ha_spare_nodes=1)
    assert real.nodes > bare.nodes


def test_bare_metal_units_are_per_node():
    shape = NodeShape(64, 512)
    s = size_cluster(100, 800, shape, headroom_pct=0, ha_spare_nodes=0)
    bm = SubscriptionModel("bm", "bare_metal", 20000.0)
    cp = SubscriptionModel("cp", "core_pair", 3000.0)
    assert units_for_sizing(s, bm) == s.nodes
    assert units_for_sizing(s, cp) == s.nodes * 16


def test_bare_metal_core_cap_adds_units():
    shape = NodeShape(256, 1024)   # 128 physical cores
    s = size_cluster(200, 900, shape, headroom_pct=0, ha_spare_nodes=0)
    capped = SubscriptionModel("bm", "bare_metal", 1.0, max_cores_per_unit=64)
    assert units_for_sizing(s, capped) == s.nodes * 2


def test_totals_and_overcommit():
    d = [
        WorkloadDemand("a", replicas=2, cpu_request_m=1000, cpu_usage_p95_m=100,
                       mem_request_mib=2048, mem_usage_p95_mib=1024),
    ]
    t = totals(d)
    assert t.cpu_request_vcpu == pytest.approx(2.0)
    assert t.cpu_usage_vcpu == pytest.approx(0.2)
    assert t.cpu_overcommit_ratio == pytest.approx(10.0)
    assert t.mem_overcommit_ratio == pytest.approx(2.0)


def test_right_size_uses_margin_over_measured_usage():
    d = [WorkloadDemand("a", cpu_request_m=2000, cpu_usage_p95_m=200,
                        mem_request_mib=4096, mem_usage_p95_mib=2000)]
    r = right_size(d, cpu_target_pct=150, mem_target_pct=130)[0]
    assert r.cpu_request_m == 300
    assert r.mem_request_mib == 2600
    assert r.cpu_request_m < d[0].cpu_request_m


def test_right_size_respects_floors():
    d = [WorkloadDemand("tiny", cpu_usage_p95_m=1, mem_usage_p95_mib=1)]
    r = right_size(d)[0]
    assert r.cpu_request_m == 50
    assert r.mem_request_mib == 128
