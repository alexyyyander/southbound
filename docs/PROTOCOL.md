# Protocol & API

版本：`0.10.0`。这是一份面向实现的协议说明：北向 agent 如何调用 Southbound，南向 adapter 如何执行，事件长什么样。

## 1. 两个平面

```text
Agent / MCP / LLM
        │ northbound: intent / call / commission
        ▼
   SouthboundRuntime
        │ southbound: adapter → device
        ▼
Wi-Fi / Zigbee / Matter / ESPHome / MQTT
```

- **northbound**：面向 agent 的接口，接受目标、能力调用、设备接入。
- **southbound**：面向设备的接口，由 adapter 实现。
- 同名方向也出现在事件里：`plane=northbound|southbound`、`direction=in|down|up|out`。

## 2. HTTP API

| Method | Path | 作用 |
|---|---|---|
| GET | `/api/northbound/status` | 策略、adapter、设备数、事件数 |
| GET | `/api/northbound/devices` | 设备与 capabilities；`?include_address=1` 含 adapter 地址 |
| GET | `/api/northbound/devices/<device_id>` | 单设备描述；支持 `include_address` |
| GET | `/api/northbound/catalog` | 跨设备能力目录 |
| POST | `/api/northbound/intent` | 目标 → 动作 + trace |
| POST | `/api/northbound/call` | 单次能力调用 |
| POST | `/api/northbound/commission` | 受控接入 |
| POST | `/api/northbound/decommission` | 撤销接入 |
| GET | `/api/events` | SSE 事件流 |
| GET | `/api/events/recent?since=<id>` | 事件重放 |
| GET | `/api/inventory` | 最近发现结果 |
| GET | `/api/deep_scan` | 最近端口/服务扫描 |
| GET | `/api/sim/devices` | 虚拟端点状态 |
| POST | `/api/sim/devices/<id>/action` | 模拟端点动作 |
| POST | `/api/sim/reset` | 重置模拟器 |
| GET | `/api/scene/state` | 2.5D 房间快照 |
| POST | `/api/scene/presence` | 更新 avatar/presence |
| POST | `/api/scene/automation` | 开/关场景自动模式 |
| POST | `/api/scene/reset` | 重置房间与模拟器 |

`/api/access/*`、`/api/agent` 是兼容别名。所有响应都是 JSON；SSE 使用 `text/event-stream`。

`SouthboundRuntime` 默认嵌入进程；`southbound serve` 作为共享 runtime 时，MCP/CLI 通过 `SOUTHBOUND_REMOTE_URL` 使用同一组 HTTP 端点。见 [共享 Runtime Service](RUNTIME_SERVICE.md)。

### intent

```http
POST /api/northbound/intent
Content-Type: application/json

{"goal": "打开客厅灯并关掉书房插座"}
```

响应：

```json
{
  "goal": "打开客厅灯并关掉书房插座",
  "ok": true,
  "summary": "客厅主灯：开，亮度 60%；书房插座：关，功率 0.0 W",
  "steps": [
    {"n": 1, "title": "解析目标", "detail": "...", "tool": "plan", "args": {}, "result": null}
  ],
  "affected": []
}
```

### call

```http
POST /api/northbound/call
Content-Type: application/json

{
  "device_id": "living_light",
  "capability": "set_brightness",
  "arguments": {"brightness": 30},
  "confirm": false
}
```

成功返回：

```json
{
  "ok": true,
  "device_id": "living_light",
  "name": "客厅主灯",
  "capability": "set_brightness",
  "risk": "low",
  "adapter": "sim",
  "result": {"state": {"on": true, "brightness": 30, "power_w": 2.7}}
}
```

失败返回 400：

```json
{
  "ok": false,
  "error": "高风险动作需要 confirm=true",
  "requires_confirmation": true
}
```

### commission

```http
POST /api/northbound/commission
Content-Type: application/json

{
  "adapter": "sim",
  "device_id": "demo_sensor",
  "name": "演示传感器",
  "kind": "sensor",
  "room": "客厅",
  "protocol": "Virtual",
  "tier": "T1",
  "capabilities": ["read"],
  "confirm": true
}
```

adapter 专属字段：

- `home_assistant`：`entity_id`
- `zigbee2mqtt`：`friendly_name`，可选 `command_topic` / `state_topic`
- `mqtt`：`command_topic`，可选 `state_topic`
- `sim`：可选 `state` JSON

响应包含 `device`（DeviceBinding snapshot）、`capabilities`、`adapter`、`ok`。

### scene / presence

2.5D 房间的 presence 输入与场景快照：

```http
POST /api/scene/presence
Content-Type: application/json

{"client_id": "demo", "x": 4.2, "y": 3.0}
```

房间坐标以米为单位，服务端会限制在 `12 × 8` 内；`x >= 7.5` 且 `y < 4` 为卧室，左侧下方为玄关，其余归入客厅/书房。返回的 snapshot 包含：

