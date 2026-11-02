# Related Work & Competitive Landscape

- 检索日期：2026-11-01
- 范围：agentic IoT、LLM/agent 控制物理设备、MCP 工具层、WoT/意图网络、数字孪生、边缘智能、MCP 安全
- 方法：GitHub 搜索、Crossref/OpenAlex、arXiv、官方规范文档；不是系统综述，stars/时间会变化
- 结论先行：**这个方向已有类似研究，而且不是边缘想法。最接近的是 Ori，第二接近的是 AssetOpsBench 的 MCP/多 agent 工业资产框架。Southbound 必须靠“横向接入与治理层”的定位差异化，而不是宣称首创。**

## 1. 最接近的开放项目

### Ori Runtime — 最直接的同方向项目

[ori-platform/ori-runtime](https://github.com/ori-platform/ori-runtime)（Python，Apache-2.0，2026-03 创建，2026-10 仍活跃）

Ori 把自己定义为 **offline-capable agentic IoT runtime with physical actuation trust**，运行在 Raspberry Pi 上：

- Tier 1 规则引擎（微秒级、离线）
- Tier 2 本地 SLM（约 3–8 秒、离线）
- Tier 3 网关 LLM（LAN/云端可选）
- Action Tier A–D：告知 / 软物理 / 硬物理审批 / 安全关键自动切断
- GPIO、I2C/SPI、Modbus RTU、MQTT、CoAP、OPC-UA、Zigbee（经 MQTT）、LoRaWAN
- 硬件 circuit breaker、SQLite 状态、签名证据链、技能/扩展机制
- 明确面向能源、工业、家庭自动化和基础设施

**对我们的意义**：Ori 已经覆盖了“边缘 runtime + 分级推理 + 物理动作授权”这一层，而且硬件/HAL/安全叙事比 Southbound 更深。Southbound 不能只是“另一个 Ori”。

### AssetOpsBench — 最接近的评估/基准

[IBM/AssetOpsBench](https://github.com/IBM/AssetOpsBench)（Python，Apache-2.0，2025-05 创建）

- 面向 Industry 4.0 资产运维的 AI agent benchmark/framework；
- 多个领域 MCP server：IoT、FMSR、TSFM、Work Order、Vibration；
- 支持 Plan-Execute、Deep Agent、Claude Agent、OpenAI Agent；
- KDD 2026、NeurIPS/AAAI/ACL 等论文与竞赛；
- 重点是**可复现场景、工具编排、评估**，不是设备 runtime。

**对我们的意义**：它验证了“MCP + 多 agent + 工业设备”是成立的研究方向；Southbound 可以把 `simulator/scene` 做成类似的 evaluation harness，而不是只做 demo。

### Home Assistant MCP 生态

[homeassistant-ai/ha-mcp](https://github.com/homeassistant-ai/ha-mcp)（Python，MIT，约 5.4k stars）

- 把 Home Assistant 的设备、自动化、仪表盘暴露给 MCP 客户端；
- 生态最大、最实用，但绑定 Home Assistant；
- 不做跨平台 LAN 发现、T0–T3 分级、受控 onboarding、策略/审计、模拟器。

**对我们的意义**：这是最现实的上层替代/竞品。Southbound 的 HA adapter 可以共存；但“MCP + 智能家居”已经被大量实现，单靠 MCP 不是差异化。

### 其他相邻项目

| 项目 | 类型 | 与 Southbound 的关系 |
|---|---|---|
| Home Assistant / Node-RED / OpenHAB | 本地自动化平台 | 强生态，规则/流程强；不是 agent 工具治理层 |
| Eclipse Ditto / EdgeX / ThingsBoard / FIWARE | 数字孪生/边缘/设备管理 | 设备状态与协议管理强；不是 LLM agent 的 capability/policy 层 |
| Busbar / Heddle | agent/MCP 策略与执行控制 | 与 AccessHub 的策略/审计重叠；不接 IoT 协议 |
| The Thing System / steward | 早期本地 IoT 中枢 | 愿景接近，已停更；没有 MCP/agent 时代接口 |
| Matter / Zigbee2MQTT / 单协议 MCP | 协议/设备接入 | 是 Southbound 的 adapter 生态，不是完整控制面 |
| W3C WoT | 标准 | Thing Description 与我们的 DeviceBinding/Capability 很接近，应该对齐 |

## 2. 学术研究脉络

### Agentic IoT / IoT 2.0 / Internet of Agents

- **Agentic AI for Autonomous Internet-of-Everything: A Distributed Three-Tier Intelligence Ecosystem**（2026，DOI 10.1109/miot.2026.3726608）
  - 三层智能生态与 T0–T3/边缘–网关–云分层高度相关。
- **IoT 2.0—The Internet of Agents for Next-Generation Smart Cities**（2026，DOI 10.1201/9781003686859-7）
  - 把 IoT 2.0 定义为 agent 互联网，而不是纯设备互联网。
- **Securing IoT 2.0: Safety, Trust, and Governance for the Internet of Agents**（2026，DOI 10.1201/9781003686859-8）
  - 安全、信任、治理是 IoT 2.0 的核心问题。
- **Vertical Integration of Agentic AI into IoT**（2026，DOI 10.1109/aiiot68874.2026.11569636）
- **Agentic AI and Large Language Models for Autonomous IoT Cybersecurity: A Systematic Survey, Taxonomy, and Research Roadmap**（2026，DOI 10.3390/electronics15122740）

### LLM + 智能家居 / 边缘–云推理

- **LLMSwitchBench: A New Edge-Cloud Routing Benchmark for Smart Home LLM Inference**（2025，DOI 10.1109/access.2025.3632276）
  - 直接研究“哪些推理放边缘、哪些放云”，对应我们的 T0–T3/本地优先。
- **DomoticaBrain: A Precision-Oriented LLM Inference Approach for Smart Home Devices**（2025，DOI 10.1109/ieir67391.2025.11491323）
- **SmartTinkerer: Bridging Smart Home Testing Gaps with LLM, Digital Twin & Reinforcement Learning**（2025，DOI 10.1109/compsac65507.2025.00182）
  - LLM + 数字孪生 + RL 做智能家居测试，和我们的 simulator/scene/eval 很接近。

### 意图驱动网络（Intent-Based Networking）

- **IETF RFC 9315: Intent-Based Networking — Concepts and Definitions**（2022）
  - 北向 intent、南向执行、验证闭环的标准概念来源。
- **Maestro: LLM-Driven Collaborative Automation of Intent-Based 6G Networks**（2024，DOI 10.1109/lnet.2024.3503292）
- **LLM-based Conversational Assistant for Intent-driven Management**（2025，DOI 10.1109/nfv-sdn66355.2025.11349446）

### MCP / Agent 安全

- **A Survey on Security of the Model Context Protocol: Documented Incidents, Defense Frameworks, and Coverage Gaps in Agentic AI**（2026，DOI 10.1109/icosics70095.2026.11746554）
- **ZT-MCP: A Zero-Trust Security Architecture for MCP-Connected AI Agents**（2026，DOI 10.1109/coins69995.2026.11715596）
- **MCP Under Attack: Security Risks in the Model Context Protocol Ecosystem**（2026，DOI 10.2139/ssrn.7330358）
- **Auditable Access Control for Physical AI Agents in Multi-Organisation Spaces**（2026，DOI 10.1109/dcoss-iot69657.2026.00064）
  - 与 Southbound 的 policy/audit/approval 直接相关。

### 标准与经典模型

- **W3C Web of Things (WoT) Thing Description / Architecture**：设备能力描述、协议绑定、交互抽象；与我们的 `DeviceBinding` / `Capability` / Tool Catalog 最接近的标准。
- **IETF RFC 9315**：intent-based networking 的概念定义。
- **MAPE-K**（Kephart & Chess, 2003, DOI 10.1109/MC.2003.1160055）：Monitor–Analyze–Plan–Execute over shared Knowledge；我们的 signal → policy → action → verify → event 就是 MAPE-K 的 IoT 版本。
- **Matter / Zigbee / Thread**：南向协议与生态，不是 agent 层。

## 3. Southbound 的真实差异

### 我们重叠的部分

- WoT/DeviceBinding：设备能力抽象；
- Busbar/Heddle/Ori：策略、审批、审计；
- Home Assistant/Node-RED/OpenHAB：本地自动化与设备接入；
- AssetOpsBench：MCP 工具、多 agent、评估；
- SmartTinkerer：数字孪生/模拟测试；
- IETF IBN：intent → action → verification。

### 我们相对独特的组合

**MCP-native、vendor-neutral 的“接入 + 治理 + 评估”工具面**：

1. 跨厂商 adapter（Sim、HA、MQTT、Z2M、BLE、NFC）而不是绑定单一平台；
2. `AccessHub` 统一参数校验、风险、策略、审计、回读；
3. 受控 onboarding + `CommissionStore`；
4. `southbound survey` 现场接入评估；
5. deterministic simulator + 2.5D/Splat scene，作为 agent/eval 的测试台；
6. MCP/HTTP/CLI 三种入口共享同一 runtime。

### 我们目前明显不如 Ori 的部分

- 没有 GPIO/I2C/Modbus/OPC-UA/HAL 级硬件 runtime；
- 没有本地 SLM 分级推理（Tier 2）；
- 没有 A–D 物理动作权限框架；
- 没有工业现场、签名证据链、硬件 circuit breaker；
- 没有真实硬件安全认证/现场验证。

**结论：Southbound 不应宣称“首个 agentic IoT runtime”或“完整物理安全 runtime”。** 更准确的定位是：

> **Southbound = MCP-native device access & governance plane.**
> Ori/HA/EdgeX 是它可以接入或前置的 runtime/platform；Southbound 负责跨厂商发现、能力建模、受控接入、策略审计、survey/eval 和可视化。

## 4. 建议的研究/产品方向

1. **对齐 W3C WoT**：导出/导入 Thing Description，把 Capability 映射到 WoT TD；这会让 Southbound 更容易被研究和企业采用。
2. **补 Action Tier + MAPE-K**：把当前 read/low/medium/high 映射到 A–D；增加 monitor/analyze/plan/execute 的显式状态机与回滚。
3. **MCP 安全**：学习 ZT-MCP/MCP Under Attack，加入工具级 scope、prompt-injection 防护、auth、审计关联、沙箱和 rate limit。
4. **建立 eval harness**：参考 AssetOpsBench/SmartTinkerer，把 simulator 场景做成可复现 benchmark，而不仅是 demo。
5. **与 Ori/HA 互操作**：写 Ori adapter 或 MCP bridge，而不是重写 HAL；Southbound 可以成为 Ori/HA 之上的统一 agent 工具面。
6. **保持本地优先**：用 LLMSwitchBench 的思路做 edge/cloud 路由；不要在 Pi 上强跑大模型。

## 5. 参考来源

- Ori Runtime: https://github.com/ori-platform/ori-runtime
- AssetOpsBench: https://github.com/IBM/AssetOpsBench
- AssetOpsBench paper: https://arxiv.org/pdf/2506.03828
- Home Assistant MCP: https://github.com/homeassistant-ai/ha-mcp
- W3C WoT Thing Description: https://www.w3.org/TR/wot-thing-description/
- W3C WoT Architecture: https://www.w3.org/TR/wot-architecture/
- IETF RFC 9315: https://www.rfc-editor.org/rfc/rfc9315
- MCP specification: https://modelcontextprotocol.io/
- OpenAlex/Crossref records for the DOIs listed above.
