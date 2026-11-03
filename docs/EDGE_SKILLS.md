# Edge Skills & Hardware Conditions

本文定义 Southbound 的 **skill contract**：一个 skill 不是一段 prompt，也不是任意 Python 插件，而是“某个边缘硬件场景下，允许在什么条件下、通过哪些 capability、做什么动作、如何验证、失败怎么办”的版本化契约。

当前状态：**契约、示例 manifests、`SkillRegistry`、`SkillRunner` 和 MCP/CLI skill 工具已接入。** 默认 runner 使用 shadow 模式；真正执行仍必须经过 `SouthboundRuntime.call()` → `AccessHub` 的策略、审计和回读。Camera adapter 与本地运动感知已随 0.14.0 接入；`camera_observation` skill 仍标记为 planned，等真实摄像头与 trigger/verify 现场校准后再启用；drone skill 仍需要对应 adapter 和硬件验收。

## 1. Skill 的边界

一个 skill 只能：

- 声明自己需要哪些设备类型、signal、capability 和 radio；
- 通过 `actions` 请求 `runtime.call(device_id, capability, arguments)`；
- 声明 trigger、guard、verify、fallback、action tier；
- 声明硬件和资源前提；
- 由 AccessHub 执行策略、审计、参数校验和回读。

一个 skill 不能：

- 直接执行 shell、任意 Python 或任意 HTTP；
- 直接读取/写入 adapter，绕过 AccessHub；
- 携带 token、密码或私钥；
- 自动扩大自己的权限或自动接入新设备；
- 在高风险动作上跳过确认。

## 2. Manifest 合约

```json
{
  "schema_version": 1,
  "id": "presence_lighting",
  "name": "Presence Lighting",
  "version": "0.1.0",
  "description": "Turn on a lamp when presence is detected and verify the state.",
  "status": "simulated",
  "edge_tier": "T1-T2",
  "action_tier": "B",
  "hardware": {
    "min_ram_mb": 64,
    "radios": ["Zigbee", "Wi-Fi"],
    "sensors": ["mmWave presence", "door sensor"],
    "actuators": ["smart lamp or relay"],
    "requires_local_broker": true,
    "offline_capable": true
  },
  "requires": {
    "device_kinds": ["binary_sensor", "light"],
    "capabilities": ["read", "turn_on", "turn_off"],
    "signals": ["presence", "open"]
  },
  "trigger": {
    "device_id": "entry_door",
    "field": "open",
    "equals": true,
    "for_s": 2
  },
  "guard": {
    "cooldown_s": 60,
    "max_actions_per_hour": 20,
    "clear_after_s": 10,
    "manual_override": true
  },
  "actions": [
    {"device_id": "living_light", "capability": "turn_on", "arguments": {}}
  ],
  "verify": {
    "device_id": "living_light",
    "field": "on",
    "equals": true,
    "within_s": 5
  },
  "approval": "none",
  "fallback": "notify_agent",
  "privacy": {"camera": false, "retention": "none"},
  "telemetry": ["signal", "command", "state", "verify"]
}
```

关键字段：

| 字段 | 作用 |
|---|---|
| `edge_tier` | T0–T3；决定本地能力、模型和延迟预算 |
| `action_tier` | A 告知 / B 软物理 / C 硬物理审批 / D 安全关键 |
| `hardware` | RAM、radio、sensor、actuator、本地 broker、离线能力 |
| `requires` | 设备类型、capability、signal；未满足则拒绝运行 |
| `trigger` | 事件、字段、比较、持续时间；不是“看到就执行” |
| `guard` | 冷却、频率、迟滞、手册覆盖、时间窗 |
| `actions` | 只能声明 capability 调用，不能是脚本 |
| `verify` | 动作后的状态回读；失败进入 fallback |
| `privacy` | 摄像头/麦克风/标签数据的本地保留策略 |
| `telemetry` | 需要写入 EventBus/审计的事件类型 |

## 3. Action Tier 与策略映射

| Tier | 示例 | 允许的执行方式 |
|---|---|---|
| A 告知 | 传感器读数、异常提醒 | 自动记录/通知 |
| B 软物理 | 灯、窗帘、温控、净化器 | 可在策略范围内自动，必须回读 |
| C 硬物理 | 继电器断电、门锁、阀门 | 必须审批/confirm；记录操作者和理由 |
| D 安全关键 | 过流、燃气、热失控 | 只允许经过认证的安全逻辑；不能由普通 skill 授予 |

Southbound 当前风险 `read/low/medium/high` 应映射到 A–D，但 **D 级不能仅靠 `allow_high_risk=true`**；需要独立的安全回路和硬件急停。

## 4. T0–T3 边缘硬件要处理什么

