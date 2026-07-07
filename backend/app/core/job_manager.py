from __future__ import annotations

import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.collectors.base import create_collector, SSHSessionError
from app.core.algorithm import analyze_dual_stack
from app.core.models import (
    CollectMetrics,
    DebugLogEntry,
    DeviceRecord,
    DeviceRole,
    JobStatus,
    StackType,
    SurveyJobSnapshot,
    SurveyRequest,
    SurveyResultSummary,
    SurveyStatistics,
)
from app.core.phases import (
    LABEL_BY_STEP,
    PROGRESS_BY_STEP,
    CollectionCancelledError,
    CollectCheckpoint,
)
from app.core.scale import (
    DEVICE_PAGE_SIZE_DEFAULT,
    DEVICE_PAGE_SIZE_MAX,
    MAX_DEBUG_LOGS_IN_SNAPSHOT,
    PARSE_PROGRESS_LINES,
    TARGET_ARP_ENTRIES,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_stats() -> SurveyStatistics:
    return SurveyStatistics(
        total_devices=0,
        dual_stack_count=0,
        ipv4_only_count=0,
        ipv6_only_count=0,
        dual_stack_ratio=0.0,
        ipv4_only_ratio=0.0,
        ipv6_only_ratio=0.0,
        host_count=0,
        network_device_count=0,
        nd_neighbor_count=0,
        observable_mac_count=0,
    )


def _build_summary(
    request: SurveyRequest,
    stats: SurveyStatistics,
    raw_arp: int,
    raw_ipv6: int,
    warnings: list[str],
    unique_device_count: int,
) -> SurveyResultSummary:
    return SurveyResultSummary(
        vendor=request.vendor,
        model=request.model,
        host=request.host,
        statistics=stats,
        raw_arp_entries=raw_arp,
        raw_ipv6_entries=raw_ipv6,
        unique_device_count=unique_device_count,
        warnings=warnings,
    )


@dataclass
class SurveyJob:
    job_id: str
    request: SurveyRequest
    status: JobStatus = JobStatus.PENDING
    progress: int = 0
    current_step: str = "pending"
    debug_logs: list[DebugLogEntry] = field(default_factory=list)
    metrics: CollectMetrics = field(default_factory=CollectMetrics)
    checkpoint: CollectCheckpoint = field(default_factory=CollectCheckpoint)
    partial_result: SurveyResultSummary | None = None
    result: SurveyResultSummary | None = None
    devices: list[DeviceRecord] = field(default_factory=list)
    error: str | None = None
    cancel_requested: bool = False
    resumed_from: str | None = None
    _thread: threading.Thread | None = field(default=None, repr=False)
    _listeners: list[threading.Condition] = field(default_factory=list, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def snapshot(self) -> SurveyJobSnapshot:
        with self._lock:
            return SurveyJobSnapshot(
                job_id=self.job_id,
                status=self.status,
                progress=self.progress,
                current_step=self.current_step,
                step_label=LABEL_BY_STEP.get(self.current_step, self.current_step),
                debug_logs=self.debug_logs[-MAX_DEBUG_LOGS_IN_SNAPSHOT:],
                completed_steps=sorted(self.checkpoint.completed_steps),
                metrics=self.metrics.model_copy(),
                can_resume=self.status == JobStatus.PAUSED,
                is_partial=self.partial_result is not None and self.result is None,
                partial_result=self.partial_result,
                result=self.result,
                error=self.error,
                vendor=self.request.vendor,
                model=self.request.model,
                host=self.request.host,
                design_capacity=TARGET_ARP_ENTRIES,
            )

    def _notify(self) -> None:
        for cond in self._listeners:
            with cond:
                cond.notify_all()

    def add_debug(self, level: str, message: str) -> None:
        with self._lock:
            self.debug_logs.append(
                DebugLogEntry(timestamp=_utc_now(), level=level, message=message)
            )
        self._notify()

    def set_progress(self, step: str, message: str | None = None) -> None:
        with self._lock:
            self.current_step = step
            self.progress = PROGRESS_BY_STEP.get(step, self.progress)
            if message:
                self.debug_logs.append(
                    DebugLogEntry(timestamp=_utc_now(), level="info", message=message)
                )
        self._notify()

    def update_metrics(self, data: dict[str, int]) -> None:
        with self._lock:
            for key, value in data.items():
                if hasattr(self.metrics, key):
                    setattr(self.metrics, key, value)
                elif key == "arp_bytes":
                    self.metrics.arp_bytes_received = value
                elif key == "ipv6_bytes":
                    self.metrics.ipv6_bytes_received = value
        self._notify()

    def should_cancel(self) -> bool:
        with self._lock:
            return self.cancel_requested

    def request_cancel(self) -> None:
        with self._lock:
            self.cancel_requested = True
        self.add_debug("warn", "收到中断请求，将在当前步骤完成后停止…")

    def _analyze_checkpoint(self) -> tuple[SurveyStatistics, list[DeviceRecord], list[str], int, int]:
        collector = create_collector(
            vendor=self.request.vendor,
            model=self.request.model,
            host=self.request.host,
            username=self.request.username,
            password=self.request.password,
            port=self.request.port,
            timeout=self.request.timeout,
        )
        warnings: list[str] = []

        def arp_progress(scanned: int, total: int, valid: int) -> None:
            self.update_metrics({"arp_lines_parsed": valid})
            if scanned % (PARSE_PROGRESS_LINES * 2) == 0 or scanned == total:
                self.add_debug(
                    "debug",
                    f"ARP 解析：{scanned}/{total} 行，有效 {valid} 条",
                )

        def ipv6_progress(scanned: int, total: int, valid: int) -> None:
            self.update_metrics({"ipv6_lines_parsed": valid})
            if scanned % (PARSE_PROGRESS_LINES * 2) == 0 or scanned == total:
                self.add_debug(
                    "debug",
                    f"IPv6 解析：{scanned}/{total} 行，有效 {valid} 条",
                )

        self.set_progress("parse_arp", "解析 ARP 表...")
        arp_entries = (
            collector.parse_arp(
                self.checkpoint.arp_output or "", on_progress=arp_progress
            )
            if self.checkpoint.arp_output
            else []
        )
        with self._lock:
            self.checkpoint.mark_step("parse_arp")

        self.set_progress("parse_ipv6", "解析 IPv6 邻居表...")
        ipv6_entries = (
            collector.parse_ipv6_neighbors(
                self.checkpoint.ipv6_output or "", on_progress=ipv6_progress
            )
            if self.checkpoint.ipv6_output
            else []
        )
        with self._lock:
            self.checkpoint.mark_step("parse_ipv6")
        if self.checkpoint.ipv6_output:
            self.add_debug(
                "info",
                f"IPv6 解析完成：原始 {len(self.checkpoint.ipv6_output)} 字符，"
                f"有效邻居 {len(ipv6_entries)} 条",
            )

        if self.checkpoint.arp_output and not arp_entries:
            warnings.append("ARP 表为空或解析失败，请确认 CLI 模板与设备型号是否匹配")
        if self.checkpoint.ipv6_output and not ipv6_entries:
            from app.collectors.parse_utils import sample_unparsed_ipv6_lines

            warnings.append("IPv6 邻居表为空或解析失败，可能网络尚未启用 IPv6")
            samples = sample_unparsed_ipv6_lines(self.checkpoint.ipv6_output)
            if samples:
                warnings.append(
                    "IPv6 原始输出样例: " + " | ".join(repr(s) for s in samples)
                )

        self.set_progress("analyze", "统计双栈设备...")
        stats, records, algo_warnings = analyze_dual_stack(arp_entries, ipv6_entries)
        return stats, records, warnings + algo_warnings, len(arp_entries), len(ipv6_entries)

    def _store_analysis(
        self,
        stats: SurveyStatistics,
        records: list[DeviceRecord],
        raw_arp: int,
        raw_ipv6: int,
        warnings: list[str],
        *,
        partial: bool,
    ) -> None:
        summary = _build_summary(
            self.request,
            stats,
            raw_arp,
            raw_ipv6,
            warnings,
            unique_device_count=stats.host_count,
        )
        self.devices = records
        if partial:
            self.partial_result = summary
        else:
            self.result = summary
            self.partial_result = None

    def _set_partial_from_checkpoint(self) -> None:
        stats, records, warnings, raw_arp, raw_ipv6 = self._analyze_checkpoint()
        if not records and raw_arp == 0 and raw_ipv6 == 0:
            self.partial_result = _build_summary(
                self.request, _empty_stats(), 0, 0, warnings, 0
            )
            self.devices = []
            return
        if self.status == JobStatus.PAUSED:
            warnings.insert(0, "报告基于中断前已采集的数据（部分报告）")
        self._store_analysis(
            stats, records, raw_arp, raw_ipv6, warnings, partial=True
        )

    def get_devices_page(
        self,
        page: int = 1,
        page_size: int = DEVICE_PAGE_SIZE_DEFAULT,
        stack_type: StackType | None = None,
        role: DeviceRole | None = None,
    ) -> tuple[list[DeviceRecord], int]:
        with self._lock:
            items = self.devices
            if stack_type:
                items = [d for d in items if d.stack_type == stack_type]
            if role:
                items = [d for d in items if d.role == role]
            total = len(items)
            size = min(max(page_size, 1), DEVICE_PAGE_SIZE_MAX)
            current_page = max(page, 1)
            start = (current_page - 1) * size
            end = start + size
            return items[start:end], total

    def _run_collect(self) -> None:
        self.status = JobStatus.RUNNING
        self.cancel_requested = False
        self.error = None
        self._notify()

        collector = create_collector(
            vendor=self.request.vendor,
            model=self.request.model,
            host=self.request.host,
            username=self.request.username,
            password=self.request.password,
            port=self.request.port,
            timeout=self.request.timeout,
        )

        def on_debug(message: str, level: str = "debug") -> None:
            self.add_debug(level, message)

        def on_progress(step: str, message: str) -> None:
            self.set_progress(step, message)

        def should_cancel() -> bool:
            return self.should_cancel()

        try:
            checkpoint, warnings = collector.collect_phased(
                checkpoint=self.checkpoint,
                on_debug=on_debug,
                on_progress=on_progress,
                should_cancel=should_cancel,
                on_metrics=self.update_metrics,
            )
            with self._lock:
                self.checkpoint = checkpoint

            stats, records, algo_warnings, raw_arp, raw_ipv6 = self._analyze_checkpoint()
            all_warnings = warnings + algo_warnings
            with self._lock:
                self.checkpoint.mark_step("analyze")
                self._store_analysis(
                    stats,
                    records,
                    raw_arp,
                    raw_ipv6,
                    all_warnings,
                    partial=False,
                )
                self.status = JobStatus.COMPLETED
                self.current_step = "done"
                self.progress = 100
            self.add_debug(
                "info",
                f"采集完成：ARP {raw_arp} 条，唯一终端 {len(records)} 台",
            )
        except CollectionCancelledError:
            with self._lock:
                self.status = JobStatus.PAUSED
                self.current_step = "paused"
                self._set_partial_from_checkpoint()
            self.add_debug(
                "warn",
                f"采集已中断，已完成步骤: {', '.join(sorted(self.checkpoint.completed_steps)) or '无'}",
            )
        except SSHSessionError as exc:
            with self._lock:
                self.status = JobStatus.FAILED
                self.error = str(exc)
                if self.checkpoint.arp_output or self.checkpoint.ipv6_output:
                    self._set_partial_from_checkpoint()
            self.add_debug("error", f"采集失败: {exc}")
        except Exception as exc:
            with self._lock:
                self.status = JobStatus.FAILED
                self.error = str(exc)
                if self.checkpoint.arp_output or self.checkpoint.ipv6_output:
                    self._set_partial_from_checkpoint()
            self.add_debug("error", f"采集失败: {exc}")
        finally:
            self._notify()


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, SurveyJob] = {}
        self._lock = threading.Lock()

    def create_job(
        self,
        request: SurveyRequest,
        *,
        resume_job_id: str | None = None,
    ) -> SurveyJob:
        checkpoint = CollectCheckpoint()
        resumed_from: str | None = None

        if resume_job_id:
            old = self.get_job(resume_job_id)
            if old.status != JobStatus.PAUSED:
                raise ValueError("只能恢复已中断（paused）的任务")
            checkpoint = CollectCheckpoint(
                arp_output=old.checkpoint.arp_output,
                ipv6_output=old.checkpoint.ipv6_output,
                completed_steps=set(old.checkpoint.completed_steps),
            )
            resumed_from = resume_job_id

        job = SurveyJob(
            job_id=str(uuid.uuid4()),
            request=request,
            checkpoint=checkpoint,
            resumed_from=resumed_from,
        )
        job.add_debug(
            "info",
            f"采集任务已创建（设计容量 {TARGET_ARP_ENTRIES} 条 ARP）",
        )
        if resumed_from:
            job.add_debug(
                "info",
                f"从任务 {resumed_from} 恢复，已完成: {', '.join(sorted(checkpoint.completed_steps))}",
            )

        with self._lock:
            self._jobs[job.job_id] = job

        thread = threading.Thread(target=job._run_collect, daemon=True)
        job._thread = thread
        thread.start()
        return job

    def get_job(self, job_id: str) -> SurveyJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"任务不存在: {job_id}")
        return job

    def cancel_job(self, job_id: str) -> SurveyJobSnapshot:
        job = self.get_job(job_id)
        if job.status != JobStatus.RUNNING:
            raise ValueError("仅运行中的任务可以中断")
        job.request_cancel()
        return job.snapshot()

    def resume_job(self, job_id: str) -> SurveyJob:
        old = self.get_job(job_id)
        if old.status != JobStatus.PAUSED:
            raise ValueError("仅已中断的任务可以恢复")
        return self.create_job(old.request, resume_job_id=job_id)

    def get_devices_page(
        self,
        job_id: str,
        page: int = 1,
        page_size: int = DEVICE_PAGE_SIZE_DEFAULT,
        stack_type: StackType | None = None,
        role: DeviceRole | None = None,
    ) -> tuple[list[DeviceRecord], int, int, int, int]:
        job = self.get_job(job_id)
        current_page = max(page, 1)
        size = min(max(page_size, 1), DEVICE_PAGE_SIZE_MAX)
        devices, total = job.get_devices_page(current_page, size, stack_type, role)
        total_pages = max(1, math.ceil(total / size)) if total else 1
        if current_page > total_pages:
            current_page = total_pages
            devices, total = job.get_devices_page(current_page, size, stack_type, role)
        return devices, total, total_pages, current_page, size


job_manager = JobManager()
