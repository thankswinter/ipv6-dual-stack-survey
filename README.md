# IPv6 双栈测绘系统

通过 SSH 登录核心交换机，采集 ARP 表与 IPv6 邻居表，以 MAC 地址为唯一标识统计 IPv4/IPv6 双栈终端数量与占比。

## 项目结构

```
ipv6-dual-stack-survey/
├── backend/          # FastAPI + Paramiko SSH 采集
├── frontend/         # React + Vite 前端
└── docs/             # 算法文档
```

## 快速启动

### 后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API 文档：http://127.0.0.1:8000/docs

### 前端

```bash
cd frontend
npm install
npm run dev
```

访问：http://127.0.0.1:5173

## 双栈统计算法

详见 [docs/ALGORITHM.md](docs/ALGORITHM.md)

### 核心公式

| 指标 | 计算方式 |
|------|----------|
| 终端总数 | 去重后的唯一 MAC 数量 |
| 双栈设备数 | 同时存在 IPv4 与 IPv6 地址的 MAC 数 |
| 纯 IPv4 设备数 | 仅有 IPv4 地址的 MAC 数 |
| 双栈占比 | 双栈设备数 ÷ 终端总数 × 100% |

### 采集命令（按厂商/型号）

| 厂商 | ARP | IPv6 邻居 |
|------|-----|-----------|
| 华为 | `display arp all` | `display ipv6 neighbors` |
| H3C | `display arp` | `display ipv6 neighbors` |

型号与 CLI 模板的映射见 `backend/app/cli/templates.py`。

## 支持的设备型号

- **华为**：S12700、S9700、S7700、S6700、S5735、S5732、S5720、S5700、CE6857、CE6881、CE12800
- **H3C**：S12500、S10500、S7500E、S6800、S6520X、S6520、S5820V2、S5130、S5120、S5560X

未列出的型号将使用同厂商默认 CLI 模板。

## 设计容量：5 万条 ARP

系统按 **50,000 条 ARP** 规模设计，主要参数见 `backend/app/core/scale.py`：

| 参数 | 值 | 说明 |
|------|-----|------|
| ARP 命令超时 | 900 秒 | `display arp all` 大数据量输出 |
| 自动翻页上限 | 12,000 页 | 应对 `--More--` 分页 |
| SSH 默认超时 | 180 秒 | 前端可调至 900 秒 |
| 设备明细分页 | 100 条/页 | API + 前端分页，避免一次加载 5 万行 |

进度 SSE 仅推送统计摘要，设备明细通过 `GET /api/survey/jobs/{id}/devices?page=1` 分页获取。

## 安全说明：只读操作

本系统 **不会修改交换机任何配置**。SSH 会话中仅发送以下两类命令：

| 类型 | 命令示例 | 说明 |
|------|----------|------|
| 查询 | `display arp`、`display ipv6 neighbors` | 只读，读取邻居表 |
| 会话分页 | `screen-length 0 temporary` / `screen-length disable` | 仅当前 SSH 会话生效，不写入 startup-config |

代码层通过 `app/cli/readonly.py` 白名单强制校验，任何非白名单命令（如 `system-view`、`save`、`undo` 等）会被直接拒绝。

可通过 API 查看允许的命令列表：`GET /api/readonly-commands`

## 注意事项

1. 需确保交换机已开启 SSH，且账号具备 `display arp` / `display ipv6 neighbors` 权限
2. 统计基于 L3 邻居表，反映经过核心交换机路由的终端；纯 L2 未上送 ARP/ND 的终端不会被计入
3. 同一物理终端若使用多个 NIC（不同 MAC），会计为多台设备
4. 建议在业务低峰期执行，避免大表分页导致采集超时
