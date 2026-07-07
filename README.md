# IPv6 Dual-Stack Survey

IPv6 双栈测绘系统，用于通过 SSH 登录核心交换机，采集 ARP 表与 IPv6 邻居表，并以 MAC 地址为唯一标识统计办公室终端的 IPv4/IPv6 双栈覆盖情况。

系统面向华为、H3C 等园区核心交换机场景，重点回答：

- 当前可观测终端中有多少台双栈终端
- 有多少台仍为纯 IPv4 终端
- 网络设备与下游 ND 邻居的 IPv6 能力如何
- 大规模 ARP 表采集过程中是否可中断、恢复和分页查看结果

## 功能特性

- **交换机 SSH 采集**：通过 Paramiko 执行厂商 CLI，采集 ARP 与 IPv6 Neighbor Discovery 数据。
- **多厂商模板**：支持华为、H3C 设备型号映射，未列出型号会使用同厂商默认模板。
- **只读安全策略**：通过命令白名单限制，仅允许查询命令与会话级分页命令。
- **双栈统计算法**：按 MAC 聚合 IPv4/IPv6 地址，识别 HOST、ROUTER、NETWORK、ND_NEIGHBOR。
- **任务化采集**：支持异步任务、进度 SSE 推送、中断、恢复、部分报告。
- **大规模分页**：面向 50,000 条 ARP 规模设计，设备明细分页加载，避免前端一次性渲染大表。
- **前后端分离**：后端 FastAPI，前端 React + Vite + TypeScript。
- **测试覆盖**：包含算法、IPv6 解析、只读命令、采集韧性、任务管理与规模参数测试。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | FastAPI、Pydantic、Paramiko、Uvicorn |
| 前端 | React 19、TypeScript、Vite |
| 测试 | Pytest、TypeScript build |
| 采集协议 | SSH |
| 数据来源 | ARP 表、IPv6 邻居表 |

## 项目结构

```text
ipv6-dual-stack-survey/
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI 路由
│   │   ├── cli/              # CLI 模板与只读命令策略
│   │   ├── collectors/       # 华为/H3C 采集与解析
│   │   └── core/             # 算法、模型、任务管理、容量参数
│   ├── tests/                # 后端测试
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── api/              # 前端 API client
│   │   ├── App.tsx           # 主界面
│   │   └── index.css         # 样式
│   └── package.json
├── docs/
│   └── ALGORITHM.md          # 双栈统计算法说明
└── README.md
```

## 快速启动

### 1. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

后端地址：

- API 根路径：http://127.0.0.1:8000
- Swagger 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

如果项目中已经存在 `backend/.venv`，可以直接：

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 2. 启动前端

另开一个终端：

```bash
cd frontend
npm install
npm run dev
```

前端访问地址：

```text
http://127.0.0.1:5173
```

## 使用流程

1. 打开前端页面。
2. 选择交换机厂商与型号。
3. 输入管理 IP、SSH 端口、用户名、密码和超时时间。
4. 点击「开始测绘」。
5. 系统通过 SSE 实时展示采集进度、调试日志和解析统计。
6. 采集完成后查看双栈比例、网络设备统计、ND 邻居统计和设备明细。
7. 如采集耗时较长，可中断任务；系统会基于已采集数据生成部分报告，并支持恢复采集。

## 支持设备

### 华为

S12700、S9700、S7700、S6700、S5735、S5732、S5720、S5700、CE6857、CE6881、CE12800

### H3C

S12500、S10500、S7500E、S6800、S6520X、S6520、S5820V2、S5130、S5120、S5560X

设备型号与 CLI 模板见：

```text
backend/app/cli/templates.py
```

## 采集命令

| 厂商 | 分页控制 | ARP 命令 | IPv6 邻居命令 |
|------|----------|----------|---------------|
| 华为 | `screen-length 0 temporary` | `display arp all` | `display ipv6 neighbors` |
| H3C | `screen-length disable` | `display arp` | `display ipv6 neighbors` |

所有命令都会经过只读白名单校验，策略入口：

```text
backend/app/cli/readonly.py
```

可通过接口查看允许命令：

```text
GET /api/readonly-commands
```

## 双栈统计逻辑

系统分别解析：

- ARP 表：获取 IPv4、MAC、VLAN、接口
- IPv6 邻居表：获取 IPv6、MAC、VLAN、接口、邻居状态、路由器标记

