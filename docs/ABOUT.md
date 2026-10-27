# About Southbound

**Southbound** 是一个本地优先的 IoT Agent 南向接入层：把局域网里的设备变成 agent 可以安全调用的工具。

它解决的不是“再做一个智能家居 App”，而是 agent 与设备之间缺失的那一层：

- 设备发现：ARP / mDNS / SSDP，判断设备类型与 agent 档位；
- 能力建模：`DeviceBinding`、`Capability`、`Risk`、Tool Catalog；
- 受控接入：发现 → 所有者确认 → 绑定 adapter/策略 → 工具目录；
- 执行与治理：`iot_call`、策略范围、高风险确认、审计日志；
- 可观察：SouthboundRuntime、事件总线、SSE、可视化面板；
- 可演示：2.5D 房间实验（presence → 自动反射 → 回读验证 → Agent 通知）与合成 splat 预览；
- 可接入：MCP、CLI、HTTP。

## 名字

**Southbound** 取 IoT/电信架构里的 *southbound interface*：北向接口面向应用与 agent，南向接口面向设备。

- **品牌/显示名**：`Southbound`（大写 S）
- **Python 包名 / CLI / 仓库 slug**：`southbound`（小写）
- PyPI 会按 PEP 503 规范化包名，Unix 命令按惯例小写，GitHub 仓库 slug 也通常小写

所以全小写不是“品牌调性”，只是技术标识符的约定。文档、Logo、社交预览和正式介绍统一使用 **Southbound**。

## 适合谁

- 想给本地设备加 agent 能力，但不想把协议逻辑塞进 prompt 的人；
- 想把 Matter/Zigbee/ESPHome/MQTT 设备统一成一套工具接口的人；
- 想在无硬件情况下开发、测试 agent 工具层的人；
- 关心策略、审计、高风险设备边界的人。

## 设计原则

1. **本地优先**：发现、策略、审计、工具层默认都在本机；面板只绑定 `127.0.0.1`。
2. **工具而不是提示词**：设备能力是结构化 schema，不是自然语言描述。
3. **受控扩展**：新设备必须经过所有者确认；不做自我复制、默认口令或漏洞利用。
4. **确定性护栏**：高风险动作由策略和确认控制，LLM 不能绕过 `AccessHub`。
5. **模拟器优先**：没有硬件也能跑通完整闭环，硬件到了再替换 adapter。
6. **数据与代码分离**：扫描结果、审计、接入记录都在本地 `data/`，不进仓库。

## 它不是什么

- 不是 Home Assistant 的替代品；HA 可以作为 adapter 接入。
- 不是漏洞利用或蠕虫；没有“自动扩散到所有设备”的能力，也不会做。
- 不是生产级安全认证系统；当前是 Alpha，真实门锁/电源/燃气需要现场验证。
- 不是云端服务；默认不依赖厂商云。
- 2.5D 房间是程序化演示，不是摄像头监控；未来可选的 3DGS 视觉层只做真实感展示，不改变控制面。

## 状态

- 版本：`0.11.0`
- 阶段：Alpha
- 测试：89 项标准库 unittest
- 许可：MIT

相关文档：[架构](ARCHITECTURE.md)、[接入层](ACCESS_LAYER.md)、[受控接入](ONBOARDING.md)、[协议](PROTOCOL.md)、[演示](DEMO.md)。
