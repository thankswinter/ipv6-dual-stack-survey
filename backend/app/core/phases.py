from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CollectCheckpoint:
    """采集检查点 — 用于中断后恢复。"""

    arp_output: str | None = None
    ipv6_output: str | None = None
    completed_steps: set[str] = field(default_factory=set)

    def step_done(self, step: str) -> bool:
        return step in self.completed_steps

    def mark_step(self, step: str) -> None:
        self.completed_steps.add(step)


COLLECT_STEPS: tuple[tuple[str, str, int], ...] = (
    ("connect", "正在连接 SSH...", 8),
    ("paging", "设置会话分页...", 15),
    ("arp", "采集 ARP 表...", 40),
    ("ipv6", "采集 IPv6 邻居表...", 65),
    ("parse_arp", "解析 ARP 数据...", 75),
    ("parse_ipv6", "解析 IPv6 数据...", 85),
    ("analyze", "统计双栈设备...", 95),
    ("done", "采集完成", 100),
)

STEP_ORDER = [s[0] for s in COLLECT_STEPS]
PROGRESS_BY_STEP = {s[0]: s[2] for s in COLLECT_STEPS}
LABEL_BY_STEP = {s[0]: s[1] for s in COLLECT_STEPS}


class CollectionCancelledError(Exception):
    """用户请求中断采集。"""

    pass
