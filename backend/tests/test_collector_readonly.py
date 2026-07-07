"""采集器只读行为测试 — 验证 exec / shell 模式。"""

from unittest.mock import MagicMock, patch

import pytest

from app.cli.readonly import ReadOnlyViolationError
from app.collectors.base import SwitchCollector
from app.core.models import Vendor


class _FakeCollector(SwitchCollector):
    def parse_arp(self, output: str, on_progress=None):
        return []

    def parse_ipv6_neighbors(self, output: str, on_progress=None):
        return []


SAMPLE_ARP = """\
IP Address      MAC Address    VLAN ID  Interface
10.0.0.1        0011-2233-4401 10       GE1/0/1
"""


class TestCollectorReadOnly:
    @patch("app.collectors.base.paramiko.SSHClient")
    def test_exec_mode_runs_readonly_commands(self, mock_ssh_cls):
        mock_client = MagicMock()
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh_cls.return_value = mock_client
        mock_client.get_transport.return_value = mock_transport

        def make_stdout(data: bytes):
            mock_stdout = MagicMock()
            mock_stderr = MagicMock()
            mock_channel = MagicMock()
            mock_stdout.channel = mock_channel
            mock_stderr.read.return_value = b""
            mock_channel.recv_exit_status.return_value = 0
            mock_channel.exit_status_ready.side_effect = [False, True]
            mock_channel.recv_ready.side_effect = [True, False]
            mock_channel.recv.return_value = data
            return None, mock_stdout, mock_stderr

        def exec_side_effect(cmd, **kwargs):
            if "arp" in cmd:
                data = SAMPLE_ARP.encode()
            elif "ipv6" in cmd:
                data = b"fe80::1 0011-2233-4401"
            else:
                data = b"ok"
            return make_stdout(data)

        mock_client.exec_command.side_effect = exec_side_effect

        collector = _FakeCollector(
            host="192.168.1.1",
            username="admin",
            password="pass",
            vendor=Vendor.HUAWEI,
            model="S12700",
        )
        cp, _ = collector.collect_phased()

        commands = [call.args[0] for call in mock_client.exec_command.call_args_list]
        assert "screen-length 0 temporary" in commands
        assert "display arp all" in commands
        assert "display ipv6 neighbors" in commands
        assert cp.arp_output

    def test_exec_rejects_write_operations(self):
        collector = _FakeCollector(
            host="192.168.1.1",
            username="admin",
            password="pass",
            vendor=Vendor.HUAWEI,
            model="S12700",
        )
        collector._client = MagicMock()

        with pytest.raises(ReadOnlyViolationError):
            collector._exec_command("save", timeout=30)

        collector._client.exec_command.assert_not_called()
