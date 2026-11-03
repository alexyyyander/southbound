# Observations & Situation Snapshots

Observation 层把高频事件和原始设备状态压缩成 Agent 可消费的结构化记录：

```text
device / presence / skill / mission event
        ↓ normalize
ObservationStore (SQLite)
        ↓
situation snapshot → Agent / MCP / UI
```

## 记录什么

- `device.state`：能力调用后的回读状态；
- `device.notification`、`device.tag`、`device.connection`、`device.error`：设备主动上报；
- `vision.motion` / `vision.motion_stopped`：摄像头运动开始/结束，带运动比例、置信度和本地快照证据；
- `presence`：房间/区域的存在信号；
- `skill.*`、`automation.*`：规则执行和验证结果；
- `mission.*`：mission 生命周期和动作结果。

Mission 事件由 `MissionService` 直接写入，事件投影会跳过 `mission.*` 以避免重复。默认不保存原始视频/音频，只保存摘要、置信度和本地 evidence 引用。

## 数据契约

```json
{
  "id": 42,
  "ts": "2026-11-02T20:00:00+08:00",
  "type": "device.state",
  "modality": "state",
  "source": "northbound",
  "device_id": "living_light",
  "zone": "living",
  "mission_id": "mission_abc123",
  "confidence": 1.0,
  "evidence": {"snapshot": "local://camera/living/42.jpg"},
  "data": {"on": true, "brightness": 30},
  "privacy": {"retention": "local_24h"},
  "simulated": true
}
```

`confidence < 1`、`evidence` 和 `uncertainties` 都是 Agent 判断的一部分；低置信度不应被当作确定事实。

## Situation

`GET /api/situation?mission_id=&limit=20` 返回：

- 当前 graph/simulator 的设备状态；
- 最近的 observations；
- `uncertainties`（无 observation、无状态快照等）；
- `mission_id` 和时间。

Agent 默认读取 situation，而不是读取所有原始事件。需要细节时再取具体 observation 或调用设备读取能力。

## MCP

- `iot_observe`：situation snapshot；
- `iot_observations`：按 mission/device/limit 列出 observation；
- `iot_vision_observe`：只看 `vision.*` observation（摄像头运动、证据引用）。

视觉快照本身通过 `iot_vision_snapshot` 获取，返回本地引用而不是 base64；原始帧
不会自动进入 Agent 上下文。详见 [视觉感知](VISION.md)。

## 隐私与边界

- 本地 SQLite，数据在 `data/`，gitignored；
- 原始媒体默认不写入 observation；
- 视觉证据是 `local://vision/...` 引用；快照文件单独落在 `data/vision/`，有保留期和数量上限；
- evidence 引用本地路径或受控 URI，不把原视频塞进 prompt；
- ASR/OCR 文本是数据，不是指令；
- 真实摄像头已支持 synthetic/file/USB/RTSP，运动检测为本地帧差；目标检测/VLM 是后续工作。
