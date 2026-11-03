# 视觉感知 / Vision

版本：`0.14.0`。这是 Southbound 的第一版视觉感知面（MVP）：把摄像头接入统一
access layer，把运动变化变成结构化 observation，再交给 skill / mission / MCP
消费。它是**本地感知层**，不是 NVR，也不是把视频流直接塞给模型的管道。

## 1. 边界

- 原始帧默认只保存在本机 `data/vision/`，不进入 EventBus、不进 prompt、不上传；
- `camera.snapshot` 是只读能力，但带有 `sensitivity: camera`，需要
  `policy.allow_camera=true`；
- 默认不保存视频流，只保存按需快照和运动起始证据；保留期与数量可配置；
- 不做人脸识别/身份识别，不做云端推理，不做自主 PTZ；
- 摄像头仍应放在独立 IoT VLAN，控制面只绑定 loopback 或可信 LAN。

## 2. 链路

```text
CameraSource (synthetic / file / usb / rtsp)
        │  snapshot()
        ▼
CameraAdapter ── AccessHub 策略 / 审计 / 回读
        │  Frame(data, width, height, gray)
        ▼
PerceptionService ── MotionDetector (64×36 灰度帧差)
        │  vision.motion / vision.motion_stopped
        ▼
EventBus → ObservationService → ObservationStore
        │
        ├── SkillRunner（事件 trigger）
        ├── Mission（快照作为 verify 证据）
        └── MCP / HTTP / Dashboard
```

关键设计：**算法不直接进 LLM 上下文，帧也不进。** 感知服务先把帧压缩成带
`confidence`、`evidence`、`privacy` 的 observation；Agent 读 observation，需要
细节时再按本地 evidence 引用取图。

## 3. 摄像头来源

| source | 依赖 | 用途 |
|---|---|---|
| `synthetic` | 无 | 演示、测试、没有硬件时验证整条链路 |
| `file` | 无（灰度需要 Pillow） | 单张图片、离线测试、固定机位抓图 |
| `usb` | `opencv-python-headless`（`[vision]`） | USB UVC 摄像头，本机调试 |
| `rtsp` | 系统 `ffmpeg` | PoE/Wi-Fi IP 摄像头，一次抓一帧 |

安装可选视觉依赖：

```bash
python3 -m pip install -e ".[vision]"
```

RTSP 只要求系统里有 `ffmpeg`。USB 摄像头需要 OpenCV；没有 OpenCV 时
commission 会明确拒绝，不会静默降级。

### 推荐硬件路径

1. 先用 `synthetic` 或笔记本 USB 摄像头把契约跑通；
2. 第一台真实设备选明确支持本地 RTSP 的 Tapo / Reolink 等摄像头；
3. 多路再上 Frigate + N100/Pi 5 作为 camera edge，Southbound 只消费结构化结果；
4. Jetson / OAK-D / 无人机留给空间感知和运动控制，不放进第一版。

摄像头不要和 LLM 推理抢同一台小机器；按 [HARNESS.md](HARNESS.md) 的 camera edge
拆分。

## 4. 启用

复制 `config/access.example.json` 为 `config/access.local.json`，打开 camera：

```json
{
  "policy": {
    "mode": "supervised",
    "allow_camera": true
  },
  "adapters": {
    "camera": {
      "enabled": true,
      "snapshot_dir": "data/vision",
      "retention_s": 86400,
      "max_snapshots": 200,
      "cameras": [
        {
          "device_id": "entry_camera",
          "name": "入户摄像头",
          "room": "玄关",
          "source": "synthetic",
          "perception": true
        },
        {
          "device_id": "living_camera",
          "name": "客厅摄像头",
          "room": "客厅",
          "source": "rtsp",
          "url_env": "LIVING_CAMERA_RTSP_URL",
          "perception": false
        }
      ]
    }
  }
}
```

RTSP 凭据只从环境变量读取：

```bash
export LIVING_CAMERA_RTSP_URL='rtsp://USER:PASSWORD@192.168.1.50/stream1'
```

`source: "file"` 用 `path` 或 `path_env`；`source: "usb"` 用 `index`。

也可以不写配置文件，通过受控 onboarding 接入：

```bash
python3 -m southbound onboard \
  --adapter camera \
  --kind camera \
  --id living_camera \
  --name 客厅摄像头 \
  --room 客厅 \
  --source rtsp \
  --url-env LIVING_CAMERA_RTSP_URL \
  --perception \
  --confirm
```

MCP 的 `iot_commission` 同样接受 `source`、`path`、`url_env`、`index` 和
`perception`；不要把带凭据的 URL 直接写进工具参数，优先用 `url_env`。

