export type Vendor = "huawei" | "h3c";

export type StackType = "dual_stack" | "ipv4_only" | "ipv6_only";

export type DeviceRole = "host" | "router" | "network" | "nd_neighbor";

export type JobStatus = "pending" | "running" | "paused" | "completed" | "failed";

export interface DeviceModelInfo {
  vendor: Vendor;
  model: string;
  description: string;
}

export interface SurveyRequest {
  vendor: Vendor;
  model: string;
  host: string;
  port: number;
  username: string;
  password: string;
  timeout: number;
}

export interface SurveyStatistics {
  total_devices: number;
  dual_stack_count: number;
  ipv4_only_count: number;
  ipv6_only_count: number;
  dual_stack_ratio: number;
  ipv4_only_ratio: number;
  ipv6_only_ratio: number;
  host_count: number;
  network_device_count: number;
  nd_neighbor_count: number;
  host_dual_stack_count: number;
  host_ipv4_only_count: number;
  network_dual_stack_count: number;
  network_ipv4_only_count: number;
  nd_neighbor_with_ipv6_count: number;
  total_ipv4_addresses: number;
  total_ipv6_global_addresses: number;
  total_ipv6_link_local_addresses: number;
  observable_mac_count: number;
}

export interface DeviceRecord {
  mac: string;
  role: DeviceRole;
  is_router: boolean;
  stack_type: StackType;
  ipv4_addresses: string[];
  ipv6_addresses: string[];
  global_ipv6_addresses: string[];
  link_local_ipv6_addresses: string[];
  vlan_ids: number[];
  interfaces: string[];
}

export interface SurveyResultSummary {
  vendor: Vendor;
  model: string;
  host: string;
  statistics: SurveyStatistics;
  raw_arp_entries: number;
  raw_ipv6_entries: number;
  unique_device_count: number;
  warnings: string[];
}

export interface SurveyResult extends SurveyResultSummary {
  devices: DeviceRecord[];
}

export interface CollectMetrics {
  arp_bytes_received: number;
  arp_lines_parsed: number;
  ipv6_bytes_received: number;
  ipv6_lines_parsed: number;
  more_pages_sent: number;
}

export interface DebugLogEntry {
  timestamp: string;
  level: string;
  message: string;
}

export interface SurveyJobSnapshot {
  job_id: string;
  status: JobStatus;
  progress: number;
  current_step: string;
  step_label: string;
  debug_logs: DebugLogEntry[];
  completed_steps: string[];
  metrics: CollectMetrics;
  can_resume: boolean;
  is_partial: boolean;
  partial_result: SurveyResultSummary | null;
  result: SurveyResultSummary | null;
  error: string | null;
  vendor: Vendor | null;
  model: string | null;
  host: string | null;
  design_capacity: number;
}

export interface DevicePageResponse {
  job_id: string;
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  stack_type: string | null;
  devices: DeviceRecord[];
}

export interface SurveyJobCreateResponse {
  job_id: string;
  status: JobStatus;
  resumed_from: string | null;
}

const API_BASE = "/api";

export async function fetchVendors(): Promise<
  { id: Vendor; name: string }[]
> {
  const res = await fetch(`${API_BASE}/vendors`);
  if (!res.ok) throw new Error("无法加载厂商列表");
  const data = await res.json();
  return data.vendors;
}

export async function fetchModels(vendor: Vendor): Promise<DeviceModelInfo[]> {
  const res = await fetch(`${API_BASE}/vendors/${vendor}/models`);
  if (!res.ok) throw new Error("无法加载设备型号");
  const data = await res.json();
  return data.models;
}

export async function createSurveyJob(
  request: SurveyRequest,
  resumeJobId?: string
): Promise<SurveyJobCreateResponse> {
  const res = await fetch(`${API_BASE}/survey/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...request,
      resume_job_id: resumeJobId ?? null,
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "创建采集任务失败");
  }
  return res.json();
}

export async function cancelSurveyJob(
  jobId: string
): Promise<SurveyJobSnapshot> {
  const res = await fetch(`${API_BASE}/survey/jobs/${jobId}/cancel`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "中断失败");
  }
  return res.json();
}

export async function resumeSurveyJob(
  jobId: string
): Promise<SurveyJobCreateResponse> {
  const res = await fetch(`${API_BASE}/survey/jobs/${jobId}/resume`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "恢复失败");
  }
  return res.json();
}

export async function fetchJobDevices(
  jobId: string,
  page: number,
  pageSize: number = 100,
  stackType?: StackType
): Promise<DevicePageResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (stackType) params.set("stack_type", stackType);

  const res = await fetch(
    `${API_BASE}/survey/jobs/${jobId}/devices?${params.toString()}`
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "加载设备明细失败");
  }
  return res.json();
}

export function streamSurveyJob(
  jobId: string,
  onUpdate: (snapshot: SurveyJobSnapshot) => void,
  onError?: (err: Error) => void
): () => void {
  const es = new EventSource(`${API_BASE}/survey/jobs/${jobId}/stream`);

  es.onmessage = (event) => {
    try {
      const snapshot = JSON.parse(event.data) as SurveyJobSnapshot;
      onUpdate(snapshot);
    } catch (e) {
      onError?.(e instanceof Error ? e : new Error("解析进度数据失败"));
    }
  };

  es.onerror = () => {
    onError?.(new Error("进度流连接中断"));
    es.close();
  };

  return () => es.close();
}

export const STACK_LABELS: Record<StackType, string> = {
  dual_stack: "双栈",
  ipv4_only: "纯 IPv4",
  ipv6_only: "纯 IPv6",
};

export const ROLE_LABELS: Record<DeviceRole, string> = {
  host: "终端",
  router: "路由器",
  network: "网络设备",
  nd_neighbor: "ND 邻居",
};

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  pending: "等待中",
  running: "采集中",
  paused: "已中断",
  completed: "已完成",
  failed: "失败",
};

export const DEVICE_PAGE_SIZE = 100;
