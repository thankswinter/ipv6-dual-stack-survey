import { FormEvent, useEffect, useRef, useState } from "react";
import {
  DeviceModelInfo,
  DeviceRecord,
  JOB_STATUS_LABELS,
  JobStatus,
  STACK_LABELS,
  StackType,
  ROLE_LABELS,
  SurveyJobSnapshot,
  SurveyRequest,
  DEVICE_PAGE_SIZE,
  fetchJobDevices,
  SurveyResultSummary,
  Vendor,
  cancelSurveyJob,
  createSurveyJob,
  fetchModels,
  fetchVendors,
  resumeSurveyJob,
  streamSurveyJob,
} from "./api/client";

type FilterType = "all" | StackType;

const TERMINAL: JobStatus[] = ["completed", "paused", "failed"];

export default function App() {
  const [vendors, setVendors] = useState<{ id: Vendor; name: string }[]>([]);
  const [models, setModels] = useState<DeviceModelInfo[]>([]);
  const [vendor, setVendor] = useState<Vendor>("huawei");
  const [model, setModel] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [timeoutSec, setTimeoutSec] = useState("180");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterType>("all");

  const [jobId, setJobId] = useState<string | null>(null);
  const [jobSnapshot, setJobSnapshot] = useState<SurveyJobSnapshot | null>(null);
  const [isPartial, setIsPartial] = useState(false);
  const [result, setResult] = useState<SurveyResultSummary | null>(null);
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [devicePage, setDevicePage] = useState(1);
  const [deviceTotal, setDeviceTotal] = useState(0);
  const [deviceTotalPages, setDeviceTotalPages] = useState(1);
  const [devicesLoading, setDevicesLoading] = useState(false);

  const closeStreamRef = useRef<(() => void) | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);

  const isRunning = jobSnapshot?.status === "running";
  const canResume = jobSnapshot?.can_resume === true;
  const canCancel = isRunning;

  useEffect(() => {
    fetchVendors()
      .then(setVendors)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    fetchModels(vendor)
      .then((list) => {
        setModels(list);
        setModel(list[0]?.model ?? "");
      })
      .catch((e) => setError(e.message));
  }, [vendor]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [jobSnapshot?.debug_logs.length]);

  useEffect(() => {
    return () => closeStreamRef.current?.();
  }, []);

  useEffect(() => {
    if (!jobId || !result) return;
    setDevicesLoading(true);
    const stack = filter === "all" ? undefined : filter;
    fetchJobDevices(jobId, devicePage, DEVICE_PAGE_SIZE, stack)
      .then((page) => {
        setDevices(page.devices);
        setDeviceTotal(page.total);
        setDeviceTotalPages(page.total_pages);
      })
      .catch((e) => setError(e.message))
      .finally(() => setDevicesLoading(false));
  }, [jobId, result, filter, devicePage]);

  function buildRequest(): SurveyRequest {
    return {
      vendor,
      model,
      host,
      port: parseInt(port, 10) || 22,
      username,
      password,
      timeout: parseInt(timeoutSec, 10) || 180,
    };
  }

  function applySnapshot(snapshot: SurveyJobSnapshot) {
    setJobSnapshot(snapshot);
    if (snapshot.result) {
      setResult(snapshot.result);
      setIsPartial(false);
      setDevicePage(1);
    } else if (snapshot.partial_result) {
      setResult(snapshot.partial_result);
      setIsPartial(true);
      setDevicePage(1);
    }
    if (snapshot.error) {
      setError(snapshot.error);
    }
    if (TERMINAL.includes(snapshot.status)) {
      closeStreamRef.current?.();
      closeStreamRef.current = null;
    }
  }

  function subscribeJob(id: string) {
    closeStreamRef.current?.();
    closeStreamRef.current = streamSurveyJob(
      id,
      applySnapshot,
      (err) => setError(err.message)
    );
  }

  async function startJob(resumeFromId?: string) {
    setError(null);
    if (!resumeFromId) {
      setResult(null);
      setDevices([]);
      setIsPartial(false);
    }

    try {
      const created = resumeFromId
        ? await resumeSurveyJob(resumeFromId)
        : await createSurveyJob(buildRequest());
      setJobId(created.job_id);
      setJobSnapshot({
        job_id: created.job_id,
        status: created.status,
        progress: 0,
        current_step: "pending",
        step_label: "准备中",
        debug_logs: [],
        completed_steps: [],
        metrics: {
          arp_bytes_received: 0,
          arp_lines_parsed: 0,
          ipv6_bytes_received: 0,
          ipv6_lines_parsed: 0,
          more_pages_sent: 0,
        },
        can_resume: false,
        is_partial: false,
        partial_result: null,
        result: null,
        error: null,
        vendor,
        model,
        host,
        design_capacity: 50000,
      });
      subscribeJob(created.job_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动失败");
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    await startJob();
  }

  async function handleCancel() {
    if (!jobId || !canCancel) return;
    try {
      const snapshot = await cancelSurveyJob(jobId);
      applySnapshot(snapshot);
    } catch (err) {
      setError(err instanceof Error ? err.message : "中断失败");
    }
  }

  async function handleResume() {
    if (!jobId || !canResume) return;
    await startJob(jobId);
  }

  const stats = result?.statistics;
  const progress = jobSnapshot?.progress ?? 0;
  const stepLabel = jobSnapshot?.step_label ?? "";
  const status = jobSnapshot?.status;

  return (
    <>
      <h1>IPv6 双栈测绘</h1>
      <p className="subtitle">
        登录核心交换机，采集 ARP 与 IPv6 邻居表，按 MAC 地址精准统计双栈终端
      </p>

      <div className="layout">
        <aside className="card">
          <h2>交换机连接</h2>
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label>设备厂商</label>
              <select
                value={vendor}
                onChange={(e) => setVendor(e.target.value as Vendor)}
                disabled={isRunning}
              >
                {vendors.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>设备型号</label>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                required
                disabled={isRunning}
              >
                {models.map((m) => (
                  <option key={m.model} value={m.model}>
                    {m.model} — {m.description}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>管理 IP 地址</label>
              <input
                type="text"
                value={host}
                onChange={(e) => setHost(e.target.value)}
                placeholder="例如 192.168.1.1"
                required
                disabled={isRunning}
              />
            </div>

            <div className="form-row">
              <div className="form-group">
                <label>用户名</label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  disabled={isRunning}
                />
              </div>
              <div className="form-group">
                <label>端口</label>
                <input
                  type="number"
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                  min={1}
                  max={65535}
                  disabled={isRunning}
                />
              </div>
            </div>

            <div className="form-group">
              <label>密码</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                disabled={isRunning}
              />
            </div>

            <div className="form-group">
              <label>SSH 超时（秒）</label>
              <input
                type="number"
                value={timeoutSec}
                onChange={(e) => setTimeoutSec(e.target.value)}
                min={30}
                max={900}
                disabled={isRunning}
              />
            </div>

            <div className="btn-row">
              <button className="btn" type="submit" disabled={isRunning}>
                {isRunning ? "采集中…" : "开始测绘"}
              </button>
              {canCancel && (
                <button
                  className="btn btn-danger"
                  type="button"
                  onClick={handleCancel}
                >
                  中断采集
                </button>
              )}
              {canResume && (
                <button
                  className="btn btn-secondary"
                  type="button"
                  onClick={handleResume}
                >
                  恢复采集
                </button>
              )}
            </div>
          </form>
        </aside>

        <main>
          {error && <div className="error">{error}</div>}

          {jobSnapshot && (
            <div className="card progress-card">
              <div className="progress-header">
                <h2>采集进度</h2>
                {status && (
                  <span className={`status-badge status-${status}`}>
                    {JOB_STATUS_LABELS[status]}
                  </span>
                )}
              </div>
              <p className="progress-step">{stepLabel}</p>
              <div className="progress-track">
                <div
                  className="progress-fill"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="progress-meta">
                <span>{progress}%</span>
                {jobSnapshot.metrics.arp_bytes_received > 0 && (
                  <span>
                    ARP 已接收 {(jobSnapshot.metrics.arp_bytes_received / 1024).toFixed(0)} KB
                  </span>
                )}
                {jobSnapshot.metrics.more_pages_sent > 0 && (
                  <span>自动翻页 {jobSnapshot.metrics.more_pages_sent} 次</span>
                )}
                {jobSnapshot.completed_steps.length > 0 && (
                  <span>
                    已完成: {jobSnapshot.completed_steps.join(" → ")}
                  </span>
                )}
              </div>

              <h3 className="debug-title">调试信息</h3>
              <div className="debug-log">
                {jobSnapshot.debug_logs.length === 0 && (
                  <div className="debug-line debug-info">等待日志…</div>
                )}
                {jobSnapshot.debug_logs.map((log, i) => (
                  <div key={i} className={`debug-line debug-${log.level}`}>
                    <span className="debug-time">
                      {new Date(log.timestamp).toLocaleTimeString()}
                    </span>
                    <span className="debug-level">[{log.level}]</span>
                    {log.message}
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            </div>
          )}

          {!result && !jobSnapshot && !error && (
            <div className="card empty-state">
              填写交换机信息后点击「开始测绘」，系统将 SSH 登录并采集 ARP / IPv6
              邻居表
            </div>
          )}

          {result && stats && (
            <div className="card">
              <div className="progress-header">
                <h2>{isPartial ? "部分报告（中断前数据）" : "统计结果"}</h2>
                {isPartial && (
                  <span className="status-badge status-paused">部分数据</span>
                )}
              </div>
              <p className="meta">
                {result.host} · {result.vendor.toUpperCase()} {result.model} ·
                ARP {result.raw_arp_entries.toLocaleString()} 条 · IPv6 邻居{" "}
                {result.raw_ipv6_entries.toLocaleString()} 条 · 终端{" "}
                {stats.host_count.toLocaleString()} 台 · 网络设备{" "}
                {stats.network_device_count.toLocaleString()} 台 · IPv4 地址{" "}
                {stats.total_ipv4_addresses.toLocaleString()} 个 · 全局 IPv6{" "}
                {stats.total_ipv6_global_addresses.toLocaleString()} 个
              </p>

              {result.warnings.length > 0 && (
                <div className="warnings">
                  <strong>提示</strong>
                  <ul>
                    {result.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="stats-grid">
                <div className="stat-card total">
                  <div className="value">{stats.host_count}</div>
                  <div className="label">办公室终端</div>
                </div>
                <div className="stat-card dual">
                  <div className="value">{stats.host_dual_stack_count}</div>
                  <div className="label">终端双栈</div>
                </div>
                <div className="stat-card ipv4">
                  <div className="value">{stats.host_ipv4_only_count}</div>
                  <div className="label">终端纯 IPv4</div>
                </div>
                <div className="stat-card ipv6">
                  <div className="value">{stats.network_device_count}</div>
                  <div className="label">网络设备</div>
                </div>
              </div>

              <div className="stats-grid secondary-stats">
                <div className="stat-card">
                  <div className="value">{stats.network_dual_stack_count}</div>
                  <div className="label">网络设备双栈</div>
                </div>
                <div className="stat-card">
                  <div className="value">{stats.nd_neighbor_count}</div>
                  <div className="label">下游 ND 邻居</div>
                </div>
                <div className="stat-card">
                  <div className="value">{stats.observable_mac_count}</div>
                  <div className="label">可观测 MAC 总数</div>
                </div>
              </div>

              <div className="ratio-bar">
                <span
                  className="dual"
                  style={{ width: `${stats.dual_stack_ratio}%` }}
                />
                <span
                  className="ipv4"
                  style={{ width: `${stats.ipv4_only_ratio}%` }}
                />
              </div>

              <div className="legend">
                <span className="legend-item">
                  <span
                    className="legend-dot"
                    style={{ background: "var(--dual)" }}
                  />
                  双栈终端 {stats.dual_stack_ratio}%
                </span>
                <span className="legend-item">
                  <span
                    className="legend-dot"
                    style={{ background: "var(--ipv4)" }}
                  />
                  终端纯 IPv4 {stats.ipv4_only_ratio}%
                </span>
                <span className="legend-item">
                  <span
                    className="legend-dot"
                    style={{ background: "var(--accent)" }}
                  />
                  网络设备双栈 {stats.network_dual_stack_count} 台
                </span>
              </div>

              <h2>设备明细</h2>
              <div className="filter-row">
                {(
                  ["all", "dual_stack", "ipv4_only", "ipv6_only"] as FilterType[]
                ).map((f) => (
                  <button
                    key={f}
                    type="button"
                    className={`filter-btn ${filter === f ? "active" : ""}`}
                    onClick={() => {
                      setFilter(f);
                      setDevicePage(1);
                    }}
                  >
                    {f === "all" ? "全部" : STACK_LABELS[f]}
                  </button>
                ))}
              </div>

              {devicesLoading ? (
                <p className="empty-state">加载设备明细…</p>
              ) : (
                <DeviceTable devices={devices} />
              )}

              {deviceTotalPages > 1 && (
                <div className="pagination">
                  <button
                    type="button"
                    className="filter-btn"
                    disabled={devicePage <= 1}
                    onClick={() => setDevicePage((p) => p - 1)}
                  >
                    上一页
                  </button>
                  <span className="page-info">
                    第 {devicePage} / {deviceTotalPages} 页（共{" "}
                    {deviceTotal.toLocaleString()} 条）
                  </span>
                  <button
                    type="button"
                    className="filter-btn"
                    disabled={devicePage >= deviceTotalPages}
                    onClick={() => setDevicePage((p) => p + 1)}
                  >
                    下一页
                  </button>
                </div>
              )}
            </div>
          )}
        </main>
      </div>

      <section className="algorithm-doc">
        <details>
          <summary>双栈统计算法说明</summary>
          <ol>
            <li>
              通过 SSH 登录核心交换机，按设备型号执行对应 CLI（华为/H3C 的{" "}
              <code>display arp</code> 与 <code>display ipv6 neighbors</code>）
            </li>
            <li>
              解析 ARP 表得到 IPv4→MAC 映射，解析 IPv6 邻居表得到 IPv6→MAC 映射
            </li>
            <li>
              将所有 MAC 地址归一化为统一格式（AA:BB:CC:DD:EE:FF），过滤组播/广播
              MAC
            </li>
            <li>
              以 MAC 为唯一设备标识聚合：同一 MAC 在多个 VLAN/接口出现仍计为 1
              台终端
            </li>
            <li>
              分类规则：同时有 IPv4 和 IPv6 地址 → 双栈；仅有 IPv4 → 纯 IPv4；仅有
              IPv6 → 纯 IPv6
            </li>
            <li>
              双栈占比 = 双栈设备数 ÷ 唯一 MAC 总数 × 100%
            </li>
          </ol>
        </details>
      </section>
    </>
  );
}

function DeviceTable({ devices }: { devices: DeviceRecord[] }) {
  if (devices.length === 0) {
    return <p className="empty-state">无匹配设备</p>;
  }

  return (
    <div className="device-table-wrap">
      <table>
        <thead>
          <tr>
            <th>MAC</th>
            <th>角色</th>
            <th>协议栈</th>
            <th>IPv4</th>
            <th>全局 IPv6</th>
            <th>链路本地 IPv6</th>
            <th>VLAN</th>
            <th>接口</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((d) => (
            <tr key={d.mac}>
              <td>{d.mac}</td>
              <td>
                <span className={`badge role-${d.role}`}>
                  {ROLE_LABELS[d.role] || d.role}
                </span>
              </td>
              <td>
                <span className={`badge ${d.stack_type}`}>
                  {STACK_LABELS[d.stack_type]}
                </span>
              </td>
              <td>{d.ipv4_addresses.join(", ") || "—"}</td>
              <td>{d.global_ipv6_addresses.join(", ") || "—"}</td>
              <td>{d.link_local_ipv6_addresses.join(", ") || "—"}</td>
              <td>{d.vlan_ids.join(", ") || "—"}</td>
              <td>{d.interfaces.join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
