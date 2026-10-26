# 受控设备接入（Onboarding / Commissioning）

Southbound 的“扩散”是**受控的设备接入**：中控运行时发现设备，识别能力，等待主人授权，创建 `DeviceBinding`，绑定 adapter 与策略，最后才把能力暴露给 agent。

它不是把 agent 代码复制到每台设备，也不是自动猜默认口令或利用漏洞。

## 生命周期

```text
发现设备
  → 识别类型/协议/能力
  → 主人确认（confirm=true）
  → adapter 验证（HA entity / MQTT broker / 本地虚拟设备）
  → 创建 DeviceBinding + 应用策略范围
  → 注册进 AccessHub / Tool Catalog
  → agent 可 iot_call
  → 审计 + 事件 + 可撤销（decommission）
```

一次 commissioning 会产生：

- `DeviceBinding`
- `Capability` 列表（最小权限默认）
- 审计记录
- `commission.request` / `commission` / `commission.result` 事件
- 持久化的非敏感接入记录（可选 store）

## HTTP API

### 接入

`POST /api/northbound/commission`

最小虚拟设备示例：

```json
{
  "adapter": "sim",
  "device_id": "demo_sensor",
  "name": "演示传感器",
  "kind": "sensor",
  "room": "客厅",
  "protocol": "Virtual",
  "tier": "T1",
  "capabilities": ["read"],
  "state": {"temperature": 22.5, "humidity": 45, "battery": 90},
  "confirm": true
}
```

Home Assistant 示例：

```json
{
  "adapter": "home_assistant",
  "device_id": "ha:light.living_room",
  "name": "客厅灯",
  "kind": "light",
  "entity_id": "light.living_room",
  "room": "客厅",
  "protocol": "Matter",
  "capabilities": ["turn_on", "turn_off", "set_brightness", "read"],
  "confirm": true
}
```

Zigbee2MQTT 示例：

```json
{
  "adapter": "zigbee2mqtt",
  "device_id": "z2m:living_plug",
  "name": "客厅插座",
  "kind": "plug",
  "friendly_name": "living_plug",
  "room": "客厅",
  "protocol": "Zigbee",
  "capabilities": ["turn_on", "turn_off", "toggle", "read"],
  "confirm": true
}
```

MQTT 示例需要 `command_topic`（可选 `state_topic`）。

### 移除

`POST /api/northbound/decommission`

```json
{"device_id": "demo_sensor", "confirm": true}
```

## 策略

`PolicyEngine.decide_commission()` 与普通动作策略分开：

- `read_only`：禁止接入；
- `supervised`：必须 `confirm=true`；
- `autonomous`：默认仍必须 `confirm=true`；只有 `allow_auto_commission=true` 时才能自动接入；
- `allowed_rooms` / `allowed_kinds` / `allowed_adapters` 同时约束接入；
- 请求包含 `high-risk` 能力（如 `unlock`）时，还要求 `allow_high_risk=true`。

最小权限：不指定 `capabilities` 时，默认只授予非 high-risk 能力。

## 持久化

`CommissionStore` 把已授权设备写到 `data/commissioned.json`（gitignore），只保存非敏感连接信息：

- adapter、device_id、name、kind、room、protocol、tier、capabilities
- entity_id / friendly_name / command_topic / state_topic
- 不保存 token、password、access_token

运行时启动时会自动 restore。CLI 使用同一个 store，所以：

```bash
southbound onboard --adapter sim --id demo_sensor --name 演示传感器 \
  --kind sensor --room 客厅 --capabilities read --confirm

southbound access       # 看到 commissioned=1
southbound call demo_sensor read
southbound decommission demo_sensor --confirm
```

Dashboard 和 MCP 也使用同一个 `data/commissioned.json`。注意：多个进程各自维护运行时，CLI 接入后，已经运行中的 dashboard 需要刷新/重启才能看到新设备；独立 service 是下一步。

## CLI

```bash
southbound onboard --adapter sim --id demo_sensor --name 演示传感器 \
  --kind sensor --room 客厅 --capabilities read --confirm

southbound decommission demo_sensor --confirm
```

HA / Z2M / MQTT 额外参数：

- `--entity-id`
- `--friendly-name`
- `--command-topic` / `--state-topic`

## MCP

新增工具：

- `iot_commission`：受控接入；`confirm` 默认 false。
- `iot_decommission`：移除设备；`confirm` 默认 false。

agent 可以提议接入，但策略要求显式确认；高风险能力还需要 policy 开启。

## Dashboard

面板“模拟端点”下方的“＋ 接入设备”打开受控表单：

- 选择 adapter（sim / HA / Z2M / MQTT）；
- 填名称、类型、房间、能力白名单；
- 勾选确认；
- 提交后出现 `commission` 事件流，新的虚拟端点出现在面板中；
- 非内置设备可以点“移除”撤销。

## 安全边界

- 不做未授权扫描、默认口令尝试、认证绕过或漏洞利用；
- 接入必须由设备所有者确认；
- 不把 agent 复制到设备上；
- 高风险能力默认不授予；
- 每次接入/移除写审计；
- 接入记录不包含密钥；
- 公开仓库只使用脱敏/示例数据。

## 仍是扩展点

- 独立 runtime service（多客户端共享一个设备图）；
- 待批准/隔离区（quarantine）与审批 UI；
- 从 LAN 发现结果一键预填 commissioning 表单；
- HA/MQTT 的接入后健康检查与自动重连；
- 接入模板与批量 commissioning。
