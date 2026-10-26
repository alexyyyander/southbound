# 端侧 agent 芯片能力调研

判断一颗芯片“能不能跑 agent”，先要定义 agent。这里把它拆成四件事：

1. **模型**：能本地推理（哪怕只是意图分类）。
2. **工具调用**：能读写传感器/执行器，或调用局域网服务。
3. **循环与记忆**：能观察 → 决策 → 执行 → 再观察。
4. **策略与护栏**：知道什么不能做。

按这个定义，绝大多数 IoT 芯片跑不了“完整 agent”，但很多能跑**反射型 agent**（唤醒词、意图、异常检测），规划部分上送到局域网主机。真正能跑完整 tool-calling 循环的是 Linux SoC 及以上的设备。

## 分级总表

| 档位 | 典型平台 | 算力 / 内存 | 能跑的 agent 形态 | 典型功耗 |
|---|---|---|---|---|
| T0 | ESP8266、STM32F0/F1/F4、nRF52832、CC2530、8 位 MCU | <100 MHz，KB 级 RAM | 不能跑 agent；纯传感器/执行器 | mW 级 |
| T1 | ESP32-S3/P4/C6、STM32N6、Alif E7/E8、RA8P1、BL808、K210、nRF5340 | 100 MHz–1 GHz + DSP/NPU，0.1–0.6 TOPS，0.5–16 MB RAM | 唤醒词、关键词、意图分类、异常检测、tiny vision；“反射型 agent”，规划上送 | 10 mW–1 W |
| T2 | RK3588/3588S、RK3566、Pi 5、N100/N305、Orange Pi 5、Synology | 4–8 核 + 0–6 TOPS NPU，4–32 GB RAM | 0.5B–3B 量化 LLM + 完整工具调用循环 | 3–30 W |
| T3 | Jetson Orin Nano Super/NX、RTX 小主机、Mac mini M4/M5 | 30–100+ TOPS，8–64 GB RAM | 3B–8B+，多模态、视觉/语音闭环 | 10–100 W |

## T1：微控制器 + 微型 NN

这些芯片能跑“端侧反射”，跑不了通用 LLM。关键指标是 SRAM/PSRAM 和是否有向量/DSP/NPU。

- **ESP32-S3**：双核 Xtensa LX7 240 MHz，带向量指令；512 KB SRAM + 最多 8 MB PSRAM；Wi-Fi/BLE。乐鑫 ESP-SR 可做唤醒词和命令词，ESP-DL 可跑小型视觉/音频模型。适合做语音入口、传感器融合、本地规则执行。作为 agent 节点，它负责“听懂一句话并调用本地工具”，复杂规划交给局域网主机。
- **ESP32-P4**：双核 RISC-V 400 MHz，768 KB SRAM + PSRAM，带 MIPI-CSI/DSI，无无线；搭配 ESP32-C6 做 Wi-Fi 6/Thread。适合摄像头/音频前端，能跑比 S3 更大的视觉模型。
- **ESP32-C6**：RISC-V 160 MHz，Wi-Fi 6 + BLE + Thread + Zigbee；算力有限，但它是 Matter/Thread 设备最常用的桥。
- **STM32N6**：Cortex-M55 800 MHz + NeoChrom NPU（约 600 GOPS）；ST Edge AI / TFLite Micro，能跑 MobileNet 级视觉和关键词模型。
- **Alif Ensemble E7/E8**：Cortex-M55 + Ethos-U55（E8 为双 M55），面向低功耗视觉/音频推理。
- **Renesas RA8P1**：1 GHz Cortex-M85 + Ethos-U55（约 256 GOPS），MCU 里算力靠前的一档。
- **Kendryte K210**：双核 RISC-V 400 MHz + KPU（约 0.8 TOPS），老一代视觉方案，工具链维护一般。
- **Bouffalo BL808**：三核 RISC-V + NPU，Wi-Fi/BLE/Zigbee 三模，适合低成本多协议节点。
- **nRF5340 / nRF54 系列**：BLE/Thread 强项，CMSIS-NN 可跑极小模型；不要指望 LLM。

**结论**：T1 能承担“端侧 agent 的手和耳朵”，不能承担“大脑”。如果目标是全屋联动，T1 设备应通过 ESPHome/MQTT/Matter 暴露能力，由 T2/T3 主机编排。

## T2：Linux SoC（能跑完整 agent 的最低档）

