# 最小采购与刷机清单（ESP32 / Zigbee / Matter）

目标：用最少的硬件跑通 **agent → 网关 → 真实设备 → 状态回读** 的闭环。先做一条链路，再扩展。

> 价格为 2026-10 中国电商参考价（含税/包邮会有波动），型号以购买时页面为准。Matter 设备务必确认包装标注 **Matter over Wi-Fi**，不是“仅支持厂商云”或“Matter bridge only”。

## 三条路线

| 路线 | 内容 | 参考总价 | 适合 |
|---|---|---|---|
| A 最小验证 | ESP32-S3 + 一个 Matter Wi-Fi 插座 | ¥150–250 | 先验证 agent 闭环 |
| B 推荐 | A + Zigbee 协调器 + 一个 Zigbee 插座/传感器 | ¥500–700 | 覆盖 Wi-Fi MCU、Zigbee、Matter 三条链路 |
| C 全协议 | B + Thread 边界路由 / 433MHz SDR | ¥800+ | 后续扩展 Thread、433 传感器 |

建议先走 **B**，但可以先下单 A 里的 ESP32 和 Matter 插座，Zigbee 部分到货后再加。

## 购物清单

### 核心（必买）

| 类别 | 推荐型号 | 数量 | 参考价 | 说明 |
|---|---|---|---|---|
| Wi-Fi MCU | **ESP32-S3-DevKitC-1**（乐鑫官方） | 1 | ¥55–70 | 替代：合宙/兼容板 ¥30–45；不要买 ESP32-S2 或只有 2MB Flash 的版本 |
| 电源/线 | USB-C 数据线 + 5V/2A 电源 | 1 | ¥20 | 线必须能传数据，不是纯充电线 |
| 传感器 | DHT22 或 SHT30 | 1 | ¥15–25 | 温湿度，先跑通遥测 |
| 执行器 | 5V 继电器模块（低电平触发） | 1 | ¥8–12 | 先用 LED 测试，不要直接接市电 |
| 接线 | 面包板 + 杜邦线 | 1 | ¥15 | 母对母/公对母各一包 |
| Zigbee 协调器 | **SLZB-06**（以太网，预刷固件） | 1 | ¥230–280 | 最省心，NAS 首选；也可选 Sonoff ZBDongle-E/P |
| Zigbee 终端 | Sonoff S40 Lite 插座 或 Aqara 温湿度 | 1 | ¥60–100 | 确认是 Zigbee 3.0，不是 Wi-Fi 版 |
| Matter 终端 | **Meross MSS315** 或 **TP-Link Tapo P110M** | 1 | ¥100–150 | 必须是 Matter over Wi-Fi；2.4GHz |

### 可选

| 类别 | 推荐型号 | 参考价 | 什么时候需要 |
|---|---|---|---|
| USB 蓝牙适配器 | CSR8510 / Realtek 芯片 | ¥30–50 | HA 宿主（NAS）没有蓝牙时，Matter 配网需要 |
| USB 延长线 | 任意 | ¥15 | 用 USB Zigbee 棒时，远离 USB 3.0 干扰 |
| Zigbee USB 棒 | Sonoff ZBDongle-E（EFR32MG21） | ¥90–120 | 想省钱、愿意刷机；配 USB 延长线 |
| Zigbee USB 棒 | Sonoff ZBDongle-P（CC2652P） | ¥100–130 | 同上，Z-Stack 生态更老牌 |
| Thread 边界路由 | HA Connect ZBT-1 / ESP32-C6 | ¥50–250 | 买 Thread 设备（如 Eve）时才需要 |
| 433MHz 接收 | RTL-SDR + 天线 | ¥120–180 | 门磁/气象站/遥控器 |

### 可以后买的

- 更多 Zigbee 插座/传感器（先买一个验证配对）
- 摄像头/毫米波存在传感器（LD2410）
- 第二个 ESP32-S3（做分布式节点）

## 刷机与配置

