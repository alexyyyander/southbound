# 通用终端协议 v1

终端通过能力声明接入同一个 `endpoint` adapter。Android 是第一个客户端；电脑、Pi
或其他终端实现同一协议即可复用配对、策略、队列、回执和审计。

当前实现使用 HTTP JSON 轮询，不依赖 WebSocket 或 MQTT。协议契约在
`southbound/access/terminal_capabilities.py`，持久队列在 `southbound/terminals.py`，
HTTP 路由在 `southbound/terminal_http.py`，Android 客户端在 `clients/android/`。

## 分层与边界

```text
Agent / MCP / CLI
    → iot_call(device_id, capability, arguments)
    → AccessHub（参数、策略、审计）
    → EndpointAdapter（所有平台共用）
    → TerminalStore（SQLite 队列、租约）
    ↔ HTTP polling transport
    ↔ Android / 后续其他终端（系统 API 执行、持久去重、回执）
```

- 设备类型统一为 `terminal`；Android 等平台名称只作为 metadata，不增加 adapter。
- 服务端定义能力的 schema、风险和版本；终端声明自己实现了哪些版本，不能自行定义风险或执行脚本。
- 主人审批的能力必须全部被客户端实现；多报的能力不自动授予。
- Home Assistant、MQTT、BLE 等既有 adapter 保持独立，协议转换有实际差异时才增加 adapter。
- `interaction` 仍是会话/输出路由层；其中模拟 `phone-owner` 不会自动变成真实手机。
  本版真实终端使用新配对的 device_id，通过 `iot_call` 控制。尚未把 interaction 输出自动投递到手机。
- 自然语言入口已支持已配对 Android 的通知、播报、电量、硬件信息和授权截图；其他终端继续使用结构化能力调用。

## 能力契约

| 能力 | 参数 | 成功回执 result | 风险 |
|---|---|---|---|
| `notify` | `title` ≤120 字符，`text` ≤2000 字符 | `{"published":true}` | low |
| `speak` | `text` ≤2000 字符 | `{"completed":true}` | low |
| `read_battery` | 无 | `{"level":67,"charging":true}` | read |
| `show_message` | `text` ≤2000 字符 | `{"displayed":true}` | low |
| `device_info` | 无 | device / memory / storage / network / hardware 等对象 | read |
| `capture_screen` | 无 | artifact_id、尺寸、JPEG 元数据与受保护路径 | read，screen 敏感性 |
| `read` | 无 | 服务端缓存的终端信息与 last_seen | read |
| `command_status` | `command_id` | 本终端的命令状态及回执 | read |

前六项由客户端执行，后两项由服务端提供。`read_battery` 即使是只读，也需要异步取得
手机的新采样。`notify` 成功只表示客户端向系统发布通知，不代表人已看到或读完。
通知点击通过独立事件上报。所有回执都是客户端报告的证据，不是服务端的独立物理验证。

## 运行与 Android 配对

使用 Python 3.10+。在仓库根目录启动共享服务，以下是可信局域网的开发示例：

```bash
export SOUTHBOUND_RUNTIME_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 -m southbound serve --host 0.0.0.0 --port 8765
```

`serve` 默认启用空的 endpoint adapter；配置显式设置 `enabled:false` 可以关闭。
生产使用应在服务前部署可信证书的 HTTPS 反向代理；设备凭据不应通过公共网络明文传输。
Android debug APK 允许局域网 HTTP，release APK 仅允许 HTTPS。反向代理只能转发需要的终端
路由，不应把任意本地管理 API 当作匿名公网入口。

在另一个终端设置共享服务地址和相同的 owner token：

```bash
export SOUTHBOUND_REMOTE_URL=http://127.0.0.1:8765
export SOUTHBOUND_REMOTE_TOKEN='<上一步的 SOUTHBOUND_RUNTIME_TOKEN>'
python3 -m southbound terminal-pair alex_phone \
  --name '我的 Android' --room '移动终端' \
  --capabilities notify speak read_battery show_message --confirm
```

输出包含十分钟有效的一次性 `pairing_code`。构建并安装
[Android 客户端](../clients/android/README.md)，输入电脑的局域网地址
（例如 `http://192.168.1.10:8765`）和该配对码。手机中的 `127.0.0.1` 指手机自己，
Android 模拟器访问宿主机可用 `http://10.0.2.2:8765`。

