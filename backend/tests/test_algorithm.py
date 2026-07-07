"""算法单元测试 — 分层角色 + 地址维度统计。"""

from app.core.algorithm import (
    ArpEntry,
    Ipv6NeighborEntry,
    MacInventory,
    analyze_dual_stack,
    infer_device_role,
)
from app.core.models import DeviceRole


def test_host_dual_stack_ratio():
    arp = [
        ArpEntry("10.0.0.1", "0011-2233-4401"),
        ArpEntry("10.0.0.2", "0011-2233-4402"),
        ArpEntry("10.0.0.3", "0011-2233-4403"),
    ]
    ipv6 = [
        Ipv6NeighborEntry("fe80::1", "0011-2233-4401"),
        Ipv6NeighborEntry("2001:db8::2", "0011-2233-4402"),
    ]

    stats, records, _ = analyze_dual_stack(arp, ipv6)

    assert stats.host_count == 3
    assert stats.total_devices == 3
    assert stats.host_dual_stack_count == 2
    assert stats.host_ipv4_only_count == 1
    assert stats.dual_stack_ratio == 66.67
    assert stats.ipv6_only_count == 0
    assert stats.total_ipv4_addresses == 3
    assert stats.total_ipv6_global_addresses == 1
    assert stats.total_ipv6_link_local_addresses == 1
    hosts = [r for r in records if r.role == DeviceRole.HOST]
    assert len(hosts) == 3


def test_router_marked_by_is_router_flag():
    arp = [ArpEntry("10.1.1.1", "001c-54ff-0902", vlan=7, interface="Eth-Trunk2")]
    ipv6 = [
        Ipv6NeighborEntry(
            "240b:8050:3800:100::2",
            "001c-54ff-0902",
            is_router=True,
            interface="Eth-Trunk2",
        ),
        Ipv6NeighborEntry(
            "fe80::21c:54ff:feff:902",
            "001c-54ff-0902",
            is_router=True,
        ),
    ]

    stats, records, warnings = analyze_dual_stack(arp, ipv6)
    router = next(r for r in records if r.mac == "00:1C:54:FF:09:02")

    assert router.role == DeviceRole.ROUTER
    assert router.is_router is True
    assert stats.host_count == 0
    assert stats.network_device_count == 1
    assert stats.network_dual_stack_count == 1
    assert stats.total_ipv6_global_addresses == 1
    assert any("网络设备" in w for w in warnings)


def test_nd_neighbor_tracked_separately():
    arp = [ArpEntry("10.0.0.1", "0011-2233-4401")]
    ipv6 = [
        Ipv6NeighborEntry("fe80::1", "0011-2233-4401"),
        Ipv6NeighborEntry("240b::99", "8061-5f13-0f8d", is_router=False),
    ]

    stats, records, _ = analyze_dual_stack(arp, ipv6)

    assert stats.host_count == 1
    assert stats.host_dual_stack_count == 1
    assert stats.nd_neighbor_count == 1
    assert stats.nd_neighbor_with_ipv6_count == 1
    nd = next(r for r in records if r.role == DeviceRole.ND_NEIGHBOR)
    assert nd.mac == "80:61:5F:13:0F:8D"
    assert stats.observable_mac_count == 2


def test_infer_host_vs_router_heuristic():
    host = MacInventory(
        mac="00:11:22:33:44:01",
        ipv4_addresses={"10.0.0.1"},
        in_arp=True,
    )
    router = MacInventory(
        mac="00:1C:54:FF:09:02",
        ipv4_addresses={f"10.0.0.{i}" for i in range(1, 6)},
        interfaces={"Eth-Trunk2"},
        in_arp=True,
    )
    assert infer_device_role(host) == DeviceRole.HOST
    assert infer_device_role(router) == DeviceRole.ROUTER


def test_ipv4_only_hosts():
    arp = [
        ArpEntry("10.0.0.1", "0011-2233-4401"),
        ArpEntry("10.0.0.2", "0011-2233-4402"),
    ]
    stats, records, _ = analyze_dual_stack(arp, [])
    assert stats.host_ipv4_only_count == 2
    assert stats.host_dual_stack_count == 0
    assert all(r.stack_type.value == "ipv4_only" for r in records if r.role == DeviceRole.HOST)
