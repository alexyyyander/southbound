# Southbound Skills

这是 **skill contract** 目录。每个 `*.skill.json` 描述一个边缘硬件场景：触发信号、需要的 capability、动作、guard、verify、fallback、action tier、硬件前提和隐私边界。

- `presence_lighting` — 门磁/存在触发开灯，回读验证
- `air_quality_purifier` — PM2.5 持续偏高时启动净化器，带迟滞
- `nfc_checkin` — NFC 标签读取与 check-in 事件
- `energy_guard` — 过流保护的 C 级人工审批示例
- `camera_observation` — 本地摄像头 snapshot + 结构化 observation（计划）
- `drone_patrol` — 受监督的室内 waypoint 巡检（计划）

## 当前状态

- `SkillRegistry` 会加载并校验所有 manifest；
- `SkillRunner` 负责 trigger/guard/cooldown、shadow/proposed、AccessHub 执行、verify/fallback；
- MCP：`iot_skills`、`iot_skill_describe`、`iot_skill_run`、`iot_skill_status`；
- CLI：`southbound skills`、`southbound skill <id> --force --mode supervised`；
- 任何真实执行都必须经过 `SouthboundRuntime.call()` → `AccessHub` → policy/audit/readback；
- `camera_observation` 和 `drone_patrol` 仍是计划项，不代表当前 adapter 已支持。

完整字段和边缘硬件清单见 [docs/EDGE_SKILLS.md](../docs/EDGE_SKILLS.md)。