配对成功后手机获得独立 token，只能轮询和回报自己的命令；无需、也不应把 owner token 填入手机。
客户端以 Android Keystore 加密保存 token。服务器只保存 token 和配对码的 SHA-256 摘要。

配对响应若丢失，配对码可能已经消费。用 owner 权限撤销该设备，再创建新配对码；
本版不以再次返回明文 token 的方式恢复丢失的响应。

## 简化接入与手机自测

推荐使用 `terminal-connect` 生成 `/connect#code=...` 链接和二维码，具体步骤见
[Android 接入](../clients/android/README.md)。无需在手机手输地址和配对码。
`serve` 提供 `/downloads/android.apk`，默认读取本仓库 debug APK；打包部署时可通过
`SOUTHBOUND_ANDROID_APK` 指定已构建的 APK 路径。未构建时返回 404，不会回退到未知安装包。

安装页使用带 package 的 Android intent 链接，App 显示目标服务器并由用户点击连接。
配对凭据位于 URL fragment，不进入 HTTP 请求路径、访问日志或 Referer；页面不加载第三方资源。
配对流程仍消费原一次性码，服务器只保存摘要。安装页的状态查询授权同时在 10 分钟后失效，
撤销设备或生成新配对码也会使旧页面失效。

新增接口均为 JSON POST：

| 路由 | 身份 | 用途 |
|---|---|---|
| `/api/terminal/setup-status` | body 中 pairing_code | 仅查看该接入链接的配对状态及当前凭据下最后一次自测结果 |
| `/api/terminal/self-test` | device token | body 仅接受 32 位十六进制 request_id；固定为自己手机 30 秒后的通知＋播报 |
| `/api/terminal/self-test/status` | device token | 使用同一 request_id 查询自己的测试 |

自测不接受 actions、device_id、脚本、任意文本或调度参数；要求已授予 notify/speak，派发时仍检查策略。
用户主动点击自测直接创建并调度固定 Mission，保留审计；这不改变普通 Mission 的审批规则。
同一凭据和 request_id 产生相同任务 ID；已创建但未入队时重试会恢复，网络失败不会产生重复动作。
一个设备同一时刻只允许一个自测，连续测试间隔至少一分钟。
更换设备凭据后不能读取旧自测。电脑页面看到的“成功”是手机回执，不能独立证明屏幕处于锁定状态。

## 调用与查结果

在 Android App 点击“连接并启用”（已配对时为“开启接收”），跟随提示允许通知和锁屏联网。常驻通知出现后可以锁屏。使用配置了远程地址和 owner token 的 CLI：

```bash
python3 -m southbound call alex_phone notify \
  --args '{"title":"喝水提醒","text":"休息一下，喝杯水。"}'
python3 -m southbound call alex_phone read_battery
python3 -m southbound call alex_phone speak --args '{"text":"欢迎回来。"}'
python3 -m southbound call alex_phone show_message --args '{"text":"客厅空气质量良好"}'
```

首次调用返回 `ok:true` 表示受理成功，`result.execution:"pending"` 表示执行尚未完成：

```json
{"ok":true,"result":{"execution":"pending","command_id":"<32位十六进制ID>","status":"queued","expires_at":1794000120}}
```

取返回的 command_id 查询：

```bash
python3 -m southbound call alex_phone command_status --args '{"command_id":"<返回的ID>"}'
```

MCP 使用 `southbound mcp --remote http://127.0.0.1:8765` 连接同一服务，并设置
`SOUTHBOUND_REMOTE_TOKEN`。现有 `iot_devices_list` / `iot_tool_catalog` / `iot_call`
会自动看到已配对的终端及获批能力，无需为每个终端新增 MCP 工具。

撤销设备会立即吊销凭据，并取消未完成队列；已经开始的手机动作不能被远程撤回：

```bash
python3 -m southbound decommission alex_phone --confirm
```

## HTTP 协议

所有接口使用 POST、`Content-Type: application/json`，body 上限 32 KiB。
除配对外，终端接口使用自己的 `Authorization: Bearer <device_token>`。

