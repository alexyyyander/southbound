# 多硬件交互面

Southbound 的控制面回答“谁能控制哪个设备”；交互面回答“用户现在在哪台硬件上、用哪种方式与同一个 agent 对话”。

这一层把 agent 拆成五种能力：

- **大脑**：规划、记忆、策略和工作流，通常在 Mac/N100/NAS；
- **感官**：麦克风、摄像头、按钮、人体/环境传感器；
- **嗓子**：TTS、音箱、短语音；
- **脸**：屏幕、LED、TV、投影；
- **手**：通过 Southbound AccessHub 执行设备动作。

同一个 session 可以跨端点接续；每个端点只负责自己有能力处理的模态。

## 端点模型

```json
{
  "endpoint_id": "pi-living",
  "name": "客厅 Pi",
  "kind": "room_hub",
  "location": "living",
  "input": ["audio"],
  "output": ["audio", "screen"],
  "capabilities": ["wake_word", "local_asr", "tts"],
  "privacy": {"mic": "on_demand"},
  "metadata": {"simulated": true},
  "trust": "owner",
  "last_seen": "2026-10-29T17:00:00+08:00"
}
```

内置模拟端点：

| endpoint_id | 硬件 | 输入 | 输出 |
|---|---|---|---|
| `mac-dashboard` | Mac/桌面 | text, audio | text, audio, screen |
| `phone-owner` | 手机 | text, audio | text, audio, screen, notification |
| `pi-living` | 客厅 Pi | text, audio | audio, screen |
| `esp32-kitchen` | 厨房 ESP32 | button, wake_word | led, short_tts |
| `tv-living` | 电视 | remote | screen, audio |

端点注册默认是 `unverified`；只有 `confirm=true` 才会成为 `owner`。已存在的端点再次注册必须带 `confirm=true`，否则会被拒绝，避免无确认改写已批准端点的模态和路由。注册/审批会写入 AccessHub 审计。

## Session 与路由

```json
{
  "session_id": "s-...",
  "user_id": "owner",
  "active_endpoints": ["phone-owner", "pi-living", "tv-living"],
  "input_endpoint": "phone-owner",
  "last_input": "打开客厅灯",
  "last_response": "客厅主灯：开，亮度 60%",
  "context": {"intent_ok": true}
}
```

一次输入的处理顺序：

1. 校验输入端点已获批准，且声明支持当前输入模态（P0 为 `text`）；
2. 创建或恢复 session；如果 session 已 handoff，只接受目标端点作为输入；
3. 先校验输出端点和本次输出选择，再执行文本 intent；
4. 按 `output_endpoints` 显式路由；省略时默认回到当前输入端点，不能默认时使用 session 的活动输出端点；
5. 按端点能力渲染 `text / display / speech / notification / led`；
6. 发布带真实 `ok/error` 的 `interaction.input`、`interaction.output`、`interaction.session` 事件；
7. 更新 session 并持久化，返回持久化后的 session。

### 自动路由

- 输入端点本身有输出能力时，默认回到输入端点；
- 未显式提供 `output_endpoints` 且输入端点没有输出时，使用 session 中已批准的活动输出端点；
- `output_endpoints: []` 明确表示本轮不渲染到任何端点；
- 不再使用“第一个端点”作为全局兜底，避免把私人响应发到无关硬件。

### Handoff

`handoff` 把 session 的输入端点切到目标端点，并加入活动端点。之后其他端点直接提交输入会被拒绝；目标端点必须声明 `text` 输入（P0）。后续输入默认由该端点处理。

### 确认与错误

`confirm` 和 `execute` 只接受真实布尔值或 `true/false`、`1/0` 文本；`"false"` 不会提升端点信任，也不会执行设备动作。失败的 intent 会以 `ok:false` 和 `error` 写入 interaction 事件，不会伪装成成功。

## HTTP API

```text
GET  /api/interaction/endpoints
POST /api/interaction/endpoints
POST /api/interaction/heartbeat
GET  /api/interaction/sessions
GET  /api/interaction/sessions/<session_id>
POST /api/interaction/input
POST /api/interaction/handoff
```

### 发送一轮

```http
POST /api/interaction/input
Content-Type: application/json

{
  "endpoint_id": "phone-owner",
  "text": "打开客厅灯",
  "output_endpoints": ["pi-living", "tv-living"],
  "execute": true
}
```

响应包含：

```json
{
  "ok": true,
  "session": {"session_id": "s-...", "active_endpoints": ["phone-owner", "pi-living", "tv-living"]},
  "response": "客厅主灯：开，亮度 60%",
  "outputs": [
    {"endpoint_id": "pi-living", "modalities": ["audio", "screen"],
     "payload": {"speech": "...", "display": "..."}},
    {"endpoint_id": "tv-living", "modalities": ["screen", "audio"],
     "payload": {"speech": "...", "display": "..."}}
  ]
}
```

### 交接

```http
POST /api/interaction/handoff
Content-Type: application/json

{"session_id": "s-...", "endpoint_id": "pi-living"}
```

### 注册端点

```http
POST /api/interaction/endpoints
Content-Type: application/json

{
  "endpoint_id": "tablet-kitchen",
  "name": "厨房平板",
  "kind": "tablet",
  "location": "kitchen",
  "input": ["text", "audio"],
  "output": ["screen", "audio"],
  "capabilities": ["tts"],
  "confirm": true
}
```

## CLI

```bash
southbound interact "打开客厅灯" \
  --endpoint phone-owner \
  --output pi-living \
  --output tv-living
```

```bash
southbound interact "灯太亮了" \
  --endpoint pi-living \
  --session s-xxxxxxxxxxxx
```

`--no-execute` 只做路由和渲染，不调用 AccessHub。

## MCP

- `interaction_endpoints`：列出端点、模态、信任状态；
- `interaction_input`：输入 `endpoint_id`、`text`，可选 `session_id`、`output_endpoints`、`execute`。

## 面板

启动 `southbound serve` 后打开 `/interaction`：

- 端点卡片显示输入/输出模态、位置和信任状态；
- 选择输入端点、输入文字、勾选输出端点；
- 查看每个输出端点的模态和渲染结果；
- 查看这一轮事件；
- 创建 session 后可直接 handoff。

## 硬件落点

| 硬件 | 建议承担 |
|---|---|
| Mac / 手机 | 完整客户端、文字/语音、通知、审批 |
| Pi / N100 | 房间 hub、麦克风、扬声器、屏幕、本地 ASR/TTS |
| ESP32-S3 | 唤醒词、按钮、LED、短语音；不跑完整 agent |
| TV | 大屏展示、语音播报 |
| BLE/NFC | 在场、身份、轻量输入 |
| 机器人 | 移动/视觉/执行器，高风险动作需审批 |

## 当前边界

- 当前实现是 **文本轮次**；`audio` 是端点能力声明，音频采集/ASR/TTS 由端点 shell 负责。
- `interaction.output` 是渲染意图（text/display/speech/led），不是实际音频流；成功/失败通过 `ok/error` 表达。
- 端点注册、会话和事件已持久化；真实端点的 mTLS、签名包、离线队列和跨端音频 handoff 仍是后续工作。
- 高风险设备动作仍经过 AccessHub 的策略、确认和审计。
