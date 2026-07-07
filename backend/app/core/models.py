from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Vendor(str, Enum):
    HUAWEI = "huawei"
    H3C = "h3c"


class StackType(str, Enum):
    DUAL_STACK = "dual_stack"
    IPV4_ONLY = "ipv4_only"
    IPV6_ONLY = "ipv6_only"


class DeviceRole(str, Enum):
    """从 ARP/ND 推断的设备角色。"""

    HOST = "host"  # 办公室电脑等终端
    ROUTER = "router"  # 路由器/三层网关（含 Is Router 标记）
    NETWORK = "network"  # 交换机/网络设备
    ND_NEIGHBOR = "nd_neighbor"  # 仅 ND 可见的下游邻居（办公室出口等）


class SurveyRequest(BaseModel):
    vendor: Vendor
    model: str = Field(..., description="设备型号，用于匹配 CLI 模板")
    host: str = Field(..., description="交换机管理 IP")
    port: int = Field(default=22, ge=1, le=65535)
    username: str
    password: str
    timeout: int = Field(
        default=180,
        ge=30,
        le=900,
        description="SSH 连接超时（秒）；大规模 ARP 建议 180~900",
    )


class DeviceRecord(BaseModel):
    mac: str
    role: DeviceRole = DeviceRole.HOST
    is_router: bool = False
    stack_type: StackType
    ipv4_addresses: list[str] = Field(default_factory=list)
    ipv6_addresses: list[str] = Field(default_factory=list)
    global_ipv6_addresses: list[str] = Field(default_factory=list)
    link_local_ipv6_addresses: list[str] = Field(default_factory=list)
    vlan_ids: list[int] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)


class SurveyStatistics(BaseModel):
    """终端（HOST）双栈占比为主指标；网络设备/ND 邻居单独统计。"""

    total_devices: int = Field(description="可观测终端（HOST）数量")
    dual_stack_count: int = Field(description="双栈终端数量")
    ipv4_only_count: int = Field(description="纯 IPv4 终端数量")
    ipv6_only_count: int = 0
    dual_stack_ratio: float = Field(description="双栈终端占终端总数比例 (0-100)")
    ipv4_only_ratio: float = 0.0
    ipv6_only_ratio: float = 0.0

    host_count: int = 0
    network_device_count: int = 0
    nd_neighbor_count: int = 0
    host_dual_stack_count: int = 0
    host_ipv4_only_count: int = 0
    network_dual_stack_count: int = 0
    network_ipv4_only_count: int = 0
    nd_neighbor_with_ipv6_count: int = 0

    total_ipv4_addresses: int = 0
    total_ipv6_global_addresses: int = 0
    total_ipv6_link_local_addresses: int = 0
    observable_mac_count: int = Field(
        default=0,
        description="ARP+ND 合并后的可观测 MAC 总数（含网络设备）",
    )


class SurveyResultSummary(BaseModel):
    """统计摘要（不含设备明细，适用于 SSE 与大规模结果）。"""

    vendor: Vendor
    model: str
    host: str
    statistics: SurveyStatistics
    raw_arp_entries: int
    raw_ipv6_entries: int
    unique_device_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class SurveyResult(SurveyResultSummary):
    devices: list[DeviceRecord] = Field(default_factory=list)


class CollectMetrics(BaseModel):
    arp_bytes_received: int = 0
    arp_lines_parsed: int = 0
    ipv6_bytes_received: int = 0
    ipv6_lines_parsed: int = 0
    more_pages_sent: int = 0


class DevicePageResponse(BaseModel):
    job_id: str
    page: int
    page_size: int
    total: int
    total_pages: int
    stack_type: Optional[str] = None
    devices: list[DeviceRecord]


class DeviceModelInfo(BaseModel):
    vendor: Vendor
    model: str
    description: str


class VendorModelsResponse(BaseModel):
    vendor: Vendor
    models: list[DeviceModelInfo]


class HealthResponse(BaseModel):
    status: str
    version: str


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class DebugLogEntry(BaseModel):
    timestamp: str
    level: str
    message: str


class SurveyJobCreateRequest(SurveyRequest):
    resume_job_id: Optional[str] = Field(
        default=None, description="从中断的任务恢复采集"
    )


class SurveyJobSnapshot(BaseModel):
    job_id: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    current_step: str
    step_label: str
    debug_logs: list[DebugLogEntry] = Field(default_factory=list)
    completed_steps: list[str] = Field(default_factory=list)
    metrics: CollectMetrics = Field(default_factory=CollectMetrics)
    can_resume: bool = False
    is_partial: bool = False
    partial_result: Optional[SurveyResultSummary] = None
    result: Optional[SurveyResultSummary] = None
    error: Optional[str] = None
    vendor: Optional[Vendor] = None
    model: Optional[str] = None
    host: Optional[str] = None
    design_capacity: int = 50_000


class SurveyJobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    resumed_from: Optional[str] = None
