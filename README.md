# iot-agent-lab

一个本地优先的 IoT agent 工具链：发现局域网设备、判断端侧 agent 能力、通过统一的接入工具层控制设备，并在没有硬件时用模拟端点跑通闭环。

- **发现**：ARP + mDNS + SSDP，支持 `/23` 等非 `/24` 网段，mDNS 实例解析到 IP。
- **分级**：厂商 OUI、服务类型、端口指纹，映射到 T0–T3 agent 档位。
- **接入工具层**：统一设备/能力模型、JSON Schema 参数校验、风险策略、审计日志、Tool Catalog。
- **适配器**：模拟器、Home Assistant REST、MQTT、Zigbee2MQTT。
- **模拟与可视化**：六个虚拟端点、确定性 agent trace、观测站风格面板。
- **MCP**：13 个工具，覆盖发现、接入层查询、`iot_call` 执行与模拟器控制。
- **文档**：架构可行性、接入层、芯片能力、信号采集、硬件采购与实测报告。

## 快速开始

```bash
cd iot-agent-lab
python3 iotlab.py discover --sweep
```

```bash
python3 iotlab.py sim "打开客厅灯并关掉书房插座"
```

```bash
python3 iotlab.py tools
```

```bash
python3 iotlab.py call living_light set_brightness --args '{"brightness": 30}'
```

```bash
python3 iotlab.py demo
```

面板默认在 `http://127.0.0.1:8765/`。无需安装依赖，Python 3.10+ 即可；也可以：

```bash
pip install -e .
iot-lab demo
```

MQTT 适配器需要可选依赖：

```bash
pip install -e ".[mqtt]"
```

## 接入工具层

Agent 不直接拼协议，而是调用统一工具：

```text
iot_call(device_id, capability, arguments, confirm)
```

`AccessHub` 负责：

1. 解析设备与能力；
2. 校验参数（类型、范围、必填、未知字段）；
3. 执行策略（只读 / 监督 / 自主；高风险硬门）；
4. 分派给 adapter；
5. 写入审计日志；
6. 返回结构化状态。

示例：

```bash
python3 iotlab.py call living_light set_brightness --args '{"brightness": 30}'
python3 iotlab.py call living_light read
python3 iotlab.py access
```

MCP 中的对应工具是 `iot_call`；先调用 `iot_tool_catalog` 或 `iot_devices_list` 查看可用能力。

高风险动作（门锁、电源、燃气）默认被拒绝：

- `supervised` 模式需要 `allow_high_risk=true` + `confirm=true`；
- `autonomous` 模式还要求设备 `safe_to_automate=true`。

配置从示例复制：

```bash
cp config/access.example.json config/access.local.json
export HA_TOKEN="..."
```

`config/access.local.json` 已被 gitignore。完整说明见 [docs/ACCESS_LAYER.md](docs/ACCESS_LAYER.md)。

## 可视化面板

`python3 iotlab.py demo` 启动只绑定 `127.0.0.1` 的本地服务：

- **网络拓扑**：网关、AP、NAS、打印机、ESP32、Mac 与终端集群，按 T0–T3 着色；点击节点筛选设备。
- **设备清单**：49 台实测设备的搜索、档位过滤、端口与详情。
- **模拟端点**：灯、插座、温湿度、窗帘、门磁、净化器；操作经 `AccessHub` 走策略与审计。
- **Agent 控制台**：输入目标，展示“解析 → 匹配 → 执行 → 回读”的 trace。
- **工具层状态**：左侧显示设备数、能力数与策略模式。

![局域网观测站面板](docs/dashboard.jpg)

面板读取 `data/inventory.json` 和 `data/deep_scan.json`；缺失时仍可运行模拟器部分。

## 工具链命令

