# 接入工具层（Access Layer）

这一层解决的问题是：**agent 不应该直接拼 HTTP、MQTT、Matter 或 Zigbee 协议**。它只需要看到“设备 + 能力 + 风险”，通过一个统一的 `iot_call` 执行；协议适配、参数校验、策略和审计都在工具层完成。

## 1. 架构位置

```
 MCP client / LLM / CLI / HTTP dashboard
                  │
                  ▼
        ┌─────────────────────┐
        │      AccessHub      │
        │  resolve → validate │
        │  policy → adapter   │
        │  audit → result     │
        └─────────┬───────────┘
                  │
       DeviceBinding + Capability
                  │
     ┌────────────┼───────────────┬──────────────┐
     ▼            ▼               ▼              ▼
 SimAdapter   HomeAssistant   MqttAdapter   Zigbee2MqttAdapter
 (虚拟端点)   (REST 桥)       (可选 paho)    (Zigbee2MQTT)
```

代码位置：

- `southbound/access/models.py` — `DeviceBinding`、`Capability`、`Risk`
- `southbound/access/capabilities.py` — 能力定义、参数校验、工具目录
- `southbound/access/policy.py` — 策略引擎
- `southbound/access/audit.py` — JSONL 审计
- `southbound/access/hub.py` — 统一执行入口
- `southbound/access/adapters/` — 协议适配器
- `southbound/access/config.py` — 从 JSON 配置装配 adapter 与 policy

## 2. 核心模型

### DeviceBinding

一台设备在工具层里的投影：

```json
{
  "device_id": "living_light",
  "name": "客厅主灯",
  "kind": "light",
  "room": "客厅",
  "protocol": "Matter",
  "tier": "T1",
  "adapter": "sim",
  "address": {"sim_id": "living_light"},
  "capabilities": [
    {"name": "turn_on", "risk": "low"},
    {"name": "set_brightness", "risk": "low"}
  ],
  "metadata": {"virtual": true, "safe_to_automate": true}
}
```

- `device_id` 必须在所有 adapter 之间唯一。
- `address` 是 adapter 私有字段（实体 ID、topic、串口等），默认不暴露给 MCP。
- `metadata.safe_to_automate` 是高风险自主执行的硬条件。

### Capability

能力是 agent 能调用的最小动作：

```json
{
  "name": "set_brightness",
  "description": "设置亮度并打开灯",
  "risk": "low",
  "parameters": {
    "brightness": {"type": "integer", "minimum": 1, "maximum": 100}
  },
  "required": ["brightness"]
}
```

内置能力按设备类型定义：

| kind | capabilities | 风险 |
|---|---|---|
| `light` | turn_on / turn_off / toggle / set_brightness / read | low / read |
| `plug` | turn_on / turn_off / toggle / read | low / read |
| `cover` | open / close / stop / set_position / read | low / read |
| `sensor` | read | read |
| `binary_sensor` | read | read |
| `purifier` | turn_on / turn_off / toggle / set_mode / read | low / read |
| `thermostat` | set_temperature / read | medium / read |
| `lock` | lock / unlock / read | **high** / read |

新增设备类型只需在 `capabilities.py` 增加定义，adapter 实现对应执行。

### Tool Catalog

`AccessHub.catalog()` 把所有绑定聚合成 agent 可读的能力目录：

```json
{
  "name": "light.set_brightness",
  "kind": "light",
  "capability": "set_brightness",
  "risk": "low",
  "parameters": {"brightness": {"type": "integer", "minimum": 1, "maximum": 100}},
  "required": ["brightness"],
  "devices": ["living_light"]
}
```

Agent 先读 catalog 或 `iot_devices_list`，再用 `iot_call` 执行。

## 3. 执行流程

`AccessHub.call(device_id, capability, arguments, confirm)`：

1. 查找 binding；找不到 → `UnknownDevice`。
2. 查找 capability；找不到 → `UnknownCapability`。
3. `validate_arguments`：类型、必填、范围、枚举、未知参数。
4. `PolicyEngine.decide`：房间/类型/adapter 范围、只读模式、高风险确认。
5. 找到 adapter；不可用 → `AdapterUnavailable`。
6. 调用 adapter，异常统一包装。
7. 成功/失败都写审计；成功返回 `{ok, device_id, capability, risk, adapter, result}`。

失败不会静默：策略拒绝、参数错误、adapter 不可用都会返回结构化错误，并带 `requires_confirmation` 标记（如果适用）。

## 3.1 运行时与事件流

`SouthboundRuntime`（`southbound/runtime.py`）组合了模拟器、`AccessHub`、审计和 `EventBus`：

- **northbound**：`intent(goal)` 把目标拆成动作；`call(device_id, capability)` 执行单个能力。
- **southbound**：`AccessHub` 选择 adapter 并执行。
- **事件**：
  - `intent`：`plane=northbound`, `direction=in`
  - `command`：`plane=southbound`, `direction=down`
  - `state`：`plane=northbound`, `direction=up`
  - `intent.result`：`plane=northbound`, `direction=out`
  - `commission.request` / `commission` / `commission.result`：受控接入握手
  - `decommission` / `decommission.result`：撤销接入
