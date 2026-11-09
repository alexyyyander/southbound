# 反编译 MCP 接口与后端集成

接口版本：v1。提供契约、MCP 分发、校验和可注入后端接口。
已提供可选的真实 REA MCP 后端，部署与启动见 [REA 后端](REA_BACKEND.md)；
无配置时保持不可用，不包含分析调度器。

## 结构与入口

- `southbound/analysis/contracts.py`：7 个工具的输入/输出 JSON Schema、错误码和校验器。
- `southbound/analysis/backend.py`：`AnalysisBackend` 抽象类、`AnalysisResult`、`AnalysisError`。
- `southbound/analysis/mcp.py`：输入校验、调用后端、结果校验与 MCP 转换。
- `southbound/mcp_server.py`：注册工具、注入后端、进程结束时释放后端。
- `tests/test_analysis_mcp.py`：仅用于测试的模拟后端，不会作为生产默认值。

分析能力与 IoT Runtime 并列，不经过 `DeviceAdapter` 或 `AccessHub`。
不改变现有设备发现、接入、控制、策略和审计流程，也未新增 HTTP API。
`SOUTHBOUND_REMOTE_URL` 仍只影响 IoT Runtime；分析后端不会自动转发至该 URL。

可通过原有 `python3 -m southbound mcp` 启动 MCP。工具始终列出；未配置分析后端时，
合法分析请求返回 `BACKEND_UNAVAILABLE`；非法参数先返回 `INVALID_ARGUMENT`。
配置了真实后端时按实际分析结果返回，启动配置规则见 [REA 后端](REA_BACKEND.md)。
可以通过受信任的本地配置选择真实 REA 后端；没有切换模拟后端的生产入口。

## 工具

所有输入对象拒绝未知字段。除打开目标外，均要求非空 `session_id` 和 `target_id`。
标识为后端签发的不透明字符串（最长 256 字符），不得把地址或路径当成标识自行猜测。

| 工具 | 额外输入 | `data` 输出 |
|---|---|---|
| `decompile_open_target` | `path`：分析主机上的绝对文件路径 | `path`、`state: ready/analyzing` |
| `decompile_target_overview` | 无 | 可选 `format`、`architecture`、`function_count`、`string_count` |
| `decompile_search_functions` | `query`、可选 `limit` / `cursor` | `items`：函数列表；可选 `next_cursor` |
| `decompile_search_strings` | 同上 | `items`：`{address,value}` 列表；可选 `next_cursor` |
| `decompile_analyze_function` | `selector`：精确名称或虚拟地址 | `function`；可选 `pseudocode`、`assembly`、`calls` |
| `decompile_analysis_status` | 无 | `state: ready/analyzing/failed/closed`；可选 `message` |
| `decompile_close_target` | 无 | `closed: true`，确认资源已释放 |

搜索是大小写不敏感的字面子串匹配，不解释正则。`query` 非空，最长 4096 字符。
`limit` 是 1–200 的整数，省略时传给后端 50；布尔值不算整数。
`cursor` 非空，最长 4096 字符，由后端产生并校验，绑定到会话、目标、操作和查询。
单页条数不能超过请求的 limit；还有下一页时必须返回 `next_cursor`、
`completeness: partial` 和相应 limitations。空列表仅在搜索确实完成时才能表示无匹配。

函数对象为 `{address, name?}`。地址统一为 `0x` 开头的十六进制虚拟地址字符串，
不是文件偏移，也不用 JSON 数值，以免丢失精度。选择器只能是以下两种之一：

```json
{"selector":{"address":"0x401000"},"session_id":"s1","target_id":"t1"}
```

```json
{"selector":{"name":"main"},"session_id":"s1","target_id":"t1"}
```

名称采用大小写敏感的精确匹配；无匹配返回 `FUNCTION_NOT_FOUND`，多匹配返回
`AMBIGUOUS_FUNCTION`，不得任意选一个。后端决定是否接受函数内部地址；如果不支持，
返回明确错误，不静默改为另一函数。路径最长 4096 字符；接口仅检查绝对路径形式，
文件存在、路径授权、符号链接、文件类型和内容身份由后端检查。