| 路由 | 身份 | 请求 |
|---|---|---|
| `/api/terminals/pairings` | owner runtime token，包括 loopback | device_id、name、room、capabilities 字符串数组、`confirm:true` |
| `/api/terminal/pair` | 一次性配对码 | protocol_version、pairing_code、platform、capabilities 版本映射 |
| `/api/terminal/poll` | device token | `{}`；同时更新 last_seen |
| `/api/terminal/receipt` | device token | command_id、status、result |
| `/api/terminal/notification-click` | device token | command_id |

配对请求示例：

```json
{
  "protocol_version":1,
  "pairing_code":"<主人生成的码>",
  "platform":"android",
  "capabilities":{"notify":1,"speak":1,"read_battery":1,"show_message":1}
}
```

轮询返回 `{"protocol_version":1,"server_time":...,"command":null}`，或带一个命令：

```json
{
  "protocol_version":1,
  "server_time":1794000000,
  "command":{
    "protocol_version":1,
    "command_id":"00112233445566778899aabbccddeeff",
    "device_id":"alex_phone",
    "capability":"notify",
    "arguments":{"title":"提醒","text":"喝水"},
    "status":"leased",
    "expires_at":1794000120,
    "lease_until":1794000030,
    "attempts":1
  }
}
```

回执示例：

```json
{"command_id":"00112233445566778899aabbccddeeff","status":"succeeded","result":{"published":true}}
```

失败使用 `status:"failed"`，不能确定动作是否完成使用 `status:"unknown"`，两者必须提供
`result.error`。相同回执重发幂等；冲突回执返回 409，其他终端的命令返回 404。
400 表示格式错误、401 表示凭据无效、403 表示策略拒绝、409 表示状态冲突或过期。

## 投递、重启与状态含义

```text
queued → leased → succeeded / failed / unknown
   └──────┴────→ expired / cancelled
```

- 同一终端同时最多一个命令在执行，默认租约 30 秒、命令有效期 120 秒。
- 租约到期重投同一个 command_id，attempts 增加；不生成新 ID。
- 手机在执行前持久记录 command_id，执行后持久记录回执，再上传。
- 执行结果保存前若进程退出，恢复后报告 unknown，不自动重做可能已经发生的动作。
  这实现至少一次投递与客户端去重，不承诺外部副作用的 exactly-once。
- 服务端重启保留终端、凭据摘要、队列和租约。过期任务在轮询/查询时清理状态。
- 每个终端最多 100 个待处理命令。历史命令保存在本地 SQLite，本版尚未自动清理历史消息正文。
- 配对和投递时都检查当前策略，策略变为 read_only 后不会继续下发排队中的写动作。
- runtime 入队只发布 `command.queued`，不会伪造设备 `state`；回执产生 `device.state` 和审计。
- [异步 Mission worker](MISSIONS.md) 已支持顺序派发、等待回执、延迟启动、超时与取消。
  原有同步 Skill runner 仍拒绝异步终端动作；使用 Mission 编排终端任务。

配置示例（`serve` 默认已有 endpoint）：

```json
{"adapters":{"endpoint":{"enabled":true,"command_ttl_s":120}}}
```

`IOT_LAB_DATA_DIR` 控制默认数据库目录；也可在 endpoint 配置中指定 `db_path`。
同一数据库由一个共享 runtime 实例管理；多个服务进程共享同一队列尚不支持。

## 验收

自动测试覆盖配对版本/权限、秘密存储、凭据隔离、参数校验、重复投递、回执幂等、
过期与撤销、并发轮询、重启恢复、真实 HTTP 往返，以及同步任务入口的异步保护。

实机仍需逐项确认：

1. 配对，获取能力目录，通知权限拒绝时返回 failed。
2. 授予通知权限，发通知，查询 succeeded；点击后出现独立 clicked_at。
3. 电量回执与手机一致，TTS 完成后才返回 succeeded，展示消息实际出现在 App。
4. 断开 Wi-Fi 后回执留在本地；恢复连接后上传，不重复播报。
5. 手机进程中断时已开始但未记结果的动作报告 unknown。
6. 服务端重启后设备仍可连接；撤销后旧 token 返回 401。
7. 开启后台接收后锁屏，通知/TTS/电量任务继续执行；超期任务不执行。

