from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

import paramiko
from paramiko.ssh_exception import SSHException

from app.cli.templates import CliTemplate, get_cli_template
from app.cli.readonly import assert_readonly_command
from app.core.algorithm import ArpEntry, Ipv6NeighborEntry, analyze_dual_stack
from app.core.models import DeviceRecord, SurveyStatistics, Vendor
from app.core.phases import CollectionCancelledError, CollectCheckpoint
from app.core.scale import (
    ARP_COMMAND_MAX_SECONDS,
    COMMAND_IDLE_TIMEOUT,
    IPV6_COMMAND_MAX_SECONDS,
    LOGIN_IDLE_TIMEOUT,
    LOGIN_MAX_SECONDS,
    MORE_PAGE_LIMIT,
    READ_PROGRESS_BYTES,
    TARGET_ARP_ENTRIES,
)

MORE_PATTERNS = ("---- More ----", "--More--", "-- More --", "Press CTRL+C to break")

# 交互式 shell 中可能出现的二次认证/免责声明提示（非 CLI prompt）
INTERACTIVE_HINTS = (
    "User Authentication",
    "Press Y",
    "Press 'Y'",
    "please press",
    "Username:",
    "Password:",
    "continue?",
)


class SSHSessionError(ConnectionError):
    """SSH 会话异常，含用户可读的排查建议。"""


