"""交换机 CLI 输出解析公共工具。"""

from __future__ import annotations

import re

from app.core.algorithm import ArpEntry, Ipv6NeighborEntry, is_valid_ipv6, normalize_mac

# 华为/H3C 常见 MAC：0011-2233-4455 或 0011.2233.4455 或 aa:bb:cc:dd:ee:ff
MAC_TOKEN = re.compile(
    r"(?<![0-9a-fA-F])"
    r"(?:"
    r"(?:[0-9a-fA-F]{4}[-.]){2}[0-9a-fA-F]{4}|"
    r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}"
    r")"
    r"(?![0-9a-fA-F])",
    re.IGNORECASE,
)

# 行内 IPv6（含压缩写法）
IPV6_TOKEN = re.compile(
    r"(?<![0-9a-fA-F:])"
    r"("
    r"(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}|"
    r"(?:[0-9a-fA-F]{0,4}:){1,7}:|"
    r":(?:[0-9a-fA-F]{0,4}:){1,6}[0-9a-fA-F]{0,4}|"
    r"(?:[0-9a-fA-F]{0,4}:){1,6}:"
    r")"
    r"(?:%[^\s]+)?"
    r"(?![0-9a-fA-F:])",
    re.IGNORECASE,
)

IPV6_TABLE_LINE = re.compile(
    r"^([0-9a-fA-F:]+(?:%[^\s]+)?)\s+"
    r"((?:[0-9a-fA-F]{4}[-.]){2}[0-9a-fA-F]{4}|"
    r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})\s+"
    r"(\d+)\s+"
    r"(\S+)\s+"
    r"(\S+)",
    re.IGNORECASE,
)

IPV6_TABLE_LINE_ALT = re.compile(
    r"^([0-9a-fA-F:]+(?:%[^\s]+)?)\s+"
    r"((?:[0-9a-fA-F]{4}[-.]){2}[0-9a-fA-F]{4}|"
    r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})\s+"
    r"(\S+)",
    re.IGNORECASE,
)

# 华为 MAC 字段常见写法
MAC_VALUE = (
    r"((?:[0-9a-fA-F]{4}[-.]){2}[0-9a-fA-F]{4}|"
    r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})"
)

IPV6_VERBOSE_ADDR = re.compile(
    r"^IPv6 Address\s*:?\s+(\S+)",
    re.IGNORECASE,
)
# S12700 典型：Link-layer   : 001c-54ff-0902                     State : REACH
IPV6_VERBOSE_LINK_LAYER = re.compile(
    rf"^Link[- ]layer(?:\s+Addr(?:ess)?)?\s*:?\s*{MAC_VALUE}"
    rf"(?:\s+State\s*:?\s*(\S+))?",
    re.IGNORECASE,
)
IPV6_VERBOSE_MAC = re.compile(
    rf"^(?:MAC Address|Hardware Address)\s*:?\s*{MAC_VALUE}",
    re.IGNORECASE,
)
IPV6_VERBOSE_VLAN = re.compile(r"^VLAN\s*:?\s*(\d+)", re.IGNORECASE)
IPV6_VERBOSE_IF = re.compile(r"^Interface\s*:?\s*(\S+)", re.IGNORECASE)
IPV6_VERBOSE_STATE = re.compile(r"^State\s*:?\s*(\S+)", re.IGNORECASE)
IPV6_VERBOSE_IS_ROUTER = re.compile(
    r"^Is Router\s*:?\s*(TRUE|FALSE)",
    re.IGNORECASE,
)
IPV6_INLINE_IS_ROUTER = re.compile(
    r"Is Router\s*:?\s*(TRUE|FALSE)",
    re.IGNORECASE,
)

# 表格：IPv6 + Link-Layer Addr + Interface + State（无 VLAN）
IPV6_TABLE_4COL = re.compile(
    r"^(?:\d+\s+)?"
    r"([0-9a-fA-F:]+(?:%[^\s]+)?)\s+"
    r"((?:[0-9a-fA-F]{4}[-.]){2}[0-9a-fA-F]{4}|"
    r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})\s+"
    r"(\S+)\s+"
    r"(\S+)\s*$",
    re.IGNORECASE,
)


def is_ipv6_table_header(line: str) -> bool:
    """跳过表头行，不误伤 verbose 数据行。"""
    lower = line.lower()
    if "ipv6 address" not in lower:
        return False
    # verbose 数据行：IPv6 Address : FE80::1
    if re.search(r"ipv6 address\s*:?\s+[0-9a-fA-F:]", lower):
        return False
    if (
        "mac address" in lower
        or "link-layer" in lower
        or "link layer" in lower
        or "hardware address" in lower
    ):
        return True
    return False


def _strip_zone(ipv6: str) -> str:
    return ipv6.split("%", 1)[0].strip().lower()


def _build_ipv6_entry(data: dict[str, str | int | None]) -> Ipv6NeighborEntry | None:
    ipv6_raw = str(data.get("ipv6") or "")
    mac_raw = str(data.get("mac") or "")
    if not ipv6_raw or not mac_raw:
        return None
    ipv6 = _strip_zone(ipv6_raw)
    if not is_valid_ipv6(ipv6):
        return None
    try:
        mac = normalize_mac(mac_raw)
    except ValueError:
        return None
    vlan = data.get("vlan")
    return Ipv6NeighborEntry(
        ipv6=ipv6,
        mac=mac,
        vlan=int(vlan) if isinstance(vlan, int) or str(vlan).isdigit() else None,
        interface=str(data["interface"]) if data.get("interface") else None,
        state=str(data["state"]) if data.get("state") else None,
        is_router=bool(data["is_router"]) if data.get("is_router") is not None else None,
    )


