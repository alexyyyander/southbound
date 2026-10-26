# 同类项目与定位

搜索时间：2026-10-26。Stars 为当时近似值，会随项目变化。

## GitHub 上的相邻项目

| 项目 | Stars | 它是什么 | 与 Southbound 的重叠与差异 |
|---|---:|---|---|
| [homeassistant-ai/ha-mcp](https://github.com/homeassistant-ai/ha-mcp) | ≈5.2k | Home Assistant 的 MCP server | 生态最大，但绑定 Home Assistant；不做 LAN 设备发现、T0–T3 能力分级、模拟器和多协议接入抽象 |
| [TheThingSystem/steward](https://github.com/TheThingSystem/steward) | 345 | 2013 年的本地 IoT 中枢，连接 Wi-Fi/Zigbee/Z-Wave/BLE | 愿景最接近的“前辈”；已停更，没有 agent/MCP、策略/审计和模拟器 |
| [GetBusbar/busbar](https://github.com/GetBusbar/busbar) | 214 | AI agent 执行控制平面，治理 MCP 工具调用/A2A | 与策略/审计层重叠；不处理 IoT 协议与设备发现 |
| [goweft/heddle](https://github.com/goweft/heddle) | 14 | MCP 工具服务器的策略与信任层 | 与 `AccessHub` 的策略层重叠；不是 IoT 接入层 |
| [agentsea/devicebay](https://github.com/agentsea/devicebay) | 7 | 给 AI agent 用的可插拔设备与 UI | 设备抽象层接近；不是本地 LAN 多协议接入层 |
| [rodrigolinhas/lan-observatory](https://github.com/rodrigolinhas/lan-observatory) | 0 | Go CLI/TUI 的局域网发现、清点与变更观测 | 与发现/可视化重叠；没有设备控制和工具层 |
| [LachlanB96/zigbee-mcp](https://github.com/LachlanB96/zigbee-mcp) | 0 | Zigbee2MQTT 的 MCP server | 单协议 |
| [0x1abin/matter-controller-mcp](https://github.com/0x1abin/matter-controller-mcp) | 8 | Matter controller MCP | 单协议 |
| [Yeelight/yeelight-iot-mcp](https://github.com/Yeelight/yeelight-iot-mcp) | 10 | Yeelight 智能照明的 MCP 服务 | 厂商专用 |
| [emqx/esp32-mcp-mqtt-tutorial](https://github.com/emqx/esp32-mcp-mqtt-tutorial) | 27 | ESP32 + MCP over MQTT 教程 | 设备侧示例，不是完整接入层 |
| [apache/iotdb-mcp-server](https://github.com/apache/iotdb-mcp-server) | 39 | IoTDB 数据库 MCP | 数据库查询，不是设备控制 |

## 没有找到直接等价的项目

现有项目分别覆盖了其中一块：

- Home Assistant MCP：家庭自动化生态，但绑定单一平台；
- Busbar / Heddle：agent/MCP 的策略与执行控制，但不接设备协议；
- DeviceBay：设备抽象，但不是本地 LAN 发现与多协议桥；
- LAN Observatory：发现与观测，但不控制设备；
- Zigbee/Matter/Yeelight MCP：单协议或厂商专用；
- The Thing System：本地 IoT 中枢的早期实现，但没有 agent 时代的接口、策略和审计。

Southbound 的差异化是把这个组合做成一条完整的工具链：

1. **LAN 发现 + T0–T3 能力分级**：先知道设备能做什么，再决定给 agent 什么工具；
2. **统一接入工具层**：`DeviceBinding` / `Capability` / `Tool Catalog` / `iot_call`；
3. **策略与审计**：高风险动作不能绕过 `AccessHub`；
4. **多协议适配**：Simulator、Home Assistant、MQTT、Zigbee2MQTT；
5. **无硬件闭环**：模拟器 + 可视化面板 + 确定性 agent trace；
6. **本地优先**：默认只监听 `127.0.0.1`，不依赖云。

## 名字

**Southbound** 取自 IoT/电信架构里的 southbound interface：北向接口面向应用与 agent，南向接口面向设备。这个项目正是 agent 的南向接入层。

- GitHub 上无同名大型项目；`alexyyyander/southbound` 可用；
- PyPI 与 npm 的 `southbound` 均未被占用；
- 中文可写“南向”，与 `northbound/southbound` 的行业术语一致；
- CLI、包名、仓库名可以统一为 `southbound`。

备选名与冲突记录：`Wireway`（可用但偏泛）、`Patchpanel`（PyPI 已占用）、`Inglenook`（npm 已占用）、`Busbar`（已有 agent 控制平面）、`Heddle`（已有 MCP 策略层）、`Lantern`/`Switchyard`/`Backplane`/`Patchbay`（同名项目较多）。