### 1. ESP32-S3 → ESPHome

**步骤**

1. 安装 ESPHome：
   ```bash
   pip install esphome
   ```
   或在 Home Assistant 的 ESPHome 插件里用 Web 界面。
2. 新建 `lab-node.yaml`（见下），把 Wi-Fi 写进 `secrets.yaml` 或直接填。
3. USB 连接开发板，执行：
   ```bash
   esphome run lab-node.yaml
   ```
   首次用 USB，之后 OTA 无线升级。
4. Home Assistant → 设置 → 设备与服务 → 添加集成 → **ESPHome**，输入设备 IP；或让它自动发现。
5. 打开 ESPHome 日志确认传感器数值与继电器动作。

**`lab-node.yaml` 最小示例**

```yaml
esphome:
  name: lab-node
  friendly_name: Lab Node

esp32:
  board: esp32-s3-devkitc-1
  framework:
    type: esp-idf

logger:
api:
ota:
  - platform: esphome

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  ap:
    ssid: "lab-node-fallback"

captive_portal:
web_server:
  port: 80

sensor:
  - platform: dht
    pin: GPIO4
    model: DHT22
    temperature:
      name: "Lab Temperature"
    humidity:
      name: "Lab Humidity"
    update_interval: 30s

switch:
  - platform: gpio
    pin: GPIO5
    name: "Lab Relay"
```

**接线与注意**

- DHT22 数据脚接 GPIO4，电源 3.3V，GND 共地；SHT30 用 I2C（SDA/SCL）。
- 继电器信号接 GPIO5；很多模块是低电平触发，若发现逻辑相反，在 `pin:` 下加 `inverted: true`。
- 第一次只用继电器控制 LED 或低压风扇，不要直接接 220V。
- ESP32-S3 的 GPIO0/3/45/46 是启动 strapping 脚，尽量避开。
- 想用 MQTT 而不是 ESPHome 原生 API，可加：
  ```yaml
  mqtt:
    broker: 192.168.1.20
    topic_prefix: lab-node
  ```

### 2. Zigbee 协调器

#### 方案 A：SLZB-06（推荐，免刷机）

1. 插网线 + USB-C 供电，等待启动。
2. 在路由器 DHCP 列表里找到它的 IP，浏览器打开 Web UI。
3. 模式选 **Coordinator**，在 Web UI 里检查/升级固件。
4. Zigbee2MQTT 配置使用 TCP（默认端口 6638，以 Web UI 实际配置为准）：
   ```yaml
   serial:
     port: tcp://192.168.1.x:6638
     adapter: zstack
   ```

#### 方案 B：Sonoff ZBDongle-E（EFR32MG21）

1. 插上 USB 延长线，确认串口（macOS `/dev/tty.usbserial-*`，Linux `/dev/ttyUSB0`）。
2. 安装刷机工具：
   ```bash
   pip install universal-silabs-flasher
   ```
3. 按官方说明进入 bootloader（通常是按住按钮再插 USB），执行：
   ```bash
   universal-silabs-flasher --device /dev/ttyUSB0 flash --firmware <firmware.gbl>
   ```
4. Zigbee2MQTT 里 `adapter: ember`，`port: /dev/ttyUSB0`。

#### 方案 C：Sonoff ZBDongle-P（CC2652P）

1. 安装 `zigpy-znp`：
   ```bash
   pip install zigpy-znp
   python -m zigpy_znp.tools.flash_znp --help
   ```
2. 按 `--help` 给出的参数，用 Koenkk 的 Z-Stack 协调器固件刷入。
3. Zigbee2MQTT 里 `adapter: zstack`。

> 刷机有变砖风险。不想折腾就选 SLZB-06，贵约 ¥150，但省掉驱动、刷机和 USB 干扰问题。

#### Zigbee2MQTT 最小配置

