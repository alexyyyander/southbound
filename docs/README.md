# 文档索引

- [架构拆解图](ARCHITECTURE_BREAKDOWN.md) — Agent harness、MCP、Southbound core、adapters、物理设备与部署 profile
- [共享 Runtime Service](RUNTIME_SERVICE.md) — 一个设备图供 Dashboard / MCP / CLI 共用，远程调用与安全边界
- [多硬件交互](INTERACTION.md) — 端点、会话、模态路由、跨端 handoff 与 `/interaction` 面板
- [BLE / NFC 与设备事件](HARDWARE_ADAPTERS.md) — 统一适配器注册、真实驱动、模拟演示与验证边界

- [About Southbound](ABOUT.md) — 项目定位、命名由来、设计原则与边界
- [演示](DEMO.md) — 面板、2.5D 房间、CLI、MCP、受控接入与故障排查
- [2.5D 场景与 3DGS](SCENE.md) — 当前控制图、坐标映射与高斯泼溅视觉层规划
- [Survey 与 Harness 部署](HARNESS.md) — 无主机 survey、Pi 最小 harness、硬件与验收
- [协议与 API](PROTOCOL.md) — northbound/southbound 契约、HTTP、SSE 事件与数据模型
- [技术架构与可行性](ARCHITECTURE.md) — 组件、数据契约、部署形态、可行性矩阵与路线图
- [接入工具层](ACCESS_LAYER.md) — 设备/能力模型、策略、审计、适配器、MCP/CLI/HTTP 接口
- [受控设备接入](ONBOARDING.md) — 发现 → 授权 → 绑定 → Tool Catalog；持久化与安全边界
- [同类项目与定位](COMPARISON.md) — GitHub 相邻项目、差异化与命名理由
- [端侧芯片能力](CHIPS.md) — T0–T3 芯片、内存与模型规模
- [数据信号采集](SIGNALS.md) — 协议桥接、无线嗅探、物理量
- [最小采购与刷机清单](HARDWARE.md) — ESP32 / Zigbee / Matter 的 BOM、接线、刷机与配网
- [最近一次扫描报告](SCAN_REPORT.md) — 192.168.1.0/24 的实测结果、风险与下一步

品牌与图标资源见 [assets/README.md](../assets/README.md)。

![局域网观测站面板](dashboard.jpg)