def _flush_pending(pending: dict[str, str | int | None]) -> Ipv6NeighborEntry | None:
    entry = _build_ipv6_entry(pending)
    pending.clear()
    return entry


def _parse_ipv6_table_line(line: str) -> Ipv6NeighborEntry | None:
    match = (
        IPV6_TABLE_LINE.match(line)
        or IPV6_TABLE_4COL.match(line)
        or IPV6_TABLE_LINE_ALT.match(line)
    )
    if not match:
        return None
    ipv6 = _strip_zone(match.group(1))
    mac_raw = match.group(2)
    if not is_valid_ipv6(ipv6):
        return None
    try:
        mac = normalize_mac(mac_raw)
    except ValueError:
        return None
    if match.re is IPV6_TABLE_4COL:
        return Ipv6NeighborEntry(
            ipv6=ipv6,
            mac=mac,
            interface=match.group(3),
            state=match.group(4),
        )
    if len(match.groups()) >= 5:
        vlan = int(match.group(3)) if match.group(3).isdigit() else None
        interface = match.group(4)
        state = match.group(5)
    else:
        vlan = None
        interface = match.group(3)
        state = None
    return Ipv6NeighborEntry(
        ipv6=ipv6,
        mac=mac,
        vlan=vlan,
        interface=interface,
        state=state,
    )


def _parse_ipv6_loose_line(line: str) -> Ipv6NeighborEntry | None:
    """兜底：从一行里提取 IPv6 + MAC。"""
    mac_match = MAC_TOKEN.search(line)
    ipv6_match = IPV6_TOKEN.search(line)
    if not mac_match or not ipv6_match:
        return None
    ipv6 = _strip_zone(ipv6_match.group(1))
    if not is_valid_ipv6(ipv6):
        return None
    try:
        mac = normalize_mac(mac_match.group(0))
    except ValueError:
        return None
    return Ipv6NeighborEntry(ipv6=ipv6, mac=mac)


def parse_ipv6_neighbor_output(output: str) -> list[Ipv6NeighborEntry]:
    """解析华为/H3C display ipv6 neighbors 的 table 与 verbose 输出。"""
    entries: list[Ipv6NeighborEntry] = []
    pending: dict[str, str | int | None] = {}

    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("-") or line.lower().startswith("total"):
            continue
        if is_ipv6_table_header(line):
            continue

        addr_match = IPV6_VERBOSE_ADDR.match(line)
        if addr_match:
            flushed = _flush_pending(pending)
            if flushed:
                entries.append(flushed)
            pending["ipv6"] = addr_match.group(1).strip()
            continue

        link_match = IPV6_VERBOSE_LINK_LAYER.match(line)
        if link_match and pending.get("ipv6"):
            pending["mac"] = link_match.group(1).strip()
            if link_match.group(2):
                pending["state"] = link_match.group(2).strip()
            continue

        mac_match = IPV6_VERBOSE_MAC.match(line)
        if mac_match and pending.get("ipv6"):
            pending["mac"] = mac_match.group(1).strip()
            continue

        vlan_match = IPV6_VERBOSE_VLAN.match(line)
        if vlan_match and pending:
            pending["vlan"] = int(vlan_match.group(1))
            continue

        if_match = IPV6_VERBOSE_IF.match(line)
        if if_match and pending:
            pending["interface"] = if_match.group(1).strip()
            continue

        state_match = IPV6_VERBOSE_STATE.match(line)
        if state_match and pending:
            pending["state"] = state_match.group(1).strip()
            continue

        router_match = IPV6_VERBOSE_IS_ROUTER.match(line)
        if router_match and pending:
            pending["is_router"] = router_match.group(1).upper() == "TRUE"
            continue

        inline_router = IPV6_INLINE_IS_ROUTER.search(line)
        if inline_router and pending:
            pending["is_router"] = inline_router.group(1).upper() == "TRUE"
            continue

        table_entry = _parse_ipv6_table_line(line)
        if table_entry:
            flushed = _flush_pending(pending)
            if flushed:
                entries.append(flushed)
            entries.append(table_entry)
            continue

        loose_entry = _parse_ipv6_loose_line(line)
        if loose_entry:
            flushed = _flush_pending(pending)
            if flushed:
                entries.append(flushed)
            entries.append(loose_entry)

    flushed = _flush_pending(pending)
    if flushed:
        entries.append(flushed)

    return entries


def sample_unparsed_ipv6_lines(output: str, limit: int = 3) -> list[str]:
    """采集成功但解析失败时，抽取样例行便于排查。"""
    samples: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("-") or line.lower().startswith("total"):
            continue
        if is_ipv6_table_header(line):
            continue
        if (
            IPV6_VERBOSE_ADDR.match(line)
            or IPV6_VERBOSE_LINK_LAYER.match(line)
            or IPV6_VERBOSE_MAC.match(line)
            or _parse_ipv6_table_line(line)
            or _parse_ipv6_loose_line(line)
        ):
            continue
        samples.append(line[:160])
        if len(samples) >= limit:
            break
    return samples


def parse_arp_table_line(
    line: str,
    *,
    full_pattern: re.Pattern[str],
    alt_pattern: re.Pattern[str],
) -> ArpEntry | None:
    match = full_pattern.match(line) or alt_pattern.match(line)
    if not match:
        return None
    ipv4 = match.group(1)
    mac_raw = match.group(2)
    try:
        mac = normalize_mac(mac_raw)
    except ValueError:
        return None
    if len(match.groups()) >= 4:
        vlan = int(match.group(3)) if match.group(3).isdigit() else None
        interface = match.group(4)
    else:
        vlan = None
        interface = match.group(3)
    return ArpEntry(ipv4=ipv4, mac=mac, vlan=vlan, interface=interface)