### T0：只上报端点（门磁、温湿度、电池传感器）

- 电池/能量采集、睡眠周期、上报间隔；
- 丢包、重复包、乱序、时钟漂移；
- 信号弱、网关离线、配对丢失；
- 传感器漂移、校准、污染/遮挡；
- 不能依赖本地推理；复杂判断放 T1/T2；
- skill 应容忍 `quality=unknown/last_seen`，不能把“没收到”当“正常”。

### T1：MCU（ESP32/STM32/RA8 等）

- RAM/flash 小，不能跑大模型；
- watchdog、brownout、复位原因、OTA 回滚；
- GPIO/PWM/ADC、继电器、执行器默认安全态；
- 本地反射必须毫秒级，不依赖 Wi-Fi/云；
- 网络断开时本地规则继续工作；
- 不能把安全关键逻辑只放在 skill 云侧；
- 需要明确的资源预算和失败安全态。

### T2：Linux SoC（Pi 4/5、N100、NAS）

- CPU/RAM/温度/降频、SD 卡磨损、UPS；
- systemd 守护、日志轮转、时间同步；
- MQTT/HA/Z2M 重连、消息重复、队列积压；
- USB radio 干扰、Zigbee/Thread 信道；
- 本地小模型的内存、延迟、模型回滚；
- 更新失败、依赖缺失、磁盘满；
- 安全补丁和本地服务暴露面。

### T3：边缘 GPU / 服务器（Jetson、GPU 工作站）

- GPU 显存、功耗、散热、并发任务；
- 摄像头带宽、编码/解码、存储写入；
- 模型版本、量化、回退和超时；
- 多设备/多用户调度；
- 隐私和本地数据保留；
- 不允许把“大模型可用”当成安全前提。

## 5. 硬件与环境条件清单

### 电源

- 断电/UPS/电池阈值；
- 电压跌落、过流、浪涌；
- 执行器断电后的安全位置；
- 重启后状态是否可恢复。

### 热与机械

- 风扇/散热片/降频；
- 封闭柜、灰尘、环境温度；
- 继电器/电机/阀门卡滞；
- 限位开关、保险丝、急停。

### 网络

- Wi-Fi 丢包、漫游、2.4GHz 干扰；
- Zigbee/Thread 路由、信道、协调器离线；
- MQTT QoS、重连、重复投递、消息顺序；
- 断网时本地规则和离线队列；
- 时钟漂移与事件时间。

### 感知

- 传感器漂移、量程、采样率；
- 多传感器冲突/融合；
- 摄像头遮挡、逆光、隐私区域；
- NFC 标签移开/重复刷；
- 置信度和数据年龄。

### 执行

- 动作是否真正完成（回读，不只看命令成功）；
- 重试是否幂等；
- 执行器卡死/反馈缺失；
- 手动覆盖和物理急停；
- 高风险动作的人工确认和审计。

### 安全与隐私

- 设备配对、凭据、固件供应链；
- MCP/tool prompt injection；
- 最小权限、工具 scope、rate limit；
- 摄像头/麦克风/标签数据的本地保留；
- 远程访问 TLS/身份；
- 不自动接入陌生设备，不默认口令。

### 生命周期

- 发现 → 受控 onboarding → 试运行 → 监督 → 自动；
- 版本升级/回滚；
- 设备替换/所有权转移；
- 撤销后的订阅、凭据和本地数据清理。

## 6. Skill 生命周期

```text
discover → validate manifest → simulate → shadow mode
        → supervised → autonomous (low risk only)
        → monitor / verify / rollback
```

- **shadow**：只记录“如果执行会做什么”，不动作；
- **supervised**：人工确认；
- **autonomous**：只对低风险、已验证、可回读的动作；
- 任何 skill 版本升级都必须重新走策略/验证；
- 用户手动覆盖应写入反馈，作为规则改进依据，但不能自动修改 policy。

## 7. 与 Southbound runtime 的接入点

已接入：

- `SkillRegistry`: 从 `skills/*.skill.json` 加载、校验、版本管理；
- `SkillRunner`: 订阅 EventBus/状态，评估 trigger，调用 `runtime.call()`，执行 verify/fallback；
- MCP：`iot_skills`、`iot_skill_describe`、`iot_skill_run`、`iot_skill_status`；
- CLI：`southbound skills` / `southbound skill <id>`；
- `serve` 启动默认 shadow runner；显式 `iot_skill_run` 使用 supervised 模式，C 级/人工审批需要 `confirm=true`；
- 评测：把 skill 场景接入 AssetOpsBench/SmartTinkerer 风格的 benchmark（下一步）。

示例 manifests 在 [`skills/`](../skills/)；运行时执行路径已接通，真实相机/无人机仍需 adapter 和硬件验收。
