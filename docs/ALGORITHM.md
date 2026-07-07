# IPv4/IPv6 双栈统计算法

## 1. 问题定义

在核心交换机视角下，需要回答三个问题：

1. 网络中有多少台 **IPv4/IPv6 双栈** 终端？
2. 有多少台 **纯 IPv4** 终端？
3. **双栈设备占终端总数的比例** 是多少？

## 2. 基本假设

| 假设 | 说明 |
|------|------|
| MAC 唯一标识终端 | 一台终端的一个 NIC 对应一个 MAC；同一 MAC 在多个 VLAN/接口出现仍视为 1 台 |
| ARP 表反映 IPv4 可达性 | 交换机 ARP 表记录已知 IPv4 邻居 |
| ND 表反映 IPv6 可达性 | IPv6 邻居表（Neighbor Discovery）记录已知 IPv6 邻居 |
| 核心交换机为汇聚点 | 下联终端的 L3 流量经核心交换路由，其 ARP/ND 表覆盖大部分活跃终端 |

## 3. 数据采集

### 3.1 连接方式

```
SSH → 核心交换机 → 执行厂商 CLI → 解析文本输出
```

### 3.2 CLI 命令

根据设备厂商与型号选择命令（见 `backend/app/cli/templates.py`）：

**华为示例：**
```
screen-length 0 temporary
display arp all
display ipv6 neighbors
```

**H3C 示例：**
```
screen-length disable
display arp
display ipv6 neighbors
```

### 3.3 解析字段

**ARP 表：**
- IPv4 地址
- MAC 地址
- VLAN ID（可选）
- 出接口（可选）

**IPv6 邻居表：**
- IPv6 地址
- MAC 地址
- VLAN ID（可选）
- 出接口（可选）
- 邻居状态（可选）

## 4. 算法流程

```
┌─────────────────┐     ┌─────────────────┐
│   ARP 表解析     │     │ IPv6 邻居表解析  │
│  (IPv4 → MAC)   │     │  (IPv6 → MAC)   │
└────────┬────────┘     └────────┬────────┘
         │                         │
         └──────────┬──────────────┘
                    ▼
         ┌─────────────────────┐
         │  MAC 地址归一化      │
         │  AA:BB:CC:DD:EE:FF  │
         └──────────┬──────────┘
                    ▼
         ┌─────────────────────┐
         │  过滤无效/组播 MAC   │
         └──────────┬──────────┘
                    ▼
         ┌─────────────────────┐
         │  按 MAC 聚合地址集合 │
         │  ipv4_set, ipv6_set │
         └──────────┬──────────┘
                    ▼
         ┌─────────────────────┐
         │  协议栈分类          │
         └──────────┬──────────┘
                    ▼
         ┌─────────────────────┐
         │  统计与比例计算      │
         └─────────────────────┘
```

### 步骤 1：MAC 归一化

将各厂商格式统一：

```
0011-2233-4455  →  00:11:22:33:44:55
00:11:22:33:44:55  →  00:11:22:33:44:55  (大写)
```

### 步骤 2：过滤规则

排除以下 MAC，避免虚增计数：

| 类型 | 前缀/特征 | 原因 |
|------|-----------|------|
| IPv4 组播 | `01:00:5E` | 非终端 |
| IPv6 组播 | `33:33` | 非终端 |
| 广播 | `FF:FF:FF:FF:FF:FF` | 非终端 |
| 无效 IPv6 | `ff00::/8` 组播地址 | 非终端单播 |

### 步骤 3：按 MAC 聚合

对每个有效 MAC 维护：

```
MacInventory {
  mac: string
  ipv4_addresses: Set<string>
  ipv6_addresses: Set<string>
  vlan_ids: Set<int>
  interfaces: Set<string>
}
```

同一 MAC 在 ARP 与 ND 表中出现多次（多 IP、多 VLAN）时，合并到同一 `MacInventory`。

### 步骤 4：协议栈分类

```
if |ipv4_addresses| > 0 AND |ipv6_addresses| > 0:
    → DUAL_STACK（双栈）
elif |ipv4_addresses| > 0:
    → IPV4_ONLY（纯 IPv4）
else:
    → IPV6_ONLY（纯 IPv6）
```

**说明：** 一台双栈终端通常同时有 link-local IPv6（`fe80::/10`）和 global IPv6，以及一个 IPv4 地址。只要 MAC 下存在任意合法 IPv6 地址即视为「有 IPv6」。

### 步骤 5：统计指标

```
N_total     = 唯一 MAC 数量
N_dual      = stack_type == DUAL_STACK 的数量
N_ipv4      = stack_type == IPV4_ONLY 的数量
N_ipv6      = stack_type == IPV6_ONLY 的数量

P_dual      = N_dual / N_total × 100%
P_ipv4      = N_ipv4 / N_total × 100%
P_ipv6      = N_ipv6 / N_total × 100%
```

验证：`N_dual + N_ipv4 + N_ipv6 = N_total`

## 5. 示例

### 输入

**ARP 表：**
| IPv4 | MAC |
|------|-----|
| 10.0.0.1 | 00:11:22:33:44:01 |
| 10.0.0.2 | 00:11:22:33:44:02 |
| 10.0.0.3 | 00:11:22:33:44:03 |

**IPv6 邻居表：**
| IPv6 | MAC |
|------|-----|
| fe80::211:22ff:fe33:4401 | 00:11:22:33:44:01 |
| 2001:db8::2 | 00:11:22:33:44:02 |
| fe80::211:22ff:fe33:4403 | 00:11:22:33:44:03 |

### 分类结果

| MAC | IPv4 | IPv6 | 类型 |
|-----|------|------|------|
| ...:01 | ✓ | ✓ | **双栈** |
| ...:02 | ✓ | ✓ | **双栈** |
| ...:03 | ✓ | ✓ | **双栈** |

### 统计

- 终端总数：3
- 双栈设备：3
- 纯 IPv4：0
- 双栈占比：**100%**

若 ...:03 无 IPv6 邻居条目：

- 双栈：2，纯 IPv4：1
- 双栈占比：**66.67%**

## 6. 精度与局限

### 可提高精度的措施

1. **多核心交换机合并**：对多台核心交换机采集结果按 MAC 去重后统一统计
2. **排除网关 MAC**：将 SVI/网关接口 MAC 加入 `GATEWAY_MAC_HINTS` 白名单过滤
3. **ND 状态过滤**：仅保留 `Reachable` / `Stale` 状态邻居（可选）
4. **定时多次采样**：取多次采集的并集，减少临时离线终端漏计

### 已知局限

| 局限 | 影响 |
|------|------|
| 纯 L2 终端未产生 ARP/ND | 漏计 |
| 多 NIC 终端 | 高估（按 NIC 计数） |
| NAT 后终端 | 可能仅见网关 MAC |
| 静默终端（无近期流量） | ARP/ND 条目老化后漏计 |
| 虚拟机漂移 | 同一 VM 换 MAC 可能重复或漏计 |

## 7. 代码入口

| 模块 | 职责 |
|------|------|
| `backend/app/core/algorithm.py` | 核心算法：`build_mac_inventory`、`compute_statistics` |
| `backend/app/collectors/huawei.py` | 华为 CLI 输出解析 |
| `backend/app/collectors/h3c.py` | H3C CLI 输出解析 |
| `backend/app/cli/templates.py` | 型号 → CLI 命令映射 |