```json
{
  "ok": true,
  "room": {"width": 12, "depth": 8, "wall_height": 3.2, "zones": []},
  "placements": [{"device_id": "living_light", "x": 3.4, "y": 2.0, "z": 2.55}],
  "presence": {"client_id": "demo", "x": 4.2, "y": 3.0, "zone": "living"},
  "devices": [],
  "automation": {"enabled": true, "rules": []}
}
```

```http
POST /api/scene/automation
Content-Type: application/json

{"enabled": false}
```

```http
POST /api/scene/reset
Content-Type: application/json

{}
```

场景自动模式只执行低风险规则，动作仍然调用 `SouthboundRuntime.call()`，因此经过策略、审计和回读验证。`/scene` 是同一进程内的程序化 2.5D 视图；设备锚点坐标可直接映射到未来的 3DGS 视觉层。

## 3. 事件

事件由 EventBus 发布，SSE 帧：

```text
id: 12
data: {"id":12,"ts":"2026-10-26T17:00:00+08:00","type":"command","plane":"southbound","direction":"down",...}
```

公共字段：

| 字段 | 说明 |
|---|---|
| `id` | 单调递增 |
| `ts` | ISO 时间 |
| `type` | `intent`、`command`、`state`、`commission` 等 |
| `plane` | `northbound` / `southbound` |
| `direction` | `in` / `down` / `up` / `out` |
| `device_id` / `name` | 设备标识 |
| `capability` | 能力名（动作事件） |
| `ok` / `error` | 结果 |
| `requires_confirmation` | 策略拒绝时可能出现 |

事件类型：

| `type` | plane | 含义 |
|---|---|---|
| `intent` | northbound | agent 目标进入 |
| `intent.result` | northbound | 目标执行结果 |
| `command` | southbound | Southbound → adapter/设备 |
| `state` | northbound | 设备状态回读 |
| `commission.request` | northbound | 请求接入 |
| `commission` | southbound | adapter 验证/绑定 |
| `commission.result` | northbound | 接入完成，能力就绪 |
| `decommission` | southbound | 撤销接入 |
| `decommission.result` | northbound | 撤销完成 |
| `presence` | southbound | 房间 avatar/区域信号 |
| `automation.mode` | northbound | 场景自动模式开关 |
| `automation.proposed` | northbound | 规则命中，准备执行 |
| `automation.verified` | northbound | 动作已执行且回读通过 |
| `automation.failed` | northbound | 动作执行或验证失败 |
| `agent.notice` | northbound | 给 Agent 的结构化通知/升级 |
| `scene.reset` | southbound | 房间和模拟器重置 |
| `reset` | southbound | 模拟器重置 |

## 4. 数据模型

### DeviceBinding

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
    {"name": "turn_on", "risk": "low", "parameters": {}, "required": []}
  ],
  "metadata": {"virtual": true, "safe_to_automate": true}
}
```

### Capability

```json
{
  "name": "set_brightness",
  "description": "设置亮度并打开灯",
  "risk": "low",
  "parameters": {"brightness": {"type": "integer", "minimum": 1, "maximum": 100}},
  "required": ["brightness"],
  "returns": "state"
}
```

### Tool Catalog

```json
{
  "name": "light.set_brightness",
  "kind": "light",
  "capability": "set_brightness",
  "description": "设置亮度并打开灯",
  "risk": "low",
  "parameters": {"brightness": {"type": "integer", "minimum": 1, "maximum": 100}},
  "required": ["brightness"],
  "devices": ["living_light"]
}
```

## 5. MCP

stdio MCP 工具：

- 发现/深扫：`lan_inventory`、`device_details`、`signal_paths`、`integration_plan`、`deep_scan_lan`
- 模拟器：`sim_devices`、`sim_action`、`sim_goal`
- 接入层：`iot_hub_status`、`iot_devices_list`、`iot_device_describe`、`iot_tool_catalog`、`iot_call`
- 受控接入：`iot_commission`、`iot_decommission`

`iot_call` 的 `arguments` 必须符合 Capability schema；`confirm` 用于高风险动作。`iot_commission` 的 `confirm` 默认 false。

## 6. 策略与兼容性

- `read_only`：只允许 `risk=read`；禁止接入。
- `supervised`：read/low/medium 自动；high 需要 `allow_high_risk` + confirm；接入需要 confirm。
- `autonomous`：high 需要 `allow_high_risk` 且 `safe_to_automate`；接入默认仍需 confirm，除非 `allow_auto_commission=true`。

协议是**加法演进**：新增事件类型、工具和字段不破坏旧客户端；破坏性变更会升级 minor 版本并在 [CHANGELOG](../CHANGELOG.md) 标注。

## 7. 安全

- 服务默认只绑定 `127.0.0.1`；
- 接入/高风险动作需要显式确认；
- 审计写入 `data/audit.jsonl`；
- `data/commissioned.json` 不含 token/password；
- 不提供未授权扫描、认证绕过或自传播接口。

相关文档：[接入层](ACCESS_LAYER.md)、[受控接入](ONBOARDING.md)、[架构](ARCHITECTURE.md)。
