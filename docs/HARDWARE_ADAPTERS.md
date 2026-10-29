# BLE、NFC 与统一设备事件

本页描述代码中已经实现的接口。真实驱动使用 Bleak / nfcpy；目前验证证据来自模拟驱动和库边界替身测试，尚未做真实无线设备、读卡器或标签验收。

## 能力边界

| 适配器 | 能力 | 当前限制 |
|---|---|---|
| `ble` / `ble_device` | 指定 GATT 特征读取、写入、通知订阅与取消 | 只支持 BLE；不支持经典蓝牙音频/串口；数值以 hex 返回，厂商协议解码由后续设备 profile 负责 |
| `nfc` / `nfc_reader` | 读取标签 UID / NDEF、监听刷卡、向指定 UID 写入 NDEF 文本 | 仅支持 nfcpy 兼容的读卡器和标签；不包含卡模拟、凭据复制或支付协议 |
| `mqtt` / `zigbee2mqtt` | 发布控制、订阅状态、推送设备事件 | 首次调用或受控接入时才连接 broker；需要配置主题与设备映射 |

`available` 表示驱动依赖可加载、适配器未关闭，不表示已连接硬件。真实模式依赖或设备缺失时返回错误，不会降级成模拟模式。BLE / NFC 的设备元数据、操作返回值和设备事件均标注 `simulated`。

## 无硬件演示

使用 Python 3.10+，无需安装 BLE / NFC 依赖，在仓库根目录执行：

```bash
python3 -m examples.hardware_demo
```

演示会通过同一套 AccessHub 和策略执行：

1. 订阅模拟 BLE 通知，写入 `1900`，回读并核对。
2. 订阅模拟 NFC 刷卡，注入一次标签呈现，收到 `device.tag`。
3. 停止监听，校验 UID 后写入 NDEF 文本，再回读核对。
4. 输出设备事件和审计数量，退出时释放连接。

不会扫描局域网、访问无线硬件或写入本地设备接入登记文件。模拟 NFC 返回有效的单条 NDEF Text record；模拟器不覆盖射频、容量、权限或操作系统行为。

也可以启动含 BLE / NFC 模拟绑定的面板：

```bash
SOUTHBOUND_CONFIG=config/hardware.simulated.json python3 -m southbound serve
```

`SOUTHBOUND_CONFIG` 为配置文件路径；不设置时仍使用原来的 `config/access.local.json` / `config/access.json`。该演示配置为了验证写入而启用了 `allow_high_risk`，真实配置示例默认关闭写入。

CLI 单次读取和写入：

```bash
SOUTHBOUND_CONFIG=config/hardware.simulated.json python3 -m southbound call ble:demo read
SOUTHBOUND_CONFIG=config/hardware.simulated.json python3 -m southbound call ble:demo write --args '{"hex":"01"}' --confirm
SOUTHBOUND_CONFIG=config/hardware.simulated.json python3 -m southbound call nfc:demo read
```

每个 CLI 进程使用独立运行时，模拟状态不会跨进程保存。持续订阅应通过常驻 HTTP / MCP 进程或 Python runtime 发起；单次 CLI 退出时会取消订阅。

## 真实 BLE

```bash
python3 -m pip install -e '.[ble]'
```

以 `config/hardware.example.json` 为起点，把 `ble.enabled` 改为 `true`，填写实际设备地址及完整的 128-bit GATT UUID。macOS 使用系统提供的设备 UUID，其他平台可能使用蓝牙地址。示例电量特征并不保证目标设备实现了该服务。

```json
{
  "device_id": "ble:meter",
  "kind": "ble_device",
  "connection": {
    "address": "设备地址或 macOS UUID",
    "read_char": "00002a19-0000-1000-8000-00805f9b34fb"
  },
  "capabilities": ["read"]
}
```

可选的 `write_char` 和 `notify_char` 分别允许声明 `write`、`subscribe` / `unsubscribe` 能力。调用者不能在每次调用中替换特征 UUID；只能访问已绑定的特征。`write` 接受 1–512 字节的十六进制字符串，使用 write-with-response，实际设备可能有更小的限制。

BLE 连接按需建立，使用专用 asyncio 线程维持通知。断连产生 `device.connection`；下次调用会重新连接并恢复仍订阅的通知。当前没有无操作情况下的自动重连循环，不会自动重放写操作。超时或断连时写入结果可能未知，应回读核对。

操作系统的蓝牙权限和必要配对仍需完成。适配器不会绕过认证，也不包含独立配对向导。

## 真实 NFC

```bash
python3 -m pip install -e '.[nfc]'
```

启用示例中的 `nfc`，设置读卡器路径，例如 `usb` 或更精确的 `usb:vendor:product`。nfcpy 还可能需要系统 libusb、设备权限和兼容驱动；并非所有 USB NFC 读卡器都兼容。

