"""
IPv4/IPv6 双栈设备统计算法

分层模型（核心交换机 ARP/ND 视角）：
1. HOST — 可直连观测的终端（办公室电脑等），以 ARP 为主、排除路由器特征
2. ROUTER / NETWORK — 办公室出口路由器、汇聚交换机等网络设备
3. ND_NEIGHBOR — 仅在 ND 表可见的下游三层邻居（代表下游网段，其下 PC 不能逐台看见）

双栈占比按 HOST 计算；网络设备的 IPv4/IPv6 能力单独统计，用于评估下游办公室是否具备双栈条件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.models import DeviceRecord, DeviceRole, StackType, SurveyStatistics

MAC_PATTERN = re.compile(
    r"(?:[0-9a-fA-F]{4}[-.]){2}[0-9a-fA-F]{4}|"
    r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}"
)

IGNORED_MAC_PREFIXES = (
    "01005E",
    "3333",
    "FFFFFFFFFFFF",
)

GATEWAY_MAC_HINTS: set[str] = set()

ROUTER_IPV4_THRESHOLD = 5
ROUTER_VLAN_THRESHOLD = 3


@dataclass
class ArpEntry:
    ipv4: str
    mac: str
    vlan: int | None = None
    interface: str | None = None


@dataclass
class Ipv6NeighborEntry:
    ipv6: str
    mac: str
    vlan: int | None = None
    interface: str | None = None
    state: str | None = None
    is_router: bool | None = None


@dataclass
class MacInventory:
    mac: str
    ipv4_addresses: set[str] = field(default_factory=set)
    ipv6_addresses: set[str] = field(default_factory=set)
    vlan_ids: set[int] = field(default_factory=set)
    interfaces: set[str] = field(default_factory=set)
    in_arp: bool = False
    is_router: bool = False
    nd_record_count: int = 0


def normalize_mac(raw_mac: str) -> str:
    cleaned = re.sub(r"[^0-9a-fA-F]", "", raw_mac).upper()
    if len(cleaned) != 12:
        raise ValueError(f"invalid MAC: {raw_mac}")
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2))


def is_ignored_mac(mac: str) -> bool:
    compact = mac.replace(":", "")
    if compact in GATEWAY_MAC_HINTS:
        return True
    for prefix in IGNORED_MAC_PREFIXES:
        if compact.startswith(prefix):
            return True
    return False


def is_valid_ipv4(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def is_valid_ipv6(ip: str) -> bool:
    if not ip:
        return False
    ip = ip.split("%", 1)[0].strip().lower()
    if not ip or ip == "::":
        return False
    if ip.startswith("ff"):
        return False
    if ":" not in ip:
        return False
    if ip.count(":") < 1:
        return False
    return True


def normalize_ipv6(ip: str) -> str:
    return ip.split("%", 1)[0].strip().lower()


def is_link_local_ipv6(ip: str) -> bool:
    return normalize_ipv6(ip).startswith("fe80:")


def is_global_ipv6(ip: str) -> bool:
    return is_valid_ipv6(ip) and not is_link_local_ipv6(ip)


def split_ipv6_addresses(addresses: set[str]) -> tuple[list[str], list[str]]:
    global_addrs = sorted(a for a in addresses if is_global_ipv6(a))
    link_local = sorted(a for a in addresses if is_link_local_ipv6(a))
    return global_addrs, link_local


def classify_stack(has_ipv4: bool, has_ipv6: bool) -> StackType:
    if has_ipv4 and has_ipv6:
        return StackType.DUAL_STACK
    if has_ipv4:
        return StackType.IPV4_ONLY
    if has_ipv6:
        return StackType.IPV6_ONLY
    return StackType.IPV4_ONLY


def classify_host_stack(item: MacInventory) -> StackType:
    has_ipv4 = bool(item.ipv4_addresses)
    has_ipv6 = bool(item.ipv6_addresses)
    if has_ipv4 and has_ipv6:
        return StackType.DUAL_STACK
    return StackType.IPV4_ONLY


def infer_device_role(item: MacInventory) -> DeviceRole:
    if item.is_router:
        return DeviceRole.ROUTER
    if not item.in_arp:
        return DeviceRole.ND_NEIGHBOR

    if len(item.ipv4_addresses) >= ROUTER_IPV4_THRESHOLD:
        return DeviceRole.ROUTER
    if (
        len(item.vlan_ids) >= ROUTER_VLAN_THRESHOLD
        and len(item.ipv4_addresses) >= 2
    ):
        return DeviceRole.ROUTER

    ifaces = [i.lower() for i in item.interfaces]
    if any(i.startswith("vlanif") for i in ifaces):
        return DeviceRole.NETWORK
    if any("trunk" in i for i in ifaces) and len(item.ipv4_addresses) >= 2:
        return DeviceRole.ROUTER

    return DeviceRole.HOST


def build_mac_inventory(
    arp_entries: list[ArpEntry],
    ipv6_entries: list[Ipv6NeighborEntry],
) -> tuple[dict[str, MacInventory], list[str]]:
    warnings: list[str] = []
    inventory: dict[str, MacInventory] = {}

    def ensure(mac: str) -> MacInventory:
        if mac not in inventory:
            inventory[mac] = MacInventory(mac=mac)
        return inventory[mac]

    for entry in arp_entries:
        if not is_valid_ipv4(entry.ipv4):
            continue
        try:
            mac = normalize_mac(entry.mac)
        except ValueError:
            warnings.append(f"跳过无效 ARP MAC: {entry.mac}")
            continue
        if is_ignored_mac(mac):
            continue
        item = ensure(mac)
        item.in_arp = True
        item.ipv4_addresses.add(entry.ipv4)
        if entry.vlan is not None:
            item.vlan_ids.add(entry.vlan)
        if entry.interface:
            item.interfaces.add(entry.interface)

    orphan_nd_macs: set[str] = set()
    for entry in ipv6_entries:
        if not is_valid_ipv6(entry.ipv6):
            continue
        try:
            mac = normalize_mac(entry.mac)
        except ValueError:
            warnings.append(f"跳过无效 IPv6 邻居 MAC: {entry.mac}")
            continue
        if is_ignored_mac(mac):
            continue

        item = ensure(mac)
        item.nd_record_count += 1
        item.ipv6_addresses.add(normalize_ipv6(entry.ipv6))
        if entry.is_router:
            item.is_router = True
        if entry.vlan is not None:
            item.vlan_ids.add(entry.vlan)
        if entry.interface:
            item.interfaces.add(entry.interface)
        if not item.in_arp:
            orphan_nd_macs.add(mac)

    if orphan_nd_macs:
        warnings.append(
            f"ND 表中有 {len(orphan_nd_macs)} 个 MAC 未出现在 ARP 表，"
            "已归类为下游网络邻居（办公室出口路由器等；其下 PC 不能从核心 ARP 逐台观测）"
        )

    return inventory, warnings


def compute_statistics(inventory: dict[str, MacInventory]) -> SurveyStatistics:
    host_dual = host_ipv4 = 0
    net_dual = net_ipv4 = 0
    nd_with_ipv6 = 0
    host_count = network_count = nd_count = 0

    total_ipv4 = total_global_v6 = total_ll_v6 = 0

    for item in inventory.values():
        role = infer_device_role(item)
        global_v6, link_local_v6 = split_ipv6_addresses(item.ipv6_addresses)
        has_ipv4 = bool(item.ipv4_addresses)
        has_ipv6 = bool(item.ipv6_addresses)
        has_global_v6 = bool(global_v6)

        total_ipv4 += len(item.ipv4_addresses)
        total_global_v6 += len(global_v6)
        total_ll_v6 += len(link_local_v6)

        if role == DeviceRole.HOST:
            host_count += 1
            if has_ipv4 and has_ipv6:
                host_dual += 1
            elif has_ipv4:
                host_ipv4 += 1
        elif role == DeviceRole.ND_NEIGHBOR:
            nd_count += 1
            if has_ipv6:
                nd_with_ipv6 += 1
        else:
            network_count += 1
            if has_ipv4 and has_ipv6:
                net_dual += 1
            elif has_ipv4:
                net_ipv4 += 1

    def ratio(count: int, total: int) -> float:
        if total == 0:
            return 0.0
        return round(count / total * 100, 2)

    return SurveyStatistics(
        total_devices=host_count,
        dual_stack_count=host_dual,
        ipv4_only_count=host_ipv4,
        ipv6_only_count=0,
        dual_stack_ratio=ratio(host_dual, host_count),
        ipv4_only_ratio=ratio(host_ipv4, host_count),
        ipv6_only_ratio=0.0,
        host_count=host_count,
        network_device_count=network_count,
        nd_neighbor_count=nd_count,
        host_dual_stack_count=host_dual,
        host_ipv4_only_count=host_ipv4,
        network_dual_stack_count=net_dual,
        network_ipv4_only_count=net_ipv4,
        nd_neighbor_with_ipv6_count=nd_with_ipv6,
        total_ipv4_addresses=total_ipv4,
        total_ipv6_global_addresses=total_global_v6,
        total_ipv6_link_local_addresses=total_ll_v6,
        observable_mac_count=len(inventory),
    )


def inventory_to_records(inventory: dict[str, MacInventory]) -> list[DeviceRecord]:
    records: list[DeviceRecord] = []
    for item in sorted(inventory.values(), key=lambda x: x.mac):
        role = infer_device_role(item)
        global_v6, link_local_v6 = split_ipv6_addresses(item.ipv6_addresses)
        has_ipv4 = bool(item.ipv4_addresses)
        has_ipv6 = bool(item.ipv6_addresses)

        if role == DeviceRole.HOST:
            stack = classify_host_stack(item)
        elif role == DeviceRole.ND_NEIGHBOR:
            stack = classify_stack(has_ipv4, has_ipv6)
        else:
            stack = classify_stack(has_ipv4, has_ipv6)

        records.append(
            DeviceRecord(
                mac=item.mac,
                role=role,
                is_router=item.is_router or role == DeviceRole.ROUTER,
                stack_type=stack,
                ipv4_addresses=sorted(item.ipv4_addresses),
                ipv6_addresses=sorted(item.ipv6_addresses),
                global_ipv6_addresses=global_v6,
                link_local_ipv6_addresses=link_local_v6,
                vlan_ids=sorted(item.vlan_ids),
                interfaces=sorted(item.interfaces),
            )
        )
    return records


def analyze_dual_stack(
    arp_entries: list[ArpEntry],
    ipv6_entries: list[Ipv6NeighborEntry],
) -> tuple[SurveyStatistics, list[DeviceRecord], list[str]]:
    inventory, warnings = build_mac_inventory(arp_entries, ipv6_entries)
    statistics = compute_statistics(inventory)
    records = inventory_to_records(inventory)

    if statistics.network_device_count:
        warnings.append(
            f"可观测网络设备 {statistics.network_device_count} 台"
            f"（双栈 {statistics.network_dual_stack_count}、纯 IPv4 {statistics.network_ipv4_only_count}），"
            "代表各办公室出口路由器/交换机及其 IPv4/IPv6 能力"
        )
    if statistics.nd_neighbor_count:
        warnings.append(
            f"下游 ND 邻居 {statistics.nd_neighbor_count} 个"
            f"（具备 IPv6 邻居信息 {statistics.nd_neighbor_with_ipv6_count} 个），"
            "其下终端需通过该网关间接推断双栈能力"
        )

    return statistics, records, warnings
