# 技术架构与可行性评估

## 1. 目标与边界

这套工具链要解决的是：**在没有完整智能家居硬件的情况下，把“发现 → 分级 → 编排 → 回读”做成一条可运行、可观察、可替换的链路**，并在硬件到货后平滑接入。

明确不做：

- 未授权扫描、认证绕过、漏洞利用或自传播；
- 依赖厂商云的强制在线架构；
- 用 LLM 直接控制门锁、燃气、总电源等危险执行器。

## 2. 工具链总览

```
                         ┌──────────────────────────────┐
  LAN ──┬─ ARP ─────────▶│ discover.py                  │──▶ data/inventory.json
        ├─ mDNS / SSDP ──┤ 主机发现 + mDNS 解析         │
        └─ TCP / banner ─▶│ deep_scan.py                 │──▶ data/deep_scan.json
                         └──────────────┬───────────────┘
                                        ▼
                              registry.py
                        厂商 OUI + 服务 + 端口规则
                        T0 / T1 / T2 / T3 分级
                                        │
            ┌───────────────────────────┼───────────────────────────┐
            ▼                           ▼                           ▼
      agent.py                  mcp_server.py                viz_server.py
   inventory 问答 / 方案      stdio MCP（13 tools）        HTTP API + 可视化面板
            │                           │                           │
            └───────────────┬───────────┘                           ▼
                            ▼                                 southbound/web/
                   外部 LLM / MCP 客户端                     index.html / app.js / styles.css
                            │
                            ▼
                 ┌─────────────────────────────┐
                 │    SouthboundRuntime        │  ← intent / call / EventBus
                 │  ┌───────────────────────┐  │
                 │  │      AccessHub        │  │  ← 策略 / 审计 / 能力目录
                 │  └───────────────────────┘  │
                 └─────────────┬───────────────┘
                           │
        ┌──────────────────┼────────────────────┐
        ▼                  ▼                    ▼
   SimAdapter     HomeAssistantAdapter    MqttAdapter /
   虚拟端点        REST 桥（Matter/Zigbee/  Zigbee2MqttAdapter
                   ESPHome 经 HA）
        │
        ▼
  simulator.py（六个虚拟端点）
```

统一入口是 `python3 -m southbound`（安装后命令为 `southbound`）：

```bash
southbound discover --sweep
southbound scan --sweep
southbound agent "哪些设备能跑 agent"
southbound sim "打开客厅灯并关掉书房插座"
southbound tools
southbound call living_light set_brightness --args '{"brightness": 30}'
southbound access
southbound demo
southbound mcp
```

## 3. 组件职责

| 组件 | 职责 | 当前状态 |
|---|---|---|
| `discover.py` | ARP + mDNS + SSDP，支持 `/23` 等非 `/24` 网段；mDNS 实例解析到 IP | 可用 |
| `deep_scan.py` | TCP 端口、HTTP/Telnet/SSH/SMB/RTSP/UPnP 指纹；支持定向 `--hosts` | 可用 |
| `registry.py` | IEEE OUI 缓存、厂商/服务/协议规则、T0–T3 分级 | 可用，启发式 |
| `agent.py` | 基于 inventory 的确定性问答与接入方案 | 可用 |
| `simulator.py` | 六个虚拟设备（灯、插座、温湿度、窗帘、门磁、净化器），三种协议 | 可用 |
| `sim_agent.py` | 目标拆分 → 设备匹配 → 动作 → 回读，输出可展示的 trace | 可用 |
| `viz_server.py` | 本地面板 HTTP API，绑定 `127.0.0.1`；模拟器操作与 agent demo 经 AccessHub | 可用 |
| `runtime.py` | SouthboundRuntime + EventBus：northbound intent/call、southbound adapter 管线、SSE 事件流 | 可用 |
| `mcp_server.py` | stdio MCP，13 个工具：发现、深扫、模拟器、接入层 | 可用 |
| `cli.py` | 统一 CLI，含 `tools` / `call` / `access` | 可用 |
| `access/` | 设备/能力模型、参数校验、策略、审计、AccessHub、四类 adapter | 可用 |
| `config/access.example.json` | HA / MQTT / Zigbee2MQTT 配置示例 | 可用 |
| `southbound/web/` | 观测站风格可视化面板 | 可用 |
| `tests/` | 标准库 unittest，覆盖分级、模拟闭环、接入层策略与审计、运行时/事件总线 | 40 项通过 |

### 接入工具层

`AccessHub` 是 agent 的唯一执行入口：`iot_call(device_id, capability, arguments, confirm)`。

- **DeviceBinding**：设备在工具层中的投影，含 kind、room、tier、adapter、address。
- **Capability**：最小可调用动作，带 JSON Schema、风险等级和返回值。
- **PolicyEngine**：`read_only` / `supervised` / `autonomous`；高风险动作需要 `allow_high_risk`，监督模式还需 `confirm`，自主模式还需 `safe_to_automate`。
- **AuditLog**：每次调用（含拒绝与参数错误）写 `data/audit.jsonl`。
- **Adapters**：`sim`、`home_assistant`、`mqtt`、`zigbee2mqtt`；ESPHome/Matter 原生协议建议经 HA/MQTT。
- **Tool Catalog**：把跨设备能力聚合为 agent 可读的目录。

