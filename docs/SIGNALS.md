# 数据信号采集矩阵

给“跑不了 agent 的设备”补数据，通常走三条路：**协议桥接、无线嗅探、物理量采集**。下面按信号类型列出能拿到什么、需要什么、设备要不要配合。

> 前提：只在自己拥有或获授权的网络上采集。嗅探他人设备、解密他人流量、未经许可发射射频，都可能违法。

## 1. 网络与服务发现（零额外硬件）

| 信号 | 能拿到什么 | 工具 | 设备端条件 | 限制 |
|---|---|---|---|---|
| ARP | IP、MAC、厂商 OUI | 系统自带 `arp` | 无 | 只看得到近期通信过的主机 |
| mDNS / Bonjour | 服务类型、实例名、TXT 记录（型号、版本） | `dns-sd`、Avahi、zeroconf | 开启 mDNS | macOS 需“本地网络”权限；跨 VLAN 不可见 |
| SSDP / UPnP | friendlyName、manufacturer、model、服务列表 | 本项目的 `discover.py` | 开启 UPnP | 很多路由器默认关闭多播 |
| DHCP 租约 | 主机名、MAC、租期 | 路由器后台/API | 需要路由器权限 | 最完整的主机清单来源 |
| ICMP / TCP 指纹 | 存活、端口、HTTP 标题 | `nmap`、`curl` | 无 | 主动探测，注意授权与噪声 |

## 2. 无线嗅探（设备无需跑 agent，甚至无需联网）

| 信号 | 能拿到什么 | 典型硬件 | 是否需要密钥 | 备注 |
|---|---|---|---|---|
| BLE 广播 | 名称、MAC、服务 UUID、厂商自定义数据（温湿度、电量） | ESP32、nRF52840（Sniffle） | 不需要 | 只收广播；加密连接内容拿不到 |
| BLE GATT | 特征值读写 | ESP32、nRF52 | 需要配对/授权 | 相当于直接控制 |
| Zigbee | 加入/离开、命令元数据、链路质量 | CC2652/CC2531/nRF52840 sniffer | 解密需要网络密钥 | 自己网络的密钥可用 zigbee2mqtt 导出 |
| Thread / Matter | 同上 + Matter 属性 | nRF52840 / ESP32-C6 + OpenThread sniffer | 需要 Thread 网络密钥 | 通过 Matter controller 订阅更简单 |
| Z-Wave | 帧、节点状态 | 700/800 系列 Zniffer | S0/S2 密钥 | 家庭自动化常见 |
| 315/433/868/915 MHz | 门磁、遥控、气象站原始帧 | RTL-SDR + `rtl_433` | 不需要 | 只收不发；发射受无线电法规约束 |
| 2.4 GHz 私有协议 | 鼠标/键盘/玩具数据 | nRF24、ESP32 | 视协议 | 兼容性差，逐设备逆向 |
| Wi-Fi | 帧头、CSI（信道状态信息） | Linux monitor mode、ESP32 CSI | 不需要 | macOS 不支持 monitor mode；CSI 可做存在/呼吸感知 |
| 红外 | 遥控码 | TSOP 接收头 + IRremote | 不需要 | 需视距 |

## 3. 有线与工业协议

| 协议 | 能拿到什么 | 接入方式 | 设备端条件 |
|---|---|---|---|
| Modbus RTU/TCP | 寄存器、线圈 | USB-RS485、Modbus 网关、`pymodbus` | 设备开放寄存器 |
| BACnet | 楼控点位 | BACnet/IP 网关、`BAC0` | 设备支持 BACnet |
| KNX | 组地址、开关/调光状态 | KNX/IP 网关、`knxd` | 总线已调试 |
| MQTT | 已接入设备的状态与事件 | mosquitto、任意订阅端 | 需要 broker 凭据 |
| ONVIF / RTSP | 摄像头流、移动侦测事件 | `ffmpeg`、ONVIF 客户端 | 摄像头账号 |
| SNMP | 打印机耗材、NAS 状态、UPS | `snmpwalk` | community string |
| IPP | 打印机状态/队列 | CUPS、`ipptool` | 无 |
| UPnP AV / SOAP | Sonos、电视播放状态 | SoCo、UPnP 客户端 | 无 |

## 4. 云端与物理量

| 来源 | 能拿到什么 | 前提 | 风险 |
|---|---|---|---|
| 厂商云 API（米家、Tuya、Aqara、Ring 等） | 状态、控制、事件 | 账号 token、区域服务器 | 云依赖、隐私、ToS 限制 |
| 本地云替代（MiIO、Tuya local） | 局域网内状态/控制 | 提取 token / local key | 固件升级可能失效 |
| 智能插座 / Shelly EM / CT 钳 | 功率、电量、开关 | 安装 | 涉及强电，需专业施工 |
| 电表 P1 / Modbus 电表 | 全屋用电 | 表计接口 | 同上 |
| USB 麦克风 / 摄像头 | 音频、视觉 | 物理位置 | 隐私与录音录像法规 |

## 采集策略（按成本从低到高）

1. **已有标准接入**：ESPHome、MQTT、Matter、Home Assistant 已集成的设备——直接订阅，成本最低。
2. **有本地 API 的封闭生态**：Hue Bridge、Sonos、Chromecast、电视——写桥接适配器。
3. **只有无线信号**：BLE、Zigbee、433 MHz——加嗅探器/协调器；能拿元数据和未加密广播，加密内容需要网络密钥。
4. **完全封闭**：云锁、蜂窝设备、无本地接口的摄像头——只能云 API，或拆机（成本与风险最高）。

## 和 agent 的关系

- **T0/T1 设备**：自己是数据源，通过上面的路径把状态喂给编排层；执行也走同一路径。
- **T2/T3 主机**：直接跑 agent，同时充当嗅探器/桥接器的宿主（USB 口接 CC2652、RTL-SDR、RS485）。
- **没有信号的设备**：多数不是“没有数据”，而是“数据在厂商云里”。先确认能不能本地化，再决定是否值得接。
