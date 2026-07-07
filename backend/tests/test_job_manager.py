"""Job 管理器测试 — 检查点、中断、部分报告。"""

from unittest.mock import MagicMock, patch

from app.core.job_manager import JobManager
from app.core.models import DeviceRecord, JobStatus, StackType, SurveyRequest, Vendor
from app.core.phases import CollectCheckpoint, CollectionCancelledError


def _request() -> SurveyRequest:
    return SurveyRequest(
        vendor=Vendor.HUAWEI,
        model="S12700",
        host="192.168.1.1",
        username="admin",
        password="pass",
    )


class TestJobManager:
    def test_build_partial_from_arp_only(self):
        from app.core.job_manager import SurveyJob

        job = SurveyJob(job_id="t1", request=_request())
        job.checkpoint.arp_output = (
            "10.0.0.1  0011-2233-4401  10  GE1/0/1  Dynamic\n"
        )
        job.checkpoint.mark_step("arp")
        job.status = JobStatus.PAUSED
        job._set_partial_from_checkpoint()

        assert job.partial_result is not None
        assert job.partial_result.statistics.ipv4_only_count == 1
        assert job.partial_result.statistics.dual_stack_count == 0

    @patch("app.core.job_manager.create_collector")
    def test_cancel_produces_paused_with_partial(self, mock_factory):
        mock_collector = MagicMock()

        def phased(**kwargs):
            cp = kwargs.get("checkpoint") or CollectCheckpoint()
            cp.arp_output = "10.0.0.1  0011-2233-4401  10  GE1/0/1  Dynamic"
            cp.mark_step("arp")
            raise CollectionCancelledError()

        mock_collector.collect_phased.side_effect = phased
        mock_collector.parse_arp.return_value = []
        mock_collector.parse_ipv6_neighbors.return_value = []
        mock_factory.return_value = mock_collector

        mgr = JobManager()
        job = mgr.create_job(_request())
        job._thread.join(timeout=5)

        snap = job.snapshot()
        assert snap.status == JobStatus.PAUSED
        assert snap.can_resume is True
        assert snap.partial_result is not None

    @patch("app.core.job_manager.create_collector")
    def test_resume_reuses_arp_checkpoint(self, mock_factory):
        mock_collector = MagicMock()

        def phased(**kwargs):
            cp = kwargs.get("checkpoint") or CollectCheckpoint()
            if cp.arp_output is None:
                cp.arp_output = "fresh arp"
            cp.ipv6_output = "fe80::1  0011-2233-4401  10  GE1/0/1"
            cp.mark_step("arp")
            cp.mark_step("ipv6")
            return cp, []

        mock_collector.collect_phased.side_effect = phased
        mock_collector.parse_arp.return_value = []
        mock_collector.parse_ipv6_neighbors.return_value = []
        mock_factory.return_value = mock_collector

        mgr = JobManager()
        old = mgr.create_job(_request())
        old._thread.join(timeout=5)

        old.status = JobStatus.PAUSED
        old.checkpoint.arp_output = "cached arp"
        old.checkpoint.completed_steps = {"arp"}

        new_job = mgr.create_job(_request(), resume_job_id=old.job_id)
        new_job._thread.join(timeout=5)

        call_kwargs = mock_collector.collect_phased.call_args.kwargs
        assert call_kwargs["checkpoint"].arp_output == "cached arp"

    @patch("app.core.job_manager.create_collector")
    def test_parse_progress_logs_use_debug_level(self, mock_factory):
        from app.core.job_manager import SurveyJob

        mock_collector = MagicMock()
        mock_collector.parse_arp.side_effect = lambda output, on_progress=None: (
            on_progress(1, 1, 1) if on_progress else None
        ) or []
        mock_collector.parse_ipv6_neighbors.side_effect = (
            lambda output, on_progress=None: (
                on_progress(1, 1, 1) if on_progress else None
            )
            or []
        )
        mock_factory.return_value = mock_collector

        job = SurveyJob(job_id="t1", request=_request())
        job.checkpoint.arp_output = "arp"
        job.checkpoint.ipv6_output = "ipv6"
        job._analyze_checkpoint()

        parse_logs = [
            log
            for log in job.debug_logs
            if log.message.startswith(("ARP 解析：", "IPv6 解析："))
        ]
        assert parse_logs
        assert {log.level for log in parse_logs} == {"debug"}

    def test_get_devices_page_normalizes_page_and_size(self):
        from app.core.job_manager import SurveyJob

        mgr = JobManager()
        job = SurveyJob(job_id="t1", request=_request())
        job.devices = [
            DeviceRecord(
                mac=f"00:11:22:33:44:{i:02X}",
                stack_type=StackType.IPV4_ONLY,
            )
            for i in range(3)
        ]
        mgr._jobs[job.job_id] = job

        devices, total, total_pages, page, page_size = mgr.get_devices_page(
            job.job_id,
            page=99,
            page_size=0,
        )

        assert len(devices) == 1
        assert total == 3
        assert total_pages == 3
        assert page == 3
        assert page_size == 1