- **SSE**：`GET /api/events` 实时推送；`GET /api/events/recent?since=<id>` 支持重放。
- **Dashboard**：顶部 flowbar 按 `plane` 在 Agent—Southbound 与 Southbound—Devices 两段上打点，显示最新事件和计数。

运行时默认进程内；`southbound serve` 可作为共享 runtime service，Dashboard 使用它，MCP/CLI 可通过 `SOUTHBOUND_REMOTE_URL` 连接同一个设备图。见 [共享 Runtime Service](RUNTIME_SERVICE.md)。

### 受控 onboarding

`commission()` / `decommission()` 把“扩散”限制为受控接入：

1. 发现/提交设备配置；
2. 校验 adapter、device_id 与能力白名单；
3. `PolicyEngine.decide_commission()` 检查范围、`confirm` 和高风险能力；
4. adapter 验证/添加设备；
5. `AccessHub.register_binding()` 加入设备图与 Tool Catalog；
6. 写审计、发事件、可选写入 `CommissionStore`；
7. 重启后从 `data/commissioned.json` 恢复（不含 token/password）。

完整流程、请求示例和安全边界见 [ONBOARDING.md](ONBOARDING.md)。

## 4. 策略

配置在 `policy` 段：

```json
{
  "mode": "supervised",
  "allow_high_risk": false,
  "allow_auto_commission": false,
  "allowed_rooms": null,
  "allowed_kinds": null,
  "allowed_adapters": null
}
```

| 模式 | 行为 |
|---|---|
| `read_only` | 只允许 `risk=read` |
| `supervised` | read/low/medium 自动放行；high 需要 `allow_high_risk=true` + `confirm=true` |
| `autonomous` | read/low/medium 自动放行；high 需要 `allow_high_risk=true` 且设备 `safe_to_automate=true` |

高风险动作（门锁、电源、燃气）默认**不可用**。即使用户要求“全智能”，工具层也不会让 LLM 直接开锁。

`allowed_rooms` / `allowed_kinds` / `allowed_adapters` 是范围限制，适合把 agent 限定在实验室或客厅。

设备接入由 `decide_commission()` 单独判断：

- `read_only`：禁止接入；
- `supervised`：必须 `confirm=true`；
- `autonomous`：默认仍必须 `confirm=true`；只有 `allow_auto_commission=true` 才允许自动接入；
- 请求包含高风险能力（如 `unlock`）时，还要求 `allow_high_risk=true`；
- 未显式指定 capabilities 时，默认只授予非 high-risk 能力。

## 5. 适配器

| Adapter | 状态 | 适用 |
|---|---|---|
| `sim` | 完整实现 | 无硬件演示、测试策略与工具层 |
| `home_assistant` | 完整实现（REST） | Matter / Zigbee / ESPHome / 厂商设备先接入 HA，再由本层调用 |
| `zigbee2mqtt` | 已实现，需 `paho-mqtt` | Zigbee 设备经 Zigbee2MQTT |
| `mqtt` | 已实现，需 `paho-mqtt` | ESPHome/Tasmota/自定义 MQTT 设备 |
| `ble` | 已实现，需 `bleak`；待硬件验收 | GATT 读写、通知、显式模拟模式 |
| `nfc` | 已实现，需 `nfcpy`；待硬件验收 | 标签 UID/NDEF、刷卡事件、显式模拟模式 |
| ESPHome 原生 API | 未实现 | 推荐走 HA 或 MQTT |
| Matter 原生 controller | 未实现 | 推荐走 HA 的 Matter 集成 |

安装 MQTT 可选依赖：

```bash
pip install -e ".[mqtt]"
```

Home Assistant 适配器只需要 `base_url` 和 token；token 从环境变量读取，不写进仓库：

```bash
export HA_TOKEN="..."
```

## 6. 配置

从示例复制：

```bash
cp config/access.example.json config/access.local.json
```

`config/access.local.json` 已在 `.gitignore` 中。默认只启用 `sim`；HA / Zigbee2MQTT / MQTT 默认关闭。

一份 HA 设备配置：

```json
{
  "device_id": "ha:light.living_room",
  "name": "客厅灯",
  "kind": "light",
  "entity_id": "light.living_room",
  "room": "客厅",
  "tier": "T1",
  "protocol": "Matter",
  "capabilities": ["turn_on", "turn_off", "toggle", "set_brightness", "read"],
  "safe_to_automate": false
}
```

## 7. Agent 接口

### MCP

`southbound mcp` 暴露：

| 工具 | 作用 |
|---|---|
| `iot_hub_status` | 策略、adapter、设备数、最近审计 |
| `iot_devices_list` | 所有设备的 capabilities |
| `iot_device_describe` | 单设备详情 |
| `iot_tool_catalog` | 跨设备能力目录 |
| `iot_call` | 策略检查 + 审计后执行 |
| `iot_commission` | 受控接入新设备（默认需要 confirm） |
| `iot_decommission` | 移除已接入设备（默认需要 confirm） |

`iot_call` 参数：

```json
{
  "device_id": "living_light",
  "capability": "set_brightness",
  "arguments": {"brightness": 30},
  "confirm": false
}
```