详细契约、配置、扩展指南与完备性评估见 [ACCESS_LAYER.md](ACCESS_LAYER.md)。

### 运行时与事件流

`SouthboundRuntime` 把 AccessHub、模拟器、审计和事件总线组合成一个运行时：

- **northbound**：`intent(goal)` 把 agent 目标拆成动作；`call()` 执行单个能力；
- **southbound**：由 `AccessHub` 选择 adapter 并执行；
- **EventBus**：每次执行发出 `intent`、`command`（southbound/down）、`state`（northbound/up）和 `intent.result` 事件；
- **SSE**：`GET /api/events` 实时推送，`GET /api/events/recent?since=` 支持重放；
- **面板 flowbar**：Agent → Southbound → Devices 的实时脉冲与最新事件。

运行时目前是进程内的。Dashboard、CLI、MCP 各自嵌入一个；需要多客户端共享状态时，下一阶段可以把同一组 HTTP 端点抽成独立 service。

## 4. 数据契约

### inventory.json

```json
{
  "generated_at": "2026-10-26T12:00:00+08:00",
  "host": {
    "interface": "en0",
    "local_ip": "192.168.1.50",
    "gateway": "192.168.1.2",
    "cidr": "192.168.1.0/24",
    "hostname": "Demo Mac",
    "cpu": "Apple M4"
  },
  "devices": [
    {
      "ip": "192.168.1.40",
      "mac": "02:00:00:00:00:40",
      "vendor": "Espressif Inc.",
      "sources": ["arp", "mdns"],
      "services": ["_esphomelib._tcp"],
      "analysis": {
        "kind": "ESP32 系列 MCU",
        "agent_tier": "T1",
        "chip_hint": "ESP32 / ESP8266（视型号）",
        "control_paths": ["ESPHome / MQTT / 自定义固件"],
        "data_paths": ["MQTT 遥测", "ESPHome 原生 API"],
        "confidence": "medium",
        "notes": "可跑唤醒词/意图/异常检测等微型模型。"
      }
    }
  ],
  "summary": {"device_count": 5, "by_tier": {"T0": 2, "T1": 1, "T2": 1, "T3": 1}}
}
```

### deep_scan.json

```json
{
  "hosts": [
    {
      "ip": "192.168.1.20",
      "vendor": "Demo Storage",
      "open_ports": [22, 80, 139, 443, 445, 5000, 8088],
      "identity": "Demo NAS",
      "netbios": "DEMO-NAS",
      "http": [{"port": 5000, "status": 200, "title": "Demo-NAS - Demo NAS"}],
      "exposures": ["SMB 开放", "DNS 服务"]
    }
  ]
}
```

### 模拟器设备

```json
{
  "id": "living_light",
  "name": "客厅主灯",
  "kind": "light",
  "room": "客厅",
  "protocol": "Matter",
  "tier": "T1",
  "state": {"on": true, "brightness": 60, "power_w": 5.4},
  "capabilities": ["turn_on", "turn_off", "toggle", "set_brightness"]
}
```

### Agent trace

每次执行返回 `{goal, ok, summary, steps[], affected[]}`。每个 step 包含 `n, title, detail, tool, args, result`，面板按顺序渲染；这是“工具调用循环”的可观察面。

## 5. 部署形态

- **本机 / NAS**：Python 3.10+，无第三方依赖；面板绑定 `127.0.0.1`，默认不对外。
- **数据**：`data/inventory.json`、`data/deep_scan.json` 是本地生成物，已在 `.gitignore` 中排除。
- **HA / MQTT / Matter**：硬件阶段再引入。推荐 NAS 上跑 Home Assistant Container + Mosquitto + Zigbee2MQTT + Matter Server；面板保持为观测与实验层。
- **网络**：实验室设备放独立 IoT SSID/VLAN，编排主机是唯一跨界的执行点。

## 6. 技术可行性矩阵

