"""5 万条 ARP 规模解析性能测试。"""

from app.collectors.huawei import HuaweiCollector
from app.core.algorithm import analyze_dual_stack
from app.core.models import Vendor
from app.core.scale import TARGET_ARP_ENTRIES


def _generate_arp_output(count: int) -> str:
    lines = ["IP Address      MAC Address    VLAN ID  Interface  Type"]
    for i in range(count):
        o1 = i & 0xFF
        o2 = (i >> 8) & 0xFF
        lines.append(
            f"10.{o2}.{o1 // 256}.{o1 % 256}  "
            f"{o1:04x}-{o2:04x}-{i % 10000:04x}  "
            f"{i % 4094 + 1}  GE1/0/{i % 48 + 1}  Dynamic"
        )
    return "\n".join(lines)


def test_parse_50k_arp_entries():
    output = _generate_arp_output(TARGET_ARP_ENTRIES)
    collector = HuaweiCollector(
        host="127.0.0.1",
        username="u",
        password="p",
        vendor=Vendor.HUAWEI,
        model="S12700",
    )
    entries = collector.parse_arp(output)
    assert len(entries) == TARGET_ARP_ENTRIES

    stats, records, _ = analyze_dual_stack(entries, [])
    assert stats.total_devices == TARGET_ARP_ENTRIES
    assert stats.ipv4_only_count == TARGET_ARP_ENTRIES