```yaml
mqtt:
  base_topic: zigbee2mqtt
  server: mqtt://192.168.1.20:1883

serial:
  port: tcp://192.168.1.x:6638   # SLZB-06；USB 棒改成 /dev/ttyUSB0
  adapter: zstack                 # EFR32 用 ember

permit_join: true
advanced:
  network_key: GENERATE
```

启动后在 Zigbee2MQTT 网页点 **Permit join**，按住 Zigbee 插座/传感器的配对键，等它出现。

### 3. Matter 设备

Matter 设备通常**不需要刷机**，固件由厂商或 Home Assistant 更新。

1. Home Assistant 安装 **Matter Server**（HA OS 是插件；Docker 环境用 `python-matter-server` 容器）。
2. 设置 → 设备与服务 → 添加集成 → **Matter**。
3. 插座进入配对模式（长按按钮直到指示灯闪烁），在 HA 里扫描二维码或输入配对码。
4. 选择 2.4GHz Wi-Fi 并输入密码；完成后 HA 会生成实体。
5. 如果 NAS 没有蓝牙，先在 HA 宿主上插 USB 蓝牙适配器，并把 USB 设备映射进 Matter Server 容器；或者把 Matter Server 跑在一台带蓝牙的 Mac/Pi 上。

> 买 Wi-Fi Matter 插座（Meross MSS315 / Tapo P110M）可以避开 Thread 边界路由；买 Eve 这类 Thread 设备则需要额外的 Thread Border Router（HomePod/Apple TV、HA Connect ZBT-1 或 ESP32-C6）。

### 4. Home Assistant 放哪里

- **Synology DS925+（推荐）**：Container Manager 里跑 Home Assistant Container + Mosquitto + Zigbee2MQTT + Matter Server。SLZB-06 走 TCP，不需要 USB 直通；Matter 需要蓝牙适配器。
- **HA OS 虚拟机 / 树莓派**：插件生态最完整，Matter/Zigbee 配置更省事；如果 NAS 折腾成本高，用一台 Pi 4/5 更简单。
- 本机 Mac 也可以临时跑 Docker 版 HA，但不适合长期常开。

## 上电与验收顺序

1. **ESP32**：串口日志 → 连 Wi-Fi → HA 发现 → 开关继电器 → 温湿度读数。
2. **Zigbee**：协调器上线 → Zigbee2MQTT 网页 → Permit join → 配对插座 → MQTT 出现 `zigbee2mqtt/<设备>/state`。
3. **Matter**：插座配网 → HA 出现实体 → 开关一次 → 状态回读。
4. **网络验收**：跑一次发现，确认新设备出现：
   ```bash
   python3 discover.py --sweep
   ```
   ESP32 应显示为 Espressif（T1），SLZB-06 会是一个带 6638 端口的 Linux 设备（T2/T0）。
5. **Agent 验收**：给 agent 加一个 MQTT publish 工具，测试“打开实验室继电器 / 读取温湿度”，确认从意图到状态回读的闭环。

## 安全与布线

- 第一次只控制 LED、低压风扇或 USB 供电的小负载；市电负载用成品智能插座，不要自己焊 220V。
- 继电器模块与 ESP32 共地；感性负载加续流二极管。
- Zigbee USB 棒用延长线远离 USB 3.0 口和金属机箱。
- 实验室设备放独立 IoT SSID/VLAN，禁止公网端口映射。
- Matter/Zigbee 设备配对码和网络密钥保存在密码管理器；HA 配置定期备份。

## 参考链接

- ESPHome：https://esphome.io/
- Zigbee2MQTT：https://www.zigbee2mqtt.io/
- Home Assistant Matter：https://www.home-assistant.io/integrations/matter/
- Koenkk Z-Stack 固件：https://github.com/Koenkk/Z-Stack-firmware
- universal-silabs-flasher：https://github.com/NabuCasa/universal-silabs-flasher
- zigpy-znp：https://github.com/zigpy/zigpy-znp
- SLZB-06：https://smlight.tech/product/slzb-06/
