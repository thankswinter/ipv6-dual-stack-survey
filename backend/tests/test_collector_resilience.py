"""采集器 SSH 韧性测试 — 阶段重连、IPv6 重试。"""

from unittest.mock import MagicMock, patch

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

SAMPLE_IPV6 = """\
IPv6 Address                          MAC Address
FE80::1                               0011-2233-4401
"""


def _make_exec_stdout(data: bytes):
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


class TestCollectorResilience:
    @patch("app.collectors.base.paramiko.SSHClient")
    def test_reconnects_before_ipv6_after_arp(self, mock_ssh_cls):
        mock_client = MagicMock()
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh_cls.return_value = mock_client
        mock_client.get_transport.return_value = mock_transport

        mock_client.exec_command.side_effect = lambda cmd, **kwargs: _make_exec_stdout(
            SAMPLE_ARP.encode()
            if "arp" in cmd
            else SAMPLE_IPV6.encode()
            if "ipv6" in cmd
            else b"ok"
        )

        collector = _FakeCollector(
            host="192.168.1.1",
            username="admin",
            password="pass",
            vendor=Vendor.HUAWEI,
            model="S12700",
        )

        with patch.object(collector, "_reconnect_session") as mock_reconnect:
            cp, warnings = collector.collect_phased()

        mock_reconnect.assert_called_once()
        assert mock_reconnect.call_args.args[1] == "IPv6 采集前"
        assert cp.arp_output
        assert cp.ipv6_output
        assert "FE80::1" in cp.ipv6_output

    @patch("app.collectors.base.paramiko.SSHClient")
    def test_ipv6_retries_when_first_output_empty(self, mock_ssh_cls):
        mock_client = MagicMock()
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_ssh_cls.return_value = mock_client
        mock_client.get_transport.return_value = mock_transport

        ipv6_calls = {"count": 0}

        def exec_side_effect(cmd, **kwargs):
            if "ipv6" in cmd:
                ipv6_calls["count"] += 1
                data = SAMPLE_IPV6.encode() if ipv6_calls["count"] >= 2 else b""
                return _make_exec_stdout(data)
            if "arp" in cmd:
                return _make_exec_stdout(SAMPLE_ARP.encode())
            return _make_exec_stdout(b"ok")

        mock_client.exec_command.side_effect = exec_side_effect

        collector = _FakeCollector(
            host="192.168.1.1",
            username="admin",
            password="pass",
            vendor=Vendor.HUAWEI,
            model="S12700",
        )

        cp, _ = collector.collect_phased()
        assert ipv6_calls["count"] >= 2
        assert "FE80::1" in (cp.ipv6_output or "")