然后按 MAC 地址聚合为设备视图：

```text
0011-2233-4455 -> 00:11:22:33:44:55
```

核心分类：

| 类型 | 判断条件 |
|------|----------|
| 双栈终端 | 同一 MAC 同时存在 IPv4 与 IPv6 |
| 纯 IPv4 终端 | 同一 MAC 仅存在 IPv4 |
| 纯 IPv6 邻居 | 同一 MAC 仅存在 IPv6 |
| 网络设备 | 具有路由器特征、多 VLAN、多 IPv4、VLANIF 或 trunk 特征 |
| 下游 ND 邻居 | 仅在 IPv6 ND 表中出现，未出现在 ARP 表 |

主指标以 HOST 终端为准：

```text
双栈占比 = 双栈终端数 / 终端总数 * 100%
```

更完整算法说明见：

```text
docs/ALGORITHM.md
```

## 大规模采集设计

系统按 50,000 条 ARP 规模设计：

| 参数 | 值 | 说明 |
|------|----|------|
| 设计 ARP 容量 | 50,000 条 | 面向大型园区核心交换机 |
| ARP 命令最长时间 | 900 秒 | 大表输出可能较慢 |
| IPv6 命令最长时间 | 600 秒 | IPv6 ND 输出超时保护 |
| 自动翻页上限 | 12,000 页 | 处理 `--More--` 分页 |
| 默认 SSH 超时 | 180 秒 | 前端可配置至 900 秒 |
| 设备明细默认分页 | 100 条/页 | 避免前端一次性渲染大结果 |
| 设备明细最大分页 | 500 条/页 | API 侧限制 |

容量配置见：

```text
backend/app/core/scale.py
```

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/vendors` | 获取厂商列表 |
| GET | `/api/vendors/{vendor}/models` | 获取厂商设备型号 |
| GET | `/api/readonly-commands` | 查看只读命令白名单 |
| POST | `/api/survey/jobs` | 创建异步采集任务 |
| GET | `/api/survey/jobs/{job_id}` | 获取任务快照 |
| GET | `/api/survey/jobs/{job_id}/stream` | SSE 订阅任务进度 |
| GET | `/api/survey/jobs/{job_id}/devices` | 分页获取设备明细 |
| POST | `/api/survey/jobs/{job_id}/cancel` | 中断任务 |
| POST | `/api/survey/jobs/{job_id}/resume` | 恢复任务 |
| POST | `/api/survey` | 同步采集接口，兼容旧调用方式 |

## 测试

### 后端测试

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

### 前端构建检查

```bash
cd frontend
npm run build
```

## 安全说明

本项目只执行只读采集操作，不会修改交换机配置。

系统允许的命令类型：

- `display arp`
- `display arp all`
- `display ipv6 neighbors`
- `screen-length 0 temporary`
- `screen-length disable`

其中 `screen-length` 仅用于当前 SSH 会话的分页控制，不写入 startup-config。

以下命令会被拒绝：

- `system-view`
- `save`
- `undo`
- `delete`
- `reboot`
- 非白名单配置命令

## 注意事项

1. 交换机需要开启 SSH。
2. SSH 账号需要具备查看 ARP 和 IPv6 邻居表的权限。
3. 统计结果基于核心交换机 L3 邻居视角，纯 L2 且近期无 ARP/ND 活动的终端可能不会出现。
4. 同一物理终端如果存在多个网卡，会按多个 MAC 计数。
5. NAT、下游路由器、办公室出口设备可能会让终端只能以网络设备或 ND 邻居形式间接呈现。
6. 建议在业务低峰期采集大型 ARP 表，避免分页输出时间过长。

## GitHub 推送

首次推送到 GitHub：

```bash
git remote add origin https://github.com/thankswinter/ipv6-dual-stack-survey.git
git branch -M main
git push -u origin main
```

如果远程仓库已经配置：

```bash
git add .
git commit -m "Update project README"
git push -u origin main
```

GitHub HTTPS 推送不支持账号密码登录，需要使用 Personal Access Token。也可以改用 SSH：

```bash
git remote set-url origin git@github.com:thankswinter/ipv6-dual-stack-survey.git
git push -u origin main
```

## License

未指定 License。如需开源发布，请根据实际使用场景补充许可证文件。