### CLI

```bash
python3 -m southbound intent "打开客厅灯并关掉书房插座"
python3 -m southbound onboard --adapter sim --id demo_sensor --confirm
python3 -m southbound decommission demo_sensor --confirm
python3 -m southbound serve
python3 -m southbound tools
python3 -m southbound call living_light set_brightness --args '{"brightness": 30}'
python3 -m southbound access
```

高风险动作：

```bash
python3 -m southbound call ha:lock.front_door unlock --confirm
```

（还需要 policy 开启 `allow_high_risk`，否则仍会被拒绝。）

### HTTP（dashboard）

- `GET /api/northbound/status`
- `GET /api/northbound/devices`
- `GET /api/northbound/catalog`
- `POST /api/northbound/intent`
- `POST /api/northbound/call`
- `POST /api/northbound/commission`
- `POST /api/northbound/decommission`
- `GET /api/events`（SSE）
- `GET /api/events/recent?since=<id>`
- `GET /api/access/status` 等旧路径保留为别名
- `GET /api/access/audit`
- `POST /api/access/call`

面板的模拟端点操作和 agent 控制台动作都经 `SouthboundRuntime` → `AccessHub`，因此会经过策略、审计并产生事件。

## 8. 扩展一个新 adapter

1. 继承 `DeviceAdapter`，实现 `bindings()` 和 `call()`。
2. `bindings()` 用 `capabilities_for(kind, allowed)` 生成能力列表。
3. `call()` 只做协议执行，策略和审计不要重复实现。
4. 在 `access/registry.py` 的 `REGISTRY` 注册 `AdapterSpec`（工厂、类型与接入字段），配置、CLI、MCP 和表单共享注册信息。
5. 在 `config/access.example.json` 增加一段示例。
6. 有主动上报时通过 `emit(device_id, event_type, data)` 发送设备事件，实现 `close()` 释放后台连接。
7. 加测试：至少覆盖 bindings、参数校验、成功调用、失败路径与资源释放。

示例骨架：

```python
class MyAdapter(DeviceAdapter):
    name = "my_protocol"

    def bindings(self):
        return [DeviceBinding(..., adapter=self.name, capabilities=capabilities_for("light"))]

    def call(self, binding, capability, arguments):
        if capability.name == "read":
            return {"state": self._read(binding)}
        return {"state": self._write(binding, capability, arguments)}
```

## 9. 审计与安全

审计写入 `data/audit.jsonl`，每条包含：

```json
{
  "ts": "2026-10-26T13:55:54+08:00",
  "device_id": "living_light",
  "capability": "set_brightness",
  "risk": "low",
  "arguments": {"brightness": 35},
  "policy_mode": "supervised",
  "confirm": false,
  "ok": true,
  "result": {"state": {"on": true, "brightness": 35}},
  "error": ""
}
```

安全边界：

- Dashboard 和 MCP 默认只绑定本机；不要暴露到公网。
- 高风险动作需要 policy 开关和确认；`autonomous` 还要求设备标记 `safe_to_automate`。
- Token/密码只从环境变量读取，不写入配置示例。
- 所有工具调用都审计；审计文件不应包含 token。

## 10. 完备性评估

**已经具备的“完整接入工具层”要素：**

- 统一的设备/能力模型；
- 能力目录与 JSON Schema 参数校验；
- 策略引擎与高风险硬门；
- 审计日志；
- 模拟器、Home Assistant、MQTT、Zigbee2MQTT、BLE、NFC 六类 adapter；后两者已完成模拟与库边界测试，待真实硬件验收，见 [硬件适配器](HARDWARE_ADAPTERS.md)；
- MCP、CLI、HTTP 三种入口；
- SouthboundRuntime、EventBus、SSE 事件流与面板 flowbar；
- 受控 onboarding/commissioning、持久化与撤销；
- 无硬件闭环与可视化；
- 测试覆盖核心路径。

**仍是扩展点：**

- Matter 原生 controller（目前经 HA）；
- ESPHome 原生 protobuf API（目前经 HA/MQTT）；
- 设备侧事件订阅（MQTT 已缓存 state，但尚未做完整事件流）；
- 共享 runtime 的认证/TLS 与多用户权限（`southbound serve` 已可共享设备图）；
- commissioning 的隔离区/审批 UI 与从扫描结果一键预填；
- 设备自动发现到 adapter 配置的自动生成；
- 真实硬件上的延迟与可靠性数据。

结论：作为 **IoT 全智能 agent 的接入工具层骨架**，它是完整的；作为**生产级控制系统**，还缺设备侧事件流、远程认证和现场验证；共享 runtime service 已具备本地模式。

## 11. 下一步

1. 用模拟器验证策略与审计，再上真实 ESP32/Matter。
2. 接 Home Assistant 后，把 Matter/Zigbee 设备统一经 HA adapter 接入。
3. 加 Zigbee2MQTT MQTT 事件订阅，把 `read` 从轮询变成事件缓存。
4. 为高风险设备单独设计确认流程（人工、双人、时间窗）。
5. 最后再接 LLM planner；工具层保持不变。