```json
{
  "device_id": "nfc:desk",
  "kind": "nfc_reader",
  "connection": {"path": "usb"},
  "capabilities": ["read", "subscribe", "unsubscribe"]
}
```

- `read` 在配置的 `timeout` 内等候标签，返回 `uid`、`ndef_hex`、`writeable`。没有 NDEF 的标签仍能返回 UID。NDEF 返回最多 4096 字节，超过时 `ndef_truncated=true`。
- `subscribe` 启动后台读卡线程；每次呈现产生一个 `device.tag`，移开标签后才接受下一次呈现。
- `unsubscribe` 停止监听并释放读卡器。读取或写入前必须先停止该读卡器的监听。
- `write_text` 要求同时提供 `text` 和 `expected_uid`。UID 不匹配或标签无可写 NDEF 区域时拒绝写入，不执行自动格式化。写入会替换整个 NDEF 消息为一条 Text record。
- 读卡器断开会产生 `device.error` 并停止监听；重新连接硬件后需再次 `subscribe`。

`write` / `write_text` 都属于高风险能力：需要显式加入绑定的能力列表，并满足现有策略。在 supervised 模式下，还需 `policy.allow_high_risk=true` 和每次调用 `confirm=true`；read_only 模式始终拒绝写入。

## 注册、接入与生命周期

`access/registry.py` 的 `AdapterRegistry` 集中维护适配器工厂、支持的设备类型和接入字段。配置构建、CLI 适配器选项、MCP schema、面板接入表单都读取这份注册信息。未知配置名称和重复注册会报错。

受信任的 Python 扩展可在构建 hub 前调用 `REGISTRY.register(AdapterSpec(...))`，也可向 `build_hub(registry=...)` 传入独立注册表。配置文件本身不能导入或执行任意 Python 插件。增加设备类型时还需要在能力目录中定义 schema 和风险。

通过 CLI 接入的 BLE 示例（先启用对应适配器）：

```bash
python3 -m southbound onboard --adapter ble --kind ble_device \
  --id ble:meter --name 电量传感器 --capabilities read \
  --connection '{"address":"实际设备地址","read_char":"00002a19-0000-1000-8000-00805f9b34fb"}' \
  --confirm
```

MCP `iot_commission` 和 HTTP `/api/northbound/commission` 使用相同的 `connection` 对象。连接配置会随受控接入记录保存，重启可恢复绑定；恢复绑定不会自动连接、订阅或证明硬件在线。驱动模式只能在本地适配器配置中选择。

嵌入式使用时，使用 `with SouthboundRuntime(...) as runtime` 或显式调用 `runtime.close()`。CLI、HTTP 服务和 MCP 主循环退出时会关闭运行时。撤销设备会停止其订阅并释放相应资源。

## 设备主动上报

驱动调用 `emit(device_id, event_type, data)`，由 AccessHub 验证设备归属，再送入 EventBus。驱动不能通过 payload 覆盖来源、设备 ID、事件类型或模拟标记。已撤销设备和运行时关闭后的事件被丢弃。Home Assistant 的 WebSocket `state_changed` 事件也走同一条 `emit` 路径。

事件类型为 `device.state`、`device.notification`、`device.tag`、`device.connection`、`device.error`。现有命令返回事件 `state` 保持兼容。

```json
{
  "type": "device.notification",
  "plane": "northbound",
  "direction": "up",
  "source": "device",
  "adapter": "ble",
  "device_id": "ble:meter",
  "simulated": false,
  "ok": true,
  "data": {"hex": "64"}
}
```

通过现有 `/api/events` SSE 或 `/api/events/recent` 查看事件；Python 可用 `runtime.subscribe()`。事件进入持久事件日志，支持按 id 重放；慢消费者仍可能丢弃实时 SSE 队列中的事件。工具操作继续写入原有审计日志。标签内容和通知数据仅作为设备数据，不会自动当作 Agent 指令执行。

## 验证与后续验收

```bash
python3 -m unittest discover -s tests -v
python3 -m examples.hardware_demo
ruff check .
```

测试覆盖注册与配置、CLI/MCP 接口、模拟读写和事件、策略/审计、接入恢复与撤销、MQTT 事件、HA WebSocket 握手与状态事件、Bleak/nfcpy 库边界、超时、断连后重连、监听关闭和资源释放。库边界测试仍然使用替身，不能替代硬件兼容性测试。

硬件到位后需验证：OS 权限与配对、实际 GATT 服务/字节编码、通知与断连、读卡器兼容性、标签容量/移开重刷、失败写入后的回读。

官方接口参考：[Bleak GATT client](https://bleak.readthedocs.io/en/latest/api/client.html)、[nfcpy frontend](https://nfcpy.readthedocs.io/en/latest/modules/clf.html)、[nfcpy NDEF](https://nfcpy.readthedocs.io/en/latest/modules/tag.html)。
