# Missions

Mission 是可持久化、可审批的设备动作计划，不是一段自由 prompt。

```text
proposed → approved → running → verified
     └──────────────→ cancelled
                    └→ failed
```

## 创建

`POST /api/missions`：

```json
{
  "goal": "打开客厅灯并确认状态",
  "actions": [
    {"device_id": "living_light", "capability": "turn_on", "arguments": {}}
  ],
  "verify": [
    {"device_id": "living_light", "field": "on", "equals": true}
  ],
  "requires_approval": true
}
```

- `actions` 只能是 `device_id` / `capability` / `arguments` / `confirm`；
- 不允许 `script`、`shell`、任意 HTTP；
- 每个动作仍经过 `SouthboundRuntime.call()` → `AccessHub` 的策略、审计和回读；
- `requires_approval=true` 时初始状态为 `proposed`。

## 审批与执行

```text
POST /api/missions/<id>/approve  {"confirm": true, "actor": "owner"}
POST /api/missions/<id>/run
POST /api/missions/<id>/cancel
```

- 审批需要显式 `confirm=true`；
- `run` 只接受 `approved`；
- 动作失败或 state verification 不匹配 → `failed`；
- 成功的 mission → `verified`；
- Mission 生命周期与每个 action 都写入 observations。

Mission approval 不等于高风险动作的 `confirm`；action 本身仍需声明 `confirm`，AccessHub 仍执行策略。

## MCP

- `iot_missions`：列表；
- `iot_mission_create`：创建；
- `iot_mission_status`：读取；
- `iot_mission_control`：approve / run / cancel。

## 当前边界

- Mission 执行是同步的；长任务后台 worker、暂停/恢复、重试和分布式锁是下一阶段；
- 没有对应 adapter 的 action（例如 drone）会被 preflight/policy 阻止；camera
  snapshot 已随 0.14.0 可用，可作为 mission 的 verify/evidence 步骤；
- mission 记录只保存非敏感元数据、动作结果和 verification，不保存原始媒体。