## 结果与证据

每个工具都通过 `outputSchema` 公布成功/失败结构。响应包含 `content` 中的 JSON 文本
及与其相同的 `structuredContent`，供旧客户端与结构化客户端分别读取。
`isError` 与 `ok` 相反。即使结果不完整，仍可 `ok: true`，但必须声明局限。

成功示例（示意数据，不代表已完成真实分析）：

```json
{
  "ok": true,
  "session_id": "s1",
  "target_id": "t1",
  "data": {
    "function": {"address": "0x401000", "name": "main"},
    "assembly": "..."
  },
  "evidence": [{
    "source": "ghidra:<version>",
    "operation": "analyze_function",
    "locator": "evidence:record-123",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }],
  "completeness": "partial",
  "limitations": ["反编译失败，仅获得汇编；调用关系尚未查询。"]
}
```

`evidence` 每项要求 `source`（工具/来源身份）、`operation`（产生记录的操作）、
`locator`（可追溯证据位置或记录标识）；可选 `sha256` 为被分析目标字节的摘要。
所有观测分析结果必须有至少一条证据；状态查询和资源关闭允许空证据。
集成方负责保存 locator 对应记录、记录工具版本和来源，不得编造摘要或证据。
接口层验证结构，不验证证据真实性或密码学身份，也不提供证据下载接口。

`completeness` 的语义：

- `complete`：相对于当前操作/查询范围已完成，不代表恢复了完整源码或整个程序。
- `partial`：有明确缺项，例如结果截断、剩余分页、缺少伪代码。
- `unknown`：无法判断覆盖程度。

后两种必须有非空 `limitations`。没有概况信息不能标为 complete；函数分析没有
非空伪代码或汇编不能标为 complete。省略字段表示未获取/不支持，必须在局限中说明；
`calls: []` 表示已检查且没有调用，不能用于掩盖未查询。后端负责判断事实完整性。

## 错误契约

合法工具调用中的业务错误是 MCP tool result，`isError: true`，不是 JSON-RPC transport error：

```json
{"ok":false,"error":{"code":"BACKEND_UNAVAILABLE","message":"No analysis backend is configured.","retryable":false}}
```

| 错误码 | 含义 |
|---|---|
| `INVALID_ARGUMENT` | 缺少字段、类型/选择器/范围错误或未知字段 |
| `BACKEND_UNAVAILABLE` | 未配置后端，或后端/依赖当前不可用 |
| `BACKEND_CONTRACT_ERROR` | 后端输出类型、字段、证据或目标/会话不符合契约 |
| `BACKEND_ERROR` | 未预期后端异常，原异常内容不会返回客户端 |
| `SESSION_NOT_FOUND` / `TARGET_NOT_FOUND` | 会话或所属目标不存在/不可再用 |
| `FUNCTION_NOT_FOUND` / `AMBIGUOUS_FUNCTION` | 函数选择失败 |
| `UNSUPPORTED` | 不支持的格式或能力 |
| `ACCESS_DENIED` | 后端拒绝路径或操作权限 |
| `BUSY` | 当前分析状态不允许操作 |
| `TIMEOUT` / `CANCELLED` | 后端超时或后端操作被取消 |

后端通过 `AnalysisError(code, safe_message, retryable=False)` 报告预期错误；
message 必须适合对客户端公开，不得包含令牌或内部堆栈。retryable 是建议，不会自动重试。
未知工具仍沿用原有 JSON-RPC `-32602`，既有 IoT 错误行为不变。

## 后端实现方需要提供什么

实现 `AnalysisBackend` 的七个操作及 `shutdown()`。每个操作接收经过校验的 request dict，
返回 `AnalysisResult(session_id, target_id, data, evidence, completeness, limitations)`。
`contracts.py` 的每操作 schema 是字段的权威定义；MCP 层会再次校验输出，且后续操作
的 session_id/target_id 必须与请求一致。