class SwitchCollector(ABC):
    """交换机采集器 — 优先 exec 非交互模式；不支持时自动回退 shell + 二次认证。"""

    # 老版本华为设备常见算法组合
    _LEGACY_KEX = (
        "diffie-hellman-group-exchange-sha256",
        "diffie-hellman-group-exchange-sha1",
        "diffie-hellman-group14-sha256",
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group1-sha1",
    )
    _LEGACY_KEYS = ("ssh-rsa", "rsa-sha2-256", "rsa-sha2-512")
    _LEGACY_CIPHERS = (
        "aes128-ctr",
        "aes192-ctr",
        "aes256-ctr",
        "aes128-cbc",
        "3des-cbc",
    )

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        vendor: Vendor,
        model: str,
        port: int = 22,
        timeout: int = 30,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.vendor = vendor
        self.model = model
        self.port = port
        self.timeout = timeout
        self.cli = get_cli_template(vendor, model)
        self._client: paramiko.SSHClient | None = None
        self._channel: paramiko.Channel | None = None
        self._use_exec = True
        self._force_shell = False
        self._paging_applied = False
        self._metrics = {"more_pages_sent": 0, "arp_bytes": 0, "ipv6_bytes": 0}

    def _session_alive(self) -> bool:
        if not self._transport_active():
            return False
        if not self._use_exec:
            return self._channel is not None and not self._channel.closed
        return True

    @staticmethod
    def _wrap_ssh_error(exc: Exception, phase: str) -> SSHSessionError:
        if isinstance(exc, SSHSessionError):
            return exc
        msg = str(exc).lower()
        if "closed" in msg or "reset" in msg or "broken pipe" in msg:
            hint = (
                "交换机或网络侧关闭了 SSH 连接。"
                "若设备有二次认证/免责声明页面，请确认账号支持非交互命令行。"
            )
            return SSHSessionError(f"{phase}：SSH 连接中断（{exc}）。{hint}")
        if "timed out" in msg or "timeout" in msg:
            return SSHSessionError(
                f"{phase}：SSH 操作超时（{exc}）。"
                "大规模 ARP 建议超时 300~900 秒。"
            )
        if "session not active" in msg:
            return SSHSessionError(
                f"{phase}：SSH 会话已失效（{exc}）。"
                "该设备 exec 通道执行后会断开，已尝试 shell 模式。"
            )
        return SSHSessionError(f"{phase}：{exc}")

    def _transport_active(self) -> bool:
        if not self._client:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def _check_cancel(self, should_cancel: Callable[[], bool] | None) -> None:
        if should_cancel and should_cancel():
            raise CollectionCancelledError()

    def _apply_legacy_algorithms(self, transport: paramiko.Transport) -> None:
        opts = transport.get_security_options()
        opts.kex = self._LEGACY_KEX
        opts.key_types = self._LEGACY_KEYS
        opts.ciphers = self._LEGACY_CIPHERS

    def _open_client(
        self,
        on_debug: Callable[[str, str], None] | None = None,
    ) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.timeout,
                banner_timeout=max(self.timeout, 60),
                auth_timeout=max(self.timeout, 60),
                look_for_keys=False,
                allow_agent=False,
            )
        except (OSError, SSHException, EOFError) as exc:
            if on_debug:
                on_debug(f"标准 SSH 握手失败，尝试兼容算法: {exc}", "warn")
            client.close()
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            transport = paramiko.Transport((self.host, self.port))
            transport.set_keepalive(15)
            transport.banner_timeout = max(self.timeout, 60)
            transport.auth_timeout = max(self.timeout, 60)
            self._apply_legacy_algorithms(transport)
            transport.connect(
                username=self.username,
                password=self.password,
            )
            client._transport = transport
        else:
            transport = client.get_transport()
            if transport:
                transport.set_keepalive(15)
        return client

    def _probe_exec(
        self,
        on_debug: Callable[[str, str], None] | None = None,
    ) -> bool:
        if not self._client:
            return False
        try:
            self._exec_command(
                self.cli.disable_paging_command,
                timeout=max(self.timeout, 60),
                on_debug=on_debug,
            )
        except SSHSessionError as exc:
            if on_debug:
                on_debug(f"exec 通道不可用: {exc}", "warn")
            return False

        if not self._transport_active():
            if on_debug:
                on_debug(
                    "exec 执行后会话被交换机关闭，改用 shell 模式",
                    "warn",
                )
            return False

        self._paging_applied = True
        return True

    def _login_shell(
        self,
        on_debug: Callable[[str, str], None] | None = None,
    ) -> None:
        if not self._client:
            raise SSHSessionError("SSH 客户端未初始化")

        if on_debug:
            on_debug("打开交互式 shell…", "debug")

        try:
            channel = self._client.invoke_shell(
                term="vt100",
                width=200,
                height=50,
            )
            channel.settimeout(5.0)
            self._channel = channel
            time.sleep(0.3)
            channel.send("\n")
        except (OSError, SSHException, EOFError) as exc:
            raise self._wrap_ssh_error(exc, "打开 shell") from exc

        buffer = ""
        deadline = time.time() + LOGIN_MAX_SECONDS
        last_data_at = time.time()
        password_sent = 0
        y_sent = False

        while time.time() < deadline:
            if not self._channel or self._channel.closed:
                raise self._wrap_ssh_error(OSError("Socket is closed"), "登录")

            if self._channel.recv_ready():
                chunk = self._channel.recv(65535).decode("utf-8", errors="replace")
                if not chunk:
                    raise EOFError("SSH 连接已关闭")
                buffer += chunk
                last_data_at = time.time()

                if on_debug and len(buffer) < 500:
                    preview = buffer.replace("\r", "").splitlines()[-1][:80]
                    if preview.strip():
                        on_debug(f"登录输出: {preview!r}", "debug")

                lower = buffer.lower()
                if not y_sent and ("press 'y'" in lower or "press y" in lower):
                    self._channel.send("Y\n")
                    y_sent = True
                    buffer = ""
                    continue

                tail = buffer.rstrip()
                if password_sent < 3 and tail.endswith("Password:"):
                    self._channel.send(self.password + "\n")
                    password_sent += 1
                    if on_debug:
                        on_debug("已响应二次密码认证", "debug")
                    buffer = ""
                    continue

                if self._has_prompt(buffer):
                    if on_debug:
                        on_debug("Shell 登录完成", "info")
                    return
            else:
                if buffer and (time.time() - last_data_at) >= LOGIN_IDLE_TIMEOUT:
                    if self._has_prompt(buffer):
                        return
                    tail_hint = buffer.splitlines()[-1][-80:] if buffer else ""
                    raise SSHSessionError(
                        f"登录：等待设备响应超时（{LOGIN_IDLE_TIMEOUT}s 无新数据）。"
                        f"末行: {tail_hint!r}"
                    )
                time.sleep(0.05)

        tail_hint = buffer.splitlines()[-1][-80:] if buffer else ""
        raise SSHSessionError(
            f"登录：{LOGIN_MAX_SECONDS}s 内未完成 shell 登录。末行: {tail_hint!r}"
        )

    def connect(
        self,
        on_debug: Callable[[str, str], None] | None = None,
        *,
        prefer_shell: bool = False,
    ) -> None:
        if on_debug:
            on_debug(f"正在连接 {self.host}:{self.port}…", "debug")
        try:
            if prefer_shell or self._force_shell:
                self._client = self._open_client(on_debug)
                self._login_shell(on_debug)
                self._use_exec = False
                self._force_shell = True
                if on_debug:
                    on_debug("SSH shell 模式连接成功", "info")
                return

            self._client = self._open_client(on_debug)
            if self._probe_exec(on_debug):
                self._use_exec = True
                if on_debug:
                    on_debug("SSH exec 模式连接成功", "info")
                return

            if on_debug:
                on_debug("exec 不可用，切换 shell 模式", "warn")
            self.disconnect()
            self._client = self._open_client(on_debug)
            self._login_shell(on_debug)
            self._use_exec = False
            self._force_shell = True
            if on_debug:
                on_debug("SSH shell 模式连接成功", "info")
        except SSHSessionError:
            raise
        except (OSError, SSHException, EOFError) as exc:
            raise self._wrap_ssh_error(exc, "SSH 连接") from exc

    def _apply_paging(
        self,
        on_debug: Callable[[str, str], None] | None = None,
    ) -> None:
        if self._paging_applied:
            return
        self._execute_readonly(
            self.cli.disable_paging_command,
            timeout=max(self.timeout, 60),
            on_debug=on_debug,
        )
        self._paging_applied = True

    def _reconnect_session(
        self,
        on_debug: Callable[[str, str], None] | None = None,
        reason: str = "重建连接",
    ) -> None:
        if on_debug:
            on_debug(f"{reason}：重新建立 SSH 连接", "info")
        prefer_shell = self._force_shell or not self._use_exec
        self.disconnect()
        self.connect(on_debug, prefer_shell=prefer_shell)
        self._apply_paging(on_debug)

    def _ensure_session(
        self,
        on_debug: Callable[[str, str], None] | None = None,
        reason: str = "继续采集",
    ) -> None:
        if self._session_alive():
            return
        self._reconnect_session(on_debug, reason)

    def _execute_readonly_resilient(
        self,
        command: str,
        *,
        phase: str,
        timeout: int,
        on_debug: Callable[[str, str], None] | None = None,
        metric_key: str | None = None,
        on_metrics: Callable[[dict[str, int]], None] | None = None,
        retry_on_empty: bool = False,
    ) -> str:
        self._ensure_session(on_debug, phase)
        try:
            output = self._execute_readonly(
                command,
                timeout=timeout,
                on_debug=on_debug,
                metric_key=metric_key,
                on_metrics=on_metrics,
            )
        except SSHSessionError as exc:
            if on_debug:
                on_debug(f"{phase} 失败，重连后重试: {exc}", "warn")
            self._reconnect_session(on_debug, phase)
            output = self._execute_readonly(
                command,
                timeout=timeout,
                on_debug=on_debug,
                metric_key=metric_key,
                on_metrics=on_metrics,
            )

        if retry_on_empty and not output.strip():
            if on_debug:
                on_debug(f"{phase} 输出为空，重连后重试", "warn")
            self._reconnect_session(on_debug, f"{phase} 重试")
            output = self._execute_readonly(
                command,
                timeout=timeout,
                on_debug=on_debug,
                metric_key=metric_key,
                on_metrics=on_metrics,
            )
        return output

    def disconnect(self) -> None:
        try:
            if self._channel and not self._channel.closed:
                self._channel.close()
        except OSError:
            pass
        try:
            if self._client:
                self._client.close()
        except OSError:
            pass
        self._channel = None
        self._client = None
        self._paging_applied = False

    def _exec_command(
        self,
        command: str,
        *,
        timeout: int,
        on_debug: Callable[[str, str], None] | None = None,
        metric_key: str | None = None,
        on_metrics: Callable[[dict[str, int]], None] | None = None,
    ) -> str:
        if not self._client:
            raise SSHSessionError("SSH 客户端未初始化")
        if not self._transport_active():
            raise SSHSessionError("SSH session not active")

        safe_command = assert_readonly_command(command)
        if on_debug:
            on_debug(f"exec → {safe_command}", "debug")

        try:
            _, stdout, stderr = self._client.exec_command(
                safe_command,
                timeout=timeout,
            )
            channel = stdout.channel
            channel.settimeout(timeout)

            chunks: list[bytes] = []
            deadline = time.time() + timeout
            last_report = 0

            while not channel.exit_status_ready():
                if time.time() > deadline:
                    raise SSHSessionError(
                        f"执行 {safe_command}：超时（{timeout}s）"
                    )
                if channel.recv_ready():
                    data = channel.recv(65535)
                    if data:
                        chunks.append(data)
                        if metric_key:
                            size = sum(len(c) for c in chunks)
                            self._metrics[metric_key] = size
                            if on_metrics:
                                on_metrics({metric_key: size})
                        if on_debug:
                            size = sum(len(c) for c in chunks)
                            bucket = size // READ_PROGRESS_BYTES
                            if bucket > last_report:
                                last_report = bucket
                                on_debug(
                                    f"{safe_command}：已接收 {size // 1024} KB",
                                    "debug",
                                )
                else:
                    time.sleep(0.05)

            while channel.recv_ready():
                chunks.append(channel.recv(65535))

            raw = b"".join(chunks).decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace").strip()
            exit_code = channel.recv_exit_status()

            if err and on_debug:
                on_debug(f"stderr: {err[:300]}", "warn")

            if exit_code != 0 and not raw.strip():
                raise SSHSessionError(
                    f"执行 {safe_command} 失败（exit={exit_code}）: {err or '无输出'}"
                )

            return self._clean_command_output(safe_command, raw)
        except SSHSessionError:
            raise
        except (OSError, SSHException, EOFError) as exc:
            raise self._wrap_ssh_error(exc, f"执行 {safe_command}") from exc

    def _clean_command_output(self, command: str, buffer: str) -> str:
        lines = buffer.splitlines()
        cleaned: list[str] = []
        for line in lines:
            if line.strip() == command.strip():
                continue
            if any(p in line for p in self.cli.prompt_patterns) and len(line) < 80:
                continue
            if any(marker in line for marker in MORE_PATTERNS):
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    def _execute_readonly(
        self,
        command: str,
        *,
        timeout: int,
        on_debug: Callable[[str, str], None] | None = None,
        metric_key: str | None = None,
        on_metrics: Callable[[dict[str, int]], None] | None = None,
    ) -> str:
        if self._use_exec:
            return self._exec_command(
                command,
                timeout=timeout,
                on_debug=on_debug,
                metric_key=metric_key,
                on_metrics=on_metrics,
            )
        return self._send_shell_command(
            command,
            on_debug=on_debug,
            max_seconds=timeout,
            metric_key=metric_key,
            on_metrics=on_metrics,
        )

    def collect_phased(
        self,
        *,
        checkpoint: CollectCheckpoint | None = None,
        on_debug: Callable[[str, str], None] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        on_metrics: Callable[[dict[str, int]], None] | None = None,
    ) -> tuple[CollectCheckpoint, list[str]]:
        cp = checkpoint or CollectCheckpoint()
        warnings: list[str] = []

        def dbg(msg: str, level: str = "debug") -> None:
            if on_debug:
                on_debug(msg, level)

        def prog(step: str, msg: str) -> None:
            if on_progress:
                on_progress(step, msg)

        try:
            self._check_cancel(should_cancel)
            prog("connect", "正在连接 SSH...")
            self.connect(on_debug=dbg)
            cp.mark_step("connect")

            if not cp.step_done("paging") and not self._paging_applied:
                self._check_cancel(should_cancel)
                prog("paging", f"执行: {self.cli.disable_paging_command}")
                self._execute_readonly(
                    self.cli.disable_paging_command,
                    timeout=max(self.timeout, 60),
                    on_debug=dbg,
                )
                cp.mark_step("paging")
                dbg("会话分页已设置", "info")
            elif self._paging_applied:
                cp.mark_step("paging")
                prog("paging", "跳过（connect 阶段已设置分页）")

            if cp.arp_output is None:
                self._check_cancel(should_cancel)
                prog("arp", f"执行: {self.cli.arp_command}")
                cp.arp_output = self._execute_readonly_resilient(
                    self.cli.arp_command,
                    phase="ARP 采集",
                    timeout=ARP_COMMAND_MAX_SECONDS,
                    on_debug=dbg,
                    metric_key="arp_bytes",
                    on_metrics=on_metrics,
                )
                dbg(
                    f"ARP 采集完成：{len(cp.arp_output)} 字符"
                    f"（设计容量 {TARGET_ARP_ENTRIES} 条）",
                    "info",
                )
            else:
                prog("arp", "跳过 ARP 采集（使用检查点数据）")
            cp.mark_step("arp")

            if cp.ipv6_output is None:
                self._check_cancel(should_cancel)
                # 长 ARP 采集后 SSH 常会超时断开，IPv6 前主动重建连接
                if cp.arp_output:
                    self._reconnect_session(dbg, "IPv6 采集前")
                prog("ipv6", f"执行: {self.cli.ipv6_neighbor_command}")
                cp.ipv6_output = self._execute_readonly_resilient(
                    self.cli.ipv6_neighbor_command,
                    phase="IPv6 采集",
                    timeout=IPV6_COMMAND_MAX_SECONDS,
                    on_debug=dbg,
                    metric_key="ipv6_bytes",
                    on_metrics=on_metrics,
                    retry_on_empty=True,
                )
                dbg(
                    f"IPv6 采集完成：{len(cp.ipv6_output)} 字符",
                    "info",
                )
                if not cp.ipv6_output.strip():
                    warnings.append(
                        "IPv6 邻居表采集结果为空，可能 SSH 中断或设备未启用 IPv6"
                    )
            else:
                prog("ipv6", "跳过 IPv6 采集（使用检查点数据）")
            cp.mark_step("ipv6")
        finally:
            self.disconnect()
            dbg("SSH 连接已关闭", "debug")

        return cp, warnings

    def collect(self) -> tuple[list[ArpEntry], list[Ipv6NeighborEntry], list[str]]:
        cp, warnings = self.collect_phased()
        arp_entries = self.parse_arp(cp.arp_output or "")
        ipv6_entries = self.parse_ipv6_neighbors(cp.ipv6_output or "")
        return arp_entries, ipv6_entries, warnings

    def survey(self) -> tuple[SurveyStatistics, list[DeviceRecord], list[str], int, int]:
        arp_entries, ipv6_entries, collect_warnings = self.collect()
        stats, records, algo_warnings = analyze_dual_stack(arp_entries, ipv6_entries)
        all_warnings = collect_warnings + algo_warnings
        return stats, records, all_warnings, len(arp_entries), len(ipv6_entries)

    # ---- 交互式 shell 备用（部分老设备可能需要） ----

    def _has_prompt(self, text: str) -> bool:
        lines = text.rstrip().splitlines()
        if not lines:
            return False
        last = lines[-1].strip()
        tail = text[-200:]

        for hint in INTERACTIVE_HINTS:
            if hint.lower() in text.lower():
                return False

        for pattern in self.cli.prompt_patterns:
            if pattern in last or pattern in tail:
                return True
        if last.startswith("<") and last.endswith(">"):
            return True
        if last.startswith("[") and (last.endswith("]") or last.endswith(">")):
            return True
        if (last.endswith(">") or last.endswith("#")) and len(last) < 120:
            if not last.startswith("-") and "----" not in last:
                return True
        return False

    def _needs_more(self, text: str) -> bool:
        tail = text[-80:]
        return any(marker in tail for marker in MORE_PATTERNS)

    def _read_until_prompt(
        self,
        *,
        phase: str = "读取输出",
        command: str | None = None,
        max_seconds: int | None = None,
        idle_timeout: int | None = None,
        on_debug: Callable[[str, str], None] | None = None,
        metric_key: str | None = None,
        on_metrics: Callable[[dict[str, int]], None] | None = None,
    ) -> str:
        if not self._channel:
            raise SSHSessionError("SSH channel 未初始化")

        limit = max_seconds or self.timeout
        idle_limit = idle_timeout or COMMAND_IDLE_TIMEOUT
        deadline = time.time() + limit
        last_data_at = time.time()
        buffer = ""
        more_sent = 0
        got_data = False

        while time.time() < deadline:
            if not self._transport_active():
                raise self._wrap_ssh_error(OSError("Socket is closed"), phase)
            if self._channel.closed:
                raise self._wrap_ssh_error(OSError("Socket is closed"), phase)

            if self._channel.recv_ready():
                chunk = self._channel.recv(65535).decode("utf-8", errors="replace")
                if not chunk:
                    raise EOFError("SSH 连接已关闭")
                buffer += chunk
                last_data_at = time.time()
                got_data = True

                for hint in INTERACTIVE_HINTS:
                    if hint.lower() in buffer.lower():
                        raise SSHSessionError(
                            f"{phase}：检测到交互式认证/免责声明页面（{hint}）。"
                            "当前账号可能不支持自动化采集，请使用具备 exec 权限的网络账号。"
                        )

                if self._needs_more(buffer) and more_sent < MORE_PAGE_LIMIT:
                    self._channel.send(" ")
                    more_sent += 1
                    self._metrics["more_pages_sent"] = more_sent
                    if on_metrics:
                        on_metrics({"more_pages_sent": more_sent})
                    time.sleep(0.02)
                    continue

                if self._has_prompt(buffer):
                    break
            else:
                if got_data and (time.time() - last_data_at) >= idle_limit:
                    if self._has_prompt(buffer):
                        break
                    tail_hint = buffer.splitlines()[-1][-60:] if buffer else ""
                    raise SSHSessionError(
                        f"{phase}：等待超时（{idle_limit}s）。末行: {tail_hint!r}"
                    )
                time.sleep(0.05)

        if not self._has_prompt(buffer):
            tail_hint = buffer.splitlines()[-1][-60:] if buffer else ""
            raise SSHSessionError(
                f"{phase}：{limit}s 内未收到 prompt。末行: {tail_hint!r}"
            )

        return buffer

    def _send_shell_command(
        self,
        command: str,
        on_debug: Callable[[str, str], None] | None = None,
        max_seconds: int | None = None,
        metric_key: str | None = None,
        on_metrics: Callable[[dict[str, int]], None] | None = None,
    ) -> str:
        if not self._session_alive():
            raise SSHSessionError("SSH 连接不可用")

        safe_command = assert_readonly_command(command)
        self._channel.send(safe_command + "\n")
        buffer = self._read_until_prompt(
            command=safe_command,
            phase=f"执行 {safe_command}",
            on_debug=on_debug,
            max_seconds=max_seconds,
            metric_key=metric_key,
            on_metrics=on_metrics,
        )
        return self._clean_command_output(safe_command, buffer)

    @abstractmethod
    def parse_arp(
        self,
        output: str,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> list[ArpEntry]:
        raise NotImplementedError

    @abstractmethod
    def parse_ipv6_neighbors(
        self,
        output: str,
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> list[Ipv6NeighborEntry]:
        raise NotImplementedError


def create_collector(
    vendor: Vendor,
    model: str,
    host: str,
    username: str,
    password: str,
    port: int = 22,
    timeout: int = 30,
) -> SwitchCollector:
    from app.collectors.h3c import H3CCollector
    from app.collectors.huawei import HuaweiCollector

    collectors = {
        Vendor.HUAWEI: HuaweiCollector,
        Vendor.H3C: H3CCollector,
    }
    cls = collectors[vendor]
    return cls(
        host=host,
        username=username,
        password=password,
        vendor=vendor,
        model=model,
        port=port,
        timeout=timeout,
    )
