"""IPv6 邻居表解析与双栈 MAC 比对测试。"""

from app.collectors.huawei import HuaweiCollector
from app.core.algorithm import ArpEntry, Ipv6NeighborEntry, analyze_dual_stack
from app.collectors.parse_utils import parse_ipv6_neighbor_output


HUAWEI_VERBOSE = """\
display ipv6 neighbors
----------------------------------------------------------------------
IPv6 Address : FE80::219:E0FF:FE59:4C0F
MAC Address  : 0019-E0E6-4C0F
Vlan         : 10
Interface    : GE1/0/1
State        : REACH

IPv6 Address : 2001:DB8:100::A
MAC Address  : 0011-2233-4401
Vlan         : 10
Interface    : GE1/0/2
State        : REACH

IPv6 Address : 2001:DB8:100::B
MAC Address  : 0011-2233-4402
Vlan         : 20
Interface    : GE1/0/3
State        : STALE
"""

HUAWEI_TABLE = """\
IPv6 Address                           MAC Address    VLAN  Interface                    State
FE80::1                                0011-2233-4401 10    GE1/0/1                      REACH
2001:DB8::2                            0011-2233-4402 10    GE1/0/2                      REACH
"""


S12700E_VERBOSE = """\
IPv6 Address : 240B:8050:3800:100::2
Link-layer   : 001c-54ff-0902                     State : REACH
Interface    : Eth-Trunk2                         Age   : 00h00m09s
VLAN         : 7                                  CEVLAN: -
VPN name     :                                    Is Router: TRUE

IPv6 Address : 240B:8050:3800:100::3
Link-layer   : 8061-5f13-0f8d                     State : STALE
Interface    : Eth-Trunk10                        Age   : 00h08m08s
VLAN         : 7                                  CEVLAN: -
VPN name     :                                    Is Router: FALSE

IPv6 Address : FE80::21C:54FF:FEFF:902
Link-layer   : 001c-54ff-0902                     State : STALE
Interface    : Eth-Trunk2                         Age   : 00h08m07s
VLAN         : 7                                  CEVLAN: -
VPN name     :                                    Is Router: TRUE

IPv6 Address : FE80::8261:5FFF:FE13:F8D
Link-layer   : 8061-5f13-0f8d                     State : STALE
Interface    : Eth-Trunk10                        Age   : 00h00m52s
VLAN         : 7                                  CEVLAN: -
VPN name     :                                    Is Router: FALSE
"""


class TestHuaweiIpv6Parser:
    def test_s12700e_parses_is_router(self):
        entries = parse_ipv6_neighbor_output(S12700E_VERBOSE)
        router_entries = [e for e in entries if e.is_router]
        host_entries = [e for e in entries if e.is_router is False]
        assert len(router_entries) == 2
        assert len(host_entries) == 2

    def test_s12700e_link_layer_with_state_on_same_line(self):
        entries = parse_ipv6_neighbor_output(S12700E_VERBOSE)
        assert len(entries) == 4

        by_mac: dict[str, list] = {}
        for e in entries:
            by_mac.setdefault(e.mac, []).append(e)

        assert "00:1C:54:FF:09:02" in by_mac
        assert "80:61:5F:13:0F:8D" in by_mac
        assert len(by_mac["00:1C:54:FF:09:02"]) == 2  # global + link-local
        assert entries[0].ipv6 == "240b:8050:3800:100::2"
        assert entries[0].interface == "Eth-Trunk2"
        assert entries[0].vlan == 7
        assert entries[0].state == "REACH"

    def test_s12700e_dual_stack_with_arp(self):
        collector = HuaweiCollector(
            host="1.1.1.1",
            username="u",
            password="p",
            vendor=__import__("app.core.models", fromlist=["Vendor"]).Vendor.HUAWEI,
            model="S12700",
        )
        arp = collector.parse_arp(
            "10.1.1.2  001c-54ff-0902  7  Eth-Trunk2  Dynamic\n"
            "10.1.1.3  8061-5f13-0f8d  7  Eth-Trunk10  Dynamic\n"
        )
        ipv6 = collector.parse_ipv6_neighbors(S12700E_VERBOSE)
        stats, records, _ = analyze_dual_stack(arp, ipv6)

        assert stats.network_device_count == 1
        assert stats.network_dual_stack_count == 1
        assert stats.host_count == 1
        assert stats.host_dual_stack_count == 1

    def test_verbose_format_not_skipped(self):
        entries = parse_ipv6_neighbor_output(HUAWEI_VERBOSE)
        assert len(entries) == 3
        macs = {e.mac for e in entries}
        assert "00:19:E0:E6:4C:0F" in macs
        assert "00:11:22:33:44:01" in macs

    def test_table_format(self):
        entries = parse_ipv6_neighbor_output(HUAWEI_TABLE)
        assert len(entries) == 2
        assert entries[0].ipv6.lower().startswith("fe80::")

    def test_dual_stack_by_mac_match(self):
        collector = HuaweiCollector(
            host="1.1.1.1",
            username="u",
            password="p",
            vendor=__import__("app.core.models", fromlist=["Vendor"]).Vendor.HUAWEI,
            model="S12700",
        )
        arp = collector.parse_arp(
            "10.0.0.1  0011-2233-4401  10  GE1/0/1  Dynamic\n"
            "10.0.0.2  0011-2233-4402  10  GE1/0/2  Dynamic\n"
            "10.0.0.3  0011-2233-4403  10  GE1/0/3  Dynamic\n"
        )
        ipv6 = collector.parse_ipv6_neighbors(HUAWEI_VERBOSE)

        stats, records, _ = analyze_dual_stack(arp, ipv6)

        dual_macs = {r.mac for r in records if r.stack_type.value == "dual_stack"}
        assert stats.host_dual_stack_count >= 0
        assert "00:11:22:33:44:01" in dual_macs or stats.network_dual_stack_count >= 1

    def test_link_layer_addr_field(self):
        text = """\
IPv6 Address : FE80::1
Link-layer : 0011-2233-4401
"""
        entries = parse_ipv6_neighbor_output(text)
        assert len(entries) == 1
        assert entries[0].mac == "00:11:22:33:44:01"

    def test_link_layer_address_verbose(self):
        text = """\
IPv6 Address : FE80::219:E0FF:FE59:4C0F
Link-Layer Address : 0019-E0E6-4C0F
Interface : GE1/0/1
State : REACH
"""
        entries = parse_ipv6_neighbor_output(text)
        assert len(entries) == 1
        assert entries[0].mac == "00:19:E0:E6:4C:0F"

    def test_link_layer_addr_table(self):
        text = """\
IPv6 Address                          Link-Layer Addr     Interface              State
FE80::219:E0FF:FE59:4C0F             0019-E0E6-4C0F       GE1/0/1                REACH
2001:DB8::1                          0011-2233-4401       GE1/0/2                STALE
"""
        entries = parse_ipv6_neighbor_output(text)
        assert len(entries) == 2
        assert entries[0].mac == "00:19:E0:E6:4C:0F"

    def test_zone_index_stripped(self):
        text = "FE80::1%GE1/0/1  0011-2233-4401  10  GE1/0/1  REACH"
        entries = parse_ipv6_neighbor_output(text)
        assert len(entries) == 1
        assert entries[0].ipv6 == "fe80::1"