`southbound serve` 会在用户配置没有显式 camera 段时注入一个空的 camera adapter，
所以 Dashboard 可以直接点“接入演示摄像头”；其他入口（CLI/MCP 本地模式）需要
显式配置或 onboarding。

## 5. 能力与接口

### 能力

| capability | risk | sensitivity | 说明 |
|---|---|---|---|
| `snapshot` | read | camera | 抓一帧，写入本地证据，返回 URI/hash/尺寸 |
| `read` | read | camera | 读取来源、可用性、运动状态、最近快照 |

两者都要经过 `AccessHub` 的参数校验、`policy.allow_camera`、审计和回读。快照
结果不会返回原始 base64，只返回本地引用：

```json
{
  "state": {
    "source": "synthetic",
    "motion": true,
    "last_snapshot": "local://vision/entry_camera/20261103T025134-53593716.png",
    "last_snapshot_status": "ok"
  },
  "snapshot": {
    "snapshot": "local://vision/entry_camera/20261103T025134-53593716.png",
    "url": "/api/vision/snapshots/entry_camera/20261103T025134-53593716.png",
    "sha256": "0e46e85c…",
    "content_type": "image/png",
    "width": 160,
    "height": 90
  }
}
```

### HTTP

- `GET /api/vision/status` — 摄像头、来源、感知服务、运动状态；
- `GET /api/vision/cameras` — 同上的别名；
- `POST /api/vision/<device_id>/snapshot` — 经 AccessHub 抓图；
- `GET /api/vision/snapshots/<device_id>/<filename>` — 本地证据图片；
- `GET /api/vision/observations?device_id=&limit=` — 最近的 `vision.*` observation。

### MCP

- `iot_vision_status` — 摄像头与感知状态；
- `iot_vision_snapshot` — 抓图，返回本地证据引用；
- `iot_vision_observe` — 最近的视觉 observation。

### CLI

```bash
southbound vision
southbound vision --json
southbound vision --snapshot entry_camera
```

CLI 本地模式需要 camera adapter 已配置；连接共享 runtime 时设置
`SOUTHBOUND_REMOTE_URL`。

## 6. Observation 契约

```json
{
  "ts": "2026-11-03T02:51:35+08:00",
  "type": "vision.motion",
  "modality": "vision",
  "source": "perception",
  "device_id": "entry_camera",
  "zone": "玄关",
  "confidence": 0.77,
  "data": {
    "motion": true,
    "ratio": 0.056,
    "threshold": 0.02,
    "frame": "local://vision/entry_camera/…png"
  },
  "evidence": {
    "snapshot": "local://vision/entry_camera/…png",
    "sha256": "…",
    "captured_at": "…"
  },
  "privacy": {
    "camera": true,
    "retention": "86400s",
    "raw_media_stored": true
  },
  "simulated": true
}
```

`vision.motion_stopped` 不带新的证据文件；它只表示运动状态回到静止。

## 7. 运动检测

第一版 `MotionDetector` 是纯 Python 帧差：

- 源帧降采样为 `64×36` 灰度；
- 相邻帧逐像素差值超过 `pixel_delta`（默认 18）的比例超过
  `threshold`（默认 0.02）即判定运动；
- 只在状态切换时发布事件，避免 observation 刷屏；
- 运动起始的证据文件有 30 秒冷却，避免存储爆炸。

它不区分人、车、宠物，也不做跟踪。下一版可以在同一 `Detector` 协议下接入
ONNX YOLO、Frigate MQTT 事件或本地 VLM；事件/observation 契约不变。

## 8. 存储与隐私

- 默认目录：`data/vision/<device>-<hash>/`，已在 `.gitignore` 的 `data/` 下；
- 默认保留 24 小时、每个摄像头最多 200 张；
- 快照文件名包含时间戳和随机后缀，不包含原始 URL/凭据；
- RTSP 错误信息会脱敏用户名/密码；
- `GET /api/vision/snapshots/...` 只接受受控文件名和目录，拒绝路径穿越；
- 非 loopback 访问仍受 Bearer token 保护；浏览器 `<img>` 无法带 token，远程
  Dashboard 需要反代或留在 loopback。

## 9. 已知限制

- 未做真实 RTSP/USB 硬件验收；驱动边界已有模拟与合成测试；
- 运动检测是帧差，不是目标检测；光照突变会误报；
- 没有视频流/录像、没有音频、没有人脸/车牌识别；
- 没有异步 mission worker，长时“盯守”任务仍在路线图上；
- 没有模型注册表；下一版再引入 `models/*.model.json` 与 ONNX detector。