内置 `ReaBackend` 可直接从本地配置自动加载。若要注入其他后端，在开始处理 MCP 请求前由集成入口调用；以下是自定义后端的示意：

```python
from southbound import mcp_server

backend = YourAnalysisBackend(...)  # 自定义实现；内置 REA 后端无需此占位类
mcp_server.configure_analysis_backend(backend)
mcp_server.main()
```

调用 `configure_analysis_backend(None)` 恢复不可用后端。注入后，MCP 服务拥有后端并在
EOF/异常退出时调用 shutdown。更换前先关闭旧后端；若关闭失败，不替换旧后端，
新实例仍由集成方负责释放。重复注入同一实例不会关闭它。只能在处理请求前配置，
没有热切换/并发安全保证。正常 shutdown 必须幂等；无法完成的清理应可重试并报告异常。
进程被强制终止时 Python finally 不保证执行，集成方需处理遗留资源恢复。

内置 `ReaBackend` 已独立持有 REA MCP 连接，将规范化结果、Evidence
记录及错误映射到本接口，不从本地 REA `src/` 导入私有模块。其他后端也应维持这一边界。可参考本地 REA 的
`docs/mcp-contracts.md`、`docs/native-investigation.md` 和 `src/contracts/`。
本接口借鉴其证据与完整性表达，不是 REA MCP 的逐字段兼容代理。

## 会话、任务、取消与资源边界

| 事项 | 责任边界 |
|---|---|
| 标识与状态 | 后端分配会话/目标 ID、绑定不可变目标字节、检查所属关系和状态；ID 不是授权凭证 |
| 初次打开 | 后端可同步返回 ready，或在已有初始化机制下返回 analyzing；接口本身不启动后台任务 |
| 状态查询 | 查询目标当前状态，不创建任务；failed 时在 message/limitations 说明原因 |
| 尚未就绪 | 后端返回 BUSY 或带局限的已有部分结果，不伪装成完整结果 |
| 长任务 | 后端必须限定调用时长并实施超时；MCP 层没有调度器、线程池、任务 ID 或重试器 |
| 取消 | 当前同步 stdio 循环不支持调用中取消/进度通知；取消通知不会中断后端。TIMEOUT/CANCELLED 仅映射后端已有结果 |
| 关闭目标 | 后端关闭目标关联句柄、进程、临时文件和后端已有任务；完成释放后才能返回 closed=true；失败应报错且允许重试 |
| 重复关闭 | 同一有效会话内，已关闭目标应再次返回成功；未知 ID 仍报错，后端管理关闭记录的生命周期 |
| 会话结束 | 本接口无单独 close_session；后端在不再有活动目标时回收会话资源，shutdown 释放全部剩余会话 |
| 断开与退出 | EOF 时调用后端 shutdown；请求已到达但响应丢失时，不保证 exactly-once，后端负责失败打开与断连清理 |
| 权限及资源 | 后端实施路径允许范围、静态分析限制、输出大小/时间/内存/磁盘预算及日志审计；不复用 IoT 的设备权限 |

现有 MCP 是同步处理，慢后端会阻塞同进程的 IoT 请求。集成方应先提供有界操作；
若需要长任务、并行设备控制或标准 MCP 取消，需要单独设计任务/传输层，本次未实现。
接口不保证异步能力或生产多用户隔离。目标程序不得由这些静态分析工具执行。

## 验证与后续工作

运行：`python3 -m unittest discover -s tests -p test_analysis_mcp.py -v`。
测试后端只存在于 tests，验证注册/分发、参数拒绝、两种函数选择器、结果与证据校验、
部分结果/分页、全部错误码、默认不可用及退出清理，并验证原有 MCP 调用未变。

真实 REA 适配、会话映射、路径限制、超时和清理已由 `ReaBackend` 提供，
部署及真实样本验证见 [REA 后端](REA_BACKEND.md)。本页的模拟测试仅证明接口层行为。
