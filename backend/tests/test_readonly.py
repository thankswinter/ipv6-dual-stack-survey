"""只读 CLI 安全校验测试。"""

import pytest

from app.cli.readonly import (
    ALLOWED_COMMANDS,
    ReadOnlyViolationError,
    assert_readonly_command,
    is_readonly_command,
    list_allowed_commands,
)
from app.cli.templates import get_cli_template
from app.core.models import Vendor


class TestReadOnlyGuard:
    def test_all_template_commands_are_allowed(self):
        for vendor in Vendor:
            for model in ["S12700", "S5735", "S12500", "S6520X"]:
                tpl = get_cli_template(vendor, model)
                for cmd in (
                    tpl.arp_command,
                    tpl.ipv6_neighbor_command,
                    tpl.disable_paging_command,
                ):
                    assert is_readonly_command(cmd), f"模板命令未通过只读校验: {cmd}"

    def test_allowed_commands_whitelist_non_empty(self):
        assert len(ALLOWED_COMMANDS) >= 6
        assert "display arp" in ALLOWED_COMMANDS
        assert "display arp all" in ALLOWED_COMMANDS
        assert "display ipv6 neighbors" in ALLOWED_COMMANDS

    def test_blocks_config_commands(self):
        dangerous = [
            "system-view",
            "configure terminal",
            "interface GigabitEthernet1/0/1",
            "undo vlan 10",
            "delete vlan 10",
            "save",
            "write memory",
            "commit",
            "reset saved-configuration",
            "shutdown",
            "vlan 100",
            "ip route-static 0.0.0.0 0.0.0.0 1.1.1.1",
            "quit",
        ]
        for cmd in dangerous:
            with pytest.raises(ReadOnlyViolationError):
                assert_readonly_command(cmd)

    def test_blocks_display_followed_by_config(self):
        with pytest.raises(ReadOnlyViolationError):
            assert_readonly_command("display current-configuration")

    def test_list_allowed_commands_sorted(self):
        cmds = list_allowed_commands()
        assert cmds == sorted(cmds)