| 能力 | 当前实现 | 可行性 | 关键瓶颈 | 结论 |
|---|---|---|---|---|
| 局域网发现 | ARP + mDNS + SSDP | 高 | 客户端隔离、多播被屏蔽、休眠设备 | 平坦网段可靠；跨 VLAN 需额外部署 |
| 设备指纹 | 端口 + banner + OUI | 中高 | 随机 MAC、厂商私有协议、设备休眠 | 足够做接入规划；精确识别需 DHCP/DHCPv6 租约 |
| T0–T3 分级 | 规则引擎 | 中 | 未知厂商、型号歧义 | 作为规划启发式可用，需标注置信度 |
| 接入工具层 | AccessHub + 四类 adapter | 高 | 真实设备鉴权、协议差异 | 模型/策略/审计/目录已完成；协议执行按 adapter 扩展 |
| 运行时与事件流 | SouthboundRuntime + EventBus + SSE | 高 | 跨进程一致性、背压 | 单进程闭环已跑通；独立 service 是下一步 |
| ESP32 / ESPHome 控制 | Sim 完整；真实设备经 HA/MQTT adapter | 高（有硬件） | 刷机、引脚、供电 | 最小闭环最成熟的一条路 |
| Matter 控制 | 经 HomeAssistantAdapter | 中高 | 需 Matter Server + 蓝牙配网 | 标准协议，长期成本最低 |
| Zigbee 控制 | 经 Zigbee2MqttAdapter | 高（有协调器） | 协调器、网络密钥、USB 干扰 | SLZB-06 + zigbee2mqtt 成熟 |
| 封闭生态（Hue/Aqara/Tuya/Apple） | 识别 + HA 桥接 | 中低 | 本地 API 限制、云 token、ToS | 逐设备适配，不能指望一套打穿 |
| LLM planner | 确定性 sim_agent + 工具目录 | 中高 | 工具调用可靠性、延迟、幻觉 | 3B 以上可用；必须保留策略与审计 |
| “所有硬件”完整自主 | 不支持 | 低（字面） | 凭据、覆盖率、安全 | 预接入集合内可达 L3；全网无授权接管不可行 |
| 可视化 | 已实现 | 高 | 数据新鲜度 | 适合观测与演示 |
| MCP 外部 agent | 已实现（13 tools） | 高 | 客户端批准、权限 | 工具边界清晰，可逐步扩展 |
| 安全关键控制 | 策略/审计已实现，硬件未验证 | 中 | 门锁/电源/燃气的现场验证 | 工具层已强制高风险门；仍需现场演练 |

## 7. 关键架构决策

1. **本地优先**：发现、分级、模拟、面板都不依赖云；MCP 也走本机 stdio。
2. **模拟器是一等公民**：没有硬件也能开发 agent 循环；虚拟设备的状态与动作接口刻意贴近真实协议语义。
3. **工具而不是提示词**：设备能力通过结构化工具暴露（HTTP API / MCP），LLM 只负责选择与参数。
4. **确定性优先**：当前 agent 是规则/关键词驱动的，可复现、可测试；LLM planner 是可选增强，不是唯一路径。
5. **数据与代码分离**：扫描结果与 OUI 缓存放 `data/`，不进仓库；文档放 `docs/`。

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 未授权扫描/接入 | 法律与信任风险 | 只扫自有网络；面板与文档明确边界；不做认证尝试 |
| 扫描误判 | 接入优先级错误 | 展示置信度；用 DHCP 租约与 mDNS 名称交叉验证 |
| 多播/客户端隔离 | 发现率低 | 支持 DHCP 租约导入、定向扫描、ARP 表 |
| 厂商云锁定 | 无法本地控制 | 优先选 Matter/Zigbee/ESPHome；云设备单独评估 |
| 固件/硬件差异 | 刷机失败、协议不一致 | 采购清单给替代型号；先小批量验证 |
| LLM 不确定性 | 错误动作 | 危险动作走确定性规则 + 人工确认；全量审计 |
| 隐私 | 抓包/摄像头数据 | 默认只收元数据；采集前确认授权与保留期限 |

## 9. 路线图

- **P0 发现与分级（已完成）**：ARP/mDNS/SSDP、深扫、OUI、T0–T3、MCP。
- **P1 无硬件闭环与接入层（已完成）**：六设备模拟器、确定性 agent trace、可视化面板、统一 CLI、接入工具层（模型/策略/审计/四类 adapter）、SouthboundRuntime/EventBus/SSE、测试。
- **P2 第一条真实链路**：ESP32-S3 + ESPHome + 一个 Matter Wi-Fi 插座；HA/MQTT 落在 NAS；先经 HA/MQTT adapter 接入。
- **P3 Zigbee 与事件流**：SLZB-06 + zigbee2mqtt；把轮询改成 MQTT 事件订阅。
- **P4 LLM planner**：在 sim_agent 的工具层上接本地 3B 模型；加策略引擎、审计与回滚。
- **P5 生产化**：IoT VLAN、认证、多用户、打包与升级。

## 10. 结论

作为**本地工具链**，这套架构是可行的：发现、分级、模拟、可视化、MCP、接入工具层、运行时与事件流都已在当前仓库跑通；真实控制只差协议适配与硬件。

作为**字面意义上的“自主扩散到所有硬件”**，不可行也不应做：瓶颈不是模型智力，而是授权、凭据、协议覆盖和安全边界。

当前项目最值得继续投入的方向不是继续扩大扫描范围，而是：

1. 用模拟器和 AccessHub 把 agent 的工具层、策略层、审计层做扎实；
2. 接一颗 ESP32-S3 和一个 Matter 插座，验证真实延迟与回读；
3. 再加 Zigbee 协调器，把“事件驱动”补上；
4. 最后才考虑 LLM planner 与多用户/远程访问。
