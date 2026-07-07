from __future__ import annotations

import re

from collections.abc import Callable

from app.collectors.base import SwitchCollector
from app.collectors.parse_utils import parse_arp_table_line, parse_ipv6_neighbor_output
from app.core.algorithm import ArpEntry, Ipv6NeighborEntry
from app.core.scale import PARSE_PROGRESS_LINES
from app.core.models import Vendor


class HuaweiCollector(SwitchCollector):
    """
    解析华为 display arp / display ipv6 neighbors 输出。

    支持：
    - 表格行：FE80::1  0011-2233-4455  10  GE1/0/1  REACH
    - verbose 块：
        IPv6 Address : FE80::219:E0FF:FE59:4C0F
        MAC Address  : 0019-E0E6-4C0F
    """

    ARP_LINE = re.compile(
        r"^(\d+\.\d+\.\d+\.\d+)\s+"
        r"((?:[0-9a-fA-F]{4}[-.]){2}[0-9a-fA-F]{4}|"
        r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})\s+"
        r"(\d+)\s+"
        r"(\S+)\s+"
        r"(\S+)",
        re.IGNORECASE,
    )

    ARP_LINE_ALT = re.compile(
        r"^(\d+\.\d+\.\d+\.\d+)\s+"
        r"((?:[0-9a-fA-F]{4}[-.]){2}[0-9a-fA-F]{4}|"
        r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})\s+"
        r"(\S+)",
        re.IGNORECASE,
    )

    def parse_arp(
        self,
        output: str,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> list[ArpEntry]:
        entries: list[ArpEntry] = []
        lines = output.splitlines()
        total = len(lines)
        for i, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("-") or (
                "IP Address" in line and "MAC Address" in line
            ):
                continue

            entry = parse_arp_table_line(
                line, full_pattern=self.ARP_LINE, alt_pattern=self.ARP_LINE_ALT
            )
            if not entry:
                continue
            entries.append(entry)
            if on_progress and i % PARSE_PROGRESS_LINES == 0:
                on_progress(i, total, len(entries))

        if on_progress and total:
            on_progress(total, total, len(entries))
        return entries

    def parse_ipv6_neighbors(
        self,
        output: str,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> list[Ipv6NeighborEntry]:
        entries = parse_ipv6_neighbor_output(output)
        if on_progress:
            total = len(output.splitlines())
            on_progress(total, total, len(entries))
        return entries