| 命令 | 作用 |
|---|---|
| `iot-lab discover --sweep` | 发现设备并写入 `data/inventory.json` |
| `iot-lab scan --sweep` | 端口扫描（大网段建议 `--hosts` 定向） |
| `iot-lab scan --hosts 192.168.1.2,192.168.1.20 --full` | 只扫关键设备 |
| `iot-lab agent "哪些设备能跑 agent"` | 基于 inventory 的问答与接入方案 |
| `iot-lab sim "打开客厅灯"` | 对模拟端点执行目标并打印 trace |
| `iot-lab tools` | 接入层能力目录 |
| `iot-lab call <device> <capability>` | 通过策略与审计执行能力 |
| `iot-lab access` | 适配器、策略与审计状态 |
| `iot-lab demo` | 启动可视化面板 |
| `iot-lab mcp` | 启动 stdio MCP server |
| `iot-lab info` | 工具链地图 |

## 仓库结构

```
iot-agent-lab/
├── iot_agent_lab/
│   ├── cli.py               # 统一 CLI
│   ├── discover.py          # ARP / mDNS / SSDP
│   ├── deep_scan.py         # 端口与服务指纹
│   ├── registry.py          # OUI 与 T0–T3
│   ├── agent.py             # inventory 问答
│   ├── simulator.py         # 六个虚拟端点
│   ├── sim_agent.py         # 模拟器上的确定性 agent
│   ├── viz_server.py        # 面板 HTTP API
│   ├── mcp_server.py        # stdio MCP
│   ├── paths.py             # 数据/配置/文档路径
│   ├── access/
│   │   ├── models.py        # DeviceBinding / Capability / Risk
│   │   ├── capabilities.py  # 能力定义、校验、目录
│   │   ├── policy.py        # 策略引擎
│   │   ├── audit.py         # JSONL 审计
│   │   ├── hub.py           # AccessHub
│   │   ├── config.py        # 配置装配
│   │   └── adapters/
│   │       ├── base.py
│   │       ├── sim.py
│   │       ├── home_assistant.py
│   │       └── mqtt.py
│   └── web/                 # 观测站面板
├── config/
│   └── access.example.json
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ACCESS_LAYER.md
│   ├── CHIPS.md
│   ├── SIGNALS.md
│   ├── HARDWARE.md
│   └── SCAN_REPORT.md
├── tests/
├── data/                    # 扫描结果与审计（本地生成，已 gitignore）
├── iotlab.py                # CLI 兼容入口
├── viz_server.py            # 面板兼容入口
├── mcp_server.py            # MCP 兼容入口
├── pyproject.toml
└── Makefile
```

## MCP

项目包含 `.mcp.json`：

```bash
claude mcp add --scope project iot-lab -- python3 /Users/alexyu/Documents/AI/大肥鱼/iot-agent-lab/mcp_server.py
```

工具：

- 发现与方案：`lan_inventory`、`device_details`、`signal_paths`、`integration_plan`、`deep_scan_lan`
- 模拟器：`sim_devices`、`sim_action`、`sim_goal`
- 接入层：`iot_hub_status`、`iot_devices_list`、`iot_device_describe`、`iot_tool_catalog`、`iot_call`

首次使用需要在 Claude Code 中批准该 server。

## 测试

```bash
make test
```

当前 20 项标准库测试覆盖分级规则、模拟器、agent 目标拆分、能力校验、策略、审计和工具目录。

## 文档

- [文档索引](docs/README.md)
- [技术架构与可行性](docs/ARCHITECTURE.md)
- [接入工具层](docs/ACCESS_LAYER.md)
- [端侧芯片能力](docs/CHIPS.md)
- [数据信号采集](docs/SIGNALS.md)
- [最小采购与刷机清单](docs/HARDWARE.md)
- [最近一次局域网扫描报告](docs/SCAN_REPORT.md)

## 边界

- 只用于自己拥有或获授权的网络；扫描、嗅探、接入他人设备可能违法。
- 不做漏洞利用、认证绕过或自传播。
- 面板与 MCP 默认只在本机；不要把控制接口暴露到公网。
- 真实门锁、电源、燃气等执行器必须走策略 + 确认；LLM 不能直接绕过 `AccessHub`。
