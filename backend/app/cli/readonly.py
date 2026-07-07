"""交换机 CLI 只读安全校验 — 禁止任何可能修改配置的命令。"""

from __future__ import annotations

from app.cli.templates import (
    H3C_COMWARE7,
    H3C_DEFAULT,
    HUAWEI_DEFAULT,
    HUAWEI_S_SWITCH,
    CliTemplate,
)

# 会话级分页设置，不写入 startup-config
READONLY_PAGING_COMMANDS = frozenset(
    {
        "screen-length 0 temporary",  # 华为 / 新版 H3C
        "screen-length disable",      # H3C 用户视图，仅当前会话生效
    }
)

_ALL_TEMPLATES: tuple[CliTemplate, ...] = (
    HUAWEI_DEFAULT,
    HUAWEI_S_SWITCH,
    H3C_DEFAULT,
    H3C_COMWARE7,
)

# 从全部 CLI 模板推导允许的命令白名单
ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        cmd
        for tpl in _ALL_TEMPLATES
        for cmd in (
            tpl.arp_command,
            tpl.ipv6_neighbor_command,
            tpl.disable_paging_command,
        )
    }
)


class ReadOnlyViolationError(RuntimeError):
    """尝试发送非只读 CLI 命令时抛出。"""


def is_readonly_command(command: str) -> bool:
    normalized = " ".join(command.strip().split())
    if normalized in ALLOWED_COMMANDS:
        return True
    if normalized in READONLY_PAGING_COMMANDS:
        return True
    return False


def assert_readonly_command(command: str) -> str:
    """校验命令为只读；通过则返回规范化后的命令字符串。"""
    normalized = " ".join(command.strip().split())
    if not is_readonly_command(normalized):
        raise ReadOnlyViolationError(
            f"拒绝执行可能修改交换机配置的命令: {command!r}。"
            "本系统仅允许 display 查询与会话级 screen-length 设置。"
        )
    return normalized


def list_allowed_commands() -> list[str]:
    return sorted(ALLOWED_COMMANDS)