Android 后台接收采用用户主动开启的前台服务和带超时续期的 partial wake lock，持续连接会增加耗电。
深度 Doze 的联网仍需用户授予电池优化豁免；App 提供“允许锁屏联网”入口，不会自动更改系统设置。
部分厂商还需在系统设置允许后台运行/自启动。强制停止或重启手机后需重新打开 App 开启接收，
不承诺绕过系统强停。网络必须能访问 Southbound：离开局域网后需自行配置可达的 HTTPS 服务或 VPN。

`notify`、`speak`、`read_battery` 可在锁屏执行；`show_message` 要求客户端页面解锁可见，
否则返回 failed，后台不会自动打开页面或把未显示的消息标记 displayed。
停止接收会释放 wake lock 并停止网络重试；服务进程意外回收时由 START_STICKY 尽力恢复。

云推送、二维码扫描、跨 App 功能、通知回执的独立证明以及 TLS 证书部署不包含在本版。
Android 终端只执行已定义且获批的能力，不接收 shell 或任意 Intent。

系统依据：[前台服务类型](https://developer.android.com/develop/background-work/services/fgs/service-types)、
[Doze 与电池优化豁免](https://developer.android.com/training/monitoring-device-state/doze-standby)。
本版声明 specialUse 及具体用途；若未来上架 Google Play，需要审核这一使用场景。

## 能力升级、自然语言与屏幕图片

原配对无需撤销。owner 使用 `POST /api/terminals/capabilities`，body 包含 device_id、完整的
capabilities 字符串数组及 confirm:true，设置批准的能力集合。旧客户端暂不支持的能力只预批准，
不会进入设备目录。客户端使用独立 token 调用 `POST /api/terminal/hello`，提交 protocol_version:1
及 capabilities 版本映射；服务端只激活已获批且版本受支持的能力，并刷新设备目录。
客户端自报不能扩大主人的授权，主人移除的能力也不会因下次 hello 自动恢复。

`POST /api/northbound/intent` 接受 goal 和可选 context.device_id；`southbound intent`、电脑面板、
MCP `iot_intent` 共用该入口。通知默认标题为“Agent 通知”，空通知使用明确的默认测试文案；
缺少播报内容、目标手机不唯一或能力不可用时返回 needs_input，不猜测设备或伪造执行成功。
正文不拆分为子指令。返回 pending 和 command_id 后，通过 command_status 等待真实回执。

`device_info` 为只读清单查询，不包含硬件控制或相机/麦克风/定位采集。
`capture_screen` 需要主人批准、policy.allow_screen_capture=true，以及手机当前有效的系统共享授权。
Android 只创建一次 VirtualDisplay，短时共享内复用它取得按需截图；不持久化或重用系统授权 token。
没有授权、锁屏或共享已结束时命令失败并返回手机端操作提示。

图片单独传输：

- `POST /api/terminal/images`：device token，Content-Type:image/jpeg，X-Command-Id 为本机已领取的
  capture_screen 命令。最多 512 KiB、最长边 1280；检查 JPEG 帧头、命令归属、租约和权限。
- 上传返回 artifact_id、mime_type、width、height、bytes、sha256、expires_at、url。
  成功截图回执必须与本命令已上传图片的元数据完全一致，不能引用另一条命令的图片。
- `GET /api/terminals/images/<artifact_id>`：必须提供 owner token，即使来自 loopback。
  响应使用 no-store；不接受 device token 或安装链接授权，且重新检查屏幕访问策略。
- MCP `iot_terminal_image` 接受 artifact_id，返回标准 image 内容供 Agent 查看。

图片最多保留每设备最近 10 张、24 小时；清理在上传/读取时执行，不提供到点安全擦除承诺。
撤销设备删除图片记录。图片数据不写入事件和审计 JSON；截图与硬件查询结果均为手机上报证据。


## 界面操作能力（Android 0.5.0）

协议 v1 增加 `ui.observe`、`app.launch`、`ui.tap`、`ui.input`、`ui.swipe`、`ui.back`，
实现版本均为 1。主人批准与客户端支持取交集，复用原队列、去重、策略和回执。
手机执行单步动作；中心 Agent 根据观察规划下一步。参数、页面引用失效规则与真机验收流程见
[Android 界面执行层](ANDROID_UI.md)。

`POST /api/terminal/progress` 使用设备 token，为已 leased 的命令报告 waiting_for_user / executing 进度。
进度不是完成回执；取消、过期、撤销后拒绝继续。应用授权由手机本地确认，不能通过此接口授予。