这是“端侧 agent”真正的起点：Linux、足够内存、能跑 llama.cpp/RKLLM/MLX 这类推理栈，并且能装 MCP 工具。

- **Rockchip RK3588 / RK3588S**：4×A76 + 4×A55，Mali-G610，NPU 6 TOPS（INT8），8–32 GB。RKLLM 可跑 Qwen2.5-1.5B/3B、Llama 3.2 3B 等，3B 量化模型在良好散热下约 8–20 tok/s。是目前性价比最高的本地 agent 主机之一（Orange Pi 5、Radxa Rock 5B 等）。
- **RK3566 / RK3568**：4×A55，NPU 约 0.8 TOPS，4–8 GB；适合 0.5B–1.5B 模型，做编排和轻量对话。
- **Raspberry Pi 5**：BCM2712，4×A76 2.4 GHz，8 GB；无 NPU，纯 CPU 跑 1B Q4 约 5–12 tok/s，3B 会明显变慢。它的价值在生态和 GPIO，适合做编排主机而非聊天主力。
- **Intel N100 / N305 迷你主机**：x86 + 8–16 GB，llama.cpp 跑 1B–3B 很稳，兼容性最好；功耗 6–15 W。
- **Synology / QNAP NAS**：容器里跑编排、MQTT、Home Assistant 很合适；跑 LLM 取决于 CPU 和内存，通常只建议 1B 级。
- **Orange Pi 5 / Rock 5B**：本质是 RK3588 平台，注意散热和电源。

内存估算（Q4 量化，含 KV cache 余量）：

| 模型规模 | 权重 | 建议最小内存 |
|---|---|---|
| 0.5B | ~0.4 GB | 2 GB |
| 1B | ~0.7 GB | 2–3 GB |
| 3B | ~2 GB | 4 GB |
| 7B | ~4.5 GB | 8 GB |
| 8B | ~5 GB | 8–12 GB |

## T3：边缘 GPU / 统一内存

- **Jetson Orin Nano Super（8 GB，67 TOPS）**：能跑 3B–8B 量化模型，支持视觉/语音多模态，是机器人/多摄像头场景的常见选择。
- **Jetson Orin NX（16 GB，100 TOPS）**：8B 级更从容。
- **RTX 4060/5060 小主机**：8B–14B，适合固定放在弱电箱/机柜。
- **Mac mini M4/M5（16–64 GB）**：MLX 生态，8B–30B 都能跑；作为家庭编排主机非常合适，但功耗高于 ARM SBC。

## 跑“简单 agent”的最低门槛

- **模型**：1B–3B 量化模型 + 工具调用模板（Qwen2.5/3、Llama 3.2、Phi-4-mini、Gemma 3 1B/4B、SmolLM2）。
- **内存**：至少 4 GB（3B 模型 + 运行时）；8 GB 更稳。
- **工具层**：MCP 或函数调用；设备能力要以 schema 暴露，而不是写死在 prompt 里。
- **可靠性**：1B 模型的工具调用经常出错；3B 是可用的下限，7B+ 才比较稳。
- **延迟**：本地 3B 在 T2 上首 token 约 0.5–2 秒，适合“意图 → 工具”而不是实时对话。

## 不能跑 agent 的芯片怎么办

它们不是废掉了，而是变成“数据源 + 执行器”：

- 有本地协议（ESPHome/MQTT/Matter）→ 直接接入。
- 有厂商本地 API（Hue、Sonos、Cast）→ 桥接。
- 只有无线信号（BLE/Zigbee/433）→ 嗅探器/网关采集，见 [SIGNALS.md](SIGNALS.md)。
- 完全云锁定（部分门锁、摄像头、蜂窝设备）→ 只能云 API，或放弃本地化。

## 选型建议

- **只想让全屋设备联动**：T2 主机（RK3588 或 N100）+ Home Assistant + MQTT/Matter，不必追求 T3。
- **要语音/视觉入口**：ESP32-S3/P4 做端侧唤醒与预处理，T2 做规划。
- **要机器人/多摄像头**：Jetson Orin Nano Super 起步。
- **要跑 8B 以上**：Mac mini / RTX 小主机，或直接放一台旧 PC。

> 注意：以上 tok/s 为量级估计，受量化方式、内存带宽、散热和推理栈影响很大；采购前最好用目标模型实测。
