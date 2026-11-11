# 异步 Missions

Mission 是可持久化、可审批的顺序动作计划。`run` 只负责持久入队，立即返回 `running`；
后台 worker 推进动作，HTTP 请求和 MCP 工具不等待手机执行。

```text
proposed → approved → running → completed（无显式验证条件）
                        ├───→ verified（显式验证通过）
                        ├───→ failed
                        └───→ cancelled
```

## 创建、审批和调度

`POST /api/missions`：

```json
{
  "goal": "锁屏时提醒喝水并播报",
  "actions": [
    {"device_id":"alex_phone","capability":"notify","arguments":{"title":"喝水提醒","text":"休息一下，喝杯水。"}},
    {"device_id":"alex_phone","capability":"speak","arguments":{"text":"该喝水了。"}}
  ],
  "verify": [{"device_id":"alex_phone","field":"completed","equals":true}],
  "delay_s": 30,
  "timeout_s": 300,
  "requires_approval": true
}
```

- `actions` 只接受 device_id / capability / arguments / confirm，不接受脚本或任意 HTTP。
- `delay_s` 是从调用 `run` 起计算的延迟（0～30 天）；可用于给用户留出锁屏时间。
- `timeout_s` 是计划开始后允许的总时长（10～86400 秒），覆盖动作、等待回执和验证。
- 每个终端命令另有 adapter 配置的有效期（默认 120 秒）。总 Mission 时长更长不会延长单条命令。
- 每个动作依然通过 AccessHub 校验、策略和审计；审批 Mission 不替代高风险动作的确认。

```text
POST /api/missions/<id>/approve  {"confirm":true,"actor":"owner"}
POST /api/missions/<id>/run      {}
GET  /api/missions/<id>
POST /api/missions/<id>/cancel   {}
```

`run` 在 running 状态重复调用不会创建第二个 job。已完成/失败/取消的任务不能重新运行；
确实要再次执行时创建新 Mission，避免无意重放副作用。

返回的 `result` 包含：

- `phase`：ready / dispatching / waiting / verifying / completed / verified / failed / cancelled；
- `next_action`：当前步骤索引；
- `scheduled_at` / `deadline`：Unix 秒；
- `actions`：已完成步骤的结果，包括手机的真实回执；
- 等待期间的 `command_id`，便于与终端审计关联。

`completed` 表示所有步骤正常返回或获得成功回执，但没有单独的 verify 条件。
`verified` 需要显式条件全部匹配。通知回执 `published:true` 不代表人读过；
TTS 回执 `completed:true` 代表引擎完成，不保证用户听到。

## 回执和验证

终端动作先进入队列，worker 等到该 command_id 的 succeeded 回执才启动下一步。
failed / unknown / expired / cancelled 回执都会终止 Mission，后面的步骤不会执行。

终端 verify 的字段从**本次 Mission 的成功回执**合并读取，可使用 `level`、`charging`、
`published`、`completed` 等字段，支持点路径；不会用设备图中的旧缓存充当新证据。
同步设备 verify 会经过 AccessHub 执行新的 `read`；短暂不匹配最多等待 5 秒，且不超过总截止时间。

传输失败可重投相同 command_id，客户端去重并重发本地回执。失败/未知动作不会自动生成新命令重做。
这让网络重试与物理动作重试保持明确区分。

## 恢复、租约和取消

- Mission 与 worker 进度存储在同一个 SQLite 数据库，入队和状态切换使用事务。
- 每次领取 job 获得 60 秒租约，更新进度校验 owner 和租约，旧 worker 不能覆盖新进度。
- 终端步骤先持久记录稳定幂等键，再派发；命令队列根据该键生成唯一 command_id。
  即使“已经入队，但尚未写入等待状态”时进程退出，恢复也会使用原命令。
- 同步设备若在派发后进程退出、无法确定动作是否完成，标为 failed 并报告 unknown outcome；
  不自动重复开锁、切换开关等动作。
- 重启后领取崩溃 worker 的 job 可能需要等待剩余租约，最长约 60 秒。
- 取消运行中的 Mission 会取消当前排队命令，并写入取消标记阻止旧 worker 重新入队。
  已被手机领取、开始执行的动作不能被撤回；晚到回执不能把 cancelled 改成成功。
- 取消清理可重复执行，服务器重启后会继续完成。
  adapter 暂不可用时，result 中的 cancellation_pending / cancellation_error 记录待清理状态，
  不阻塞其他任务；adapter 恢复后继续清理。
- 升级前遗留的 running Mission 没有步骤检查点，将报告结果不确定并失败，不会重放。

本版仍采用一个共享 runtime 管理设备。租约保护 worker 领取，不代表所有 adapter、设备绑定和
终端 token 已支持多服务实例集群。没有暂停/人工恢复接口，也不承诺对物理副作用的 exactly-once。
当前 worker 在单线程中推进任务，慢同步 adapter 会延迟其他任务执行，但不会阻塞新的入队和取消请求。

## 生命周期与 MCP

`southbound serve` 自动启动/停止 worker；本地 MCP 的 Mission 服务也会启动 worker。
连接真实 Android 时建议 MCP 使用 `--remote` 连接共享服务。
Python 嵌入调用需执行 `service.start()`，退出时调用 `service.close()`。

- `iot_mission_create` 支持 delay_s / timeout_s；
- `iot_mission_control` 的 run 为异步调度，approve / cancel 保持显式操作；
- `iot_mission_status` / `iot_missions` 只读，不靠状态查询触发执行；
- 步骤及完成/失败事件仍进入 EventBus 和 observations。

## 手机锁屏验收

1. 按 [终端指南](TERMINAL_PROTOCOL.md) 配对，允许通知、锁屏联网，并开启后台接收。
2. 创建上面的 Mission，approve、run 后立即锁屏。
3. 确认通知先发布、播报后完成，查询 Mission 最终为 verified。
4. 断网后重新连接，确认没有重复通知/播报；截止时间到了则为 failed。
5. 在 waiting 阶段重启服务，确认 command_id 保持一致。
6. 取消等待中的任务，确认后续步骤不再派发。

自动测试通过模拟回执覆盖顺序、延迟、超时、租约、取消和崩溃窗口；真实锁屏、Doze 和各厂商
后台限制需在目标手机验收，不能用服务端测试替代。
