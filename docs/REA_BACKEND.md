# REA 真实分析后端

Southbound 的 7 个 `decompile_*` 工具可通过独立 REA MCP 进程调用真实反编译引擎。
入口与字段保持 [接口契约](DECOMPILATION_MCP.md) 不变。Python 侧没有新增依赖，
不导入 REA 内部源码，不执行被分析程序，不经过 IoT 设备适配器。

## 本机已验证部署

以下为 2026-11-06 的部署及验收记录；新机器需独立配置，不会随源码复制运行依赖。

当前 ORICO 部署已验证 REA 6.8.0、独立 Node 26.12.1、Ghidra 12.1.4、
Corretto JDK 25.0.5；运行目录为 `/Volumes/ORICO/rea-runtime/`。
本地 `config/analysis.local.json` 已配置这些绝对路径，启动 MCP 即可加载。
输入目录为 `/Volumes/ORICO/southbound/data/analysis-inputs/`。

实际已发布的 REA 6.8.0 只接受 Ghidra 12.1.x，不能根据本地较新源码推定支持 12.2。
Ghidra 官方发行包没有 macOS ARM 原生反编译器，本部署从官方 12.1.4 标签的源码编译
`ghidra_opt`，安装为 `Ghidra/Features/Decompiler/os/mac_arm_64/decompile`。
构建参数及二进制摘要保存在运行目录的 `native-builds/ghidra-12.1.4-mac-arm64/build.json`。

ORICO 文件系统会产生 AppleDouble `._*` 伴随文件；其中 `._*.jar` 会干扰 Java 库扫描。
本次仅清理新安装 Ghidra 目录中的伴随文件，随后真实分析通过。重新解压或复制引擎后，
应检查安装目录是否再次出现这些文件。不要对其他用户目录批量清理。

实际验证通过的调用记录在 `data/analysis/live-smoke.json`，包含全部七类工具、
按地址和精确名称的函数分析、重复关闭及真实 Ghidra 证据引用。

相关入口：[功能更新说明](DECOMPILATION_UPDATE.md)、[调用演示](DEMO.md#反编译-mcp-demo)、
[接口契约](DECOMPILATION_MCP.md)。

## 配置及启动

将 `config/analysis.example.json` 复制为 `config/analysis.local.json`，填写实际路径。
也可通过 `SOUTHBOUND_ANALYSIS_CONFIG=/absolute/path/config.json` 指定配置。
环境变量优先；设置为空字符串可禁用自动配置。配置只由操作者提供，MCP 参数不能指定启动命令。

- `command`：启动 REA stdio MCP 的 argv 数组，例如 `[node, rea.mjs, "mcp"]`，不经过 shell。
- `allowed_roots`：允许提交分析文件的绝对目录，必须存在。指向目录外的符号链接会被拒绝。
- `state_dir`：目标快照、会话临时文件和证据存放目录。
- `provider_id`：明确选定分析提供方，默认 `ghidra`；失败时不会自动切换为模拟或另一个引擎。
- `timeout`：单次上游 MCP 请求时限，1–600 秒，默认 180 秒；初始化也受同一时限限制，失败后的进程清理最多另需约 6 秒。
- `env`：只传给 REA 子进程的环境变量，例如 `GHIDRA_INSTALL_DIR`、`JAVA_HOME`。

配置中的可执行文件、目录和环境变量属于受信任的本机部署配置。配置不会安装软件、
下载引擎或修改全局环境。无配置继续返回 BACKEND_UNAVAILABLE；配置非法会向 stderr
报告并保持不可用，不影响既有 IoT 工具的初始化。REA 在第一次 open 请求时才启动。

启动方式保持：

```bash
cd /Volumes/ORICO/southbound
python3 -m southbound mcp
```

原来的 `.mcp.json` 入口也可使用本地自动配置。配置文件和分析输入/输出目录已加入
`.gitignore`，部署到其他机器时需重新填写绝对路径，不要提交凭证。

## 实现方式

每次打开目标创建独立的 REA 进程、session_id 和 target_id，避免 REA 单活动目标模型
造成会话之间相互切换。各工具映射如下：

| Southbound 操作 | REA 工具 |
|---|---|
| open_target | open_binary（显式 provider_id） |
| target_overview | binary_overview |
| search_functions | search_procedures（literal、case_sensitive=false、detail=complete） |
| search_strings | search_strings（同上） |
| analyze_function | analyze_function（procedure=地址、detail=complete） |
| analysis_status | binary_session；已关闭/失败的本地记录直接查询 |
| close_target | close_binary，然后关闭进程并删除目标快照 |

函数名选择先查询精确匹配的主名称或别名；存在多个不同地址时报歧义，只有一个才继续。
函数分析返回伪代码、汇编和已声明可用的 callees；未声明可用的调用关系会省略并说明局限。
地址输出规范化为十六进制字符串，目前不支持带地址空间前缀的非平面地址。

## 输入、分页与证据

- 只接受允许目录内的普通文件，最大 128 MiB。快照前逐级使用不跟随符号链接的文件句柄
  打开已解析路径；拒绝目录、设备、FIFO 等特殊文件。
- 原始文件被复制到独立工作区，REA 分析该快照；核对 REA 返回的目标 SHA-256，
  后续原始文件变化不影响已打开目标。文件复制过程中不保证原始文件的原子版本一致性，
  证据身份指向实际快照字节。
- 搜索结果基于一次 REA 完整结果分页，不会为每页重新执行分析。游标绑定目标、查询及
  操作，最多保留 16 个后续页游标，超出后最旧游标失效。跨会话或重启后不能复用。
- 最大同时打开 4 个目标；每个后端实例最多记录 128 个打开过的目标，达到上限后需重启。
- REA 的完整成功记录和可解析的业务错误记录保存在 `state_dir/evidence/<target_id>/`，
  文件名是该 JSON 的 SHA-256；返回的 locator 是该记录的本地路径，sha256 字段仍表示
  被分析目标字节的摘要。打开/状态的生命周期收据来自真实 REA 响应，不伪装成 REA Evidence。
- 证据关闭后保留，供审阅；关闭目标删除输入快照和游标。没有自动清理证据的保留策略，
  部署方应按需归档/清理并控制磁盘容量。错误记录可能包含本地路径，需限制本机访问。

REA 明确将函数和字符串发现覆盖率标为 unknown；因此本后端不把库存搜索结束等同于
发现完整，返回 unknown 或分页时 partial，并保留上游 limitations。函数结果同样保留局限。

## 生命周期及当前限制

正常关闭先调用 REA close_binary，随后关闭 stdin 并等待正常 EOF 退出；
超时后才发送 SIGTERM/SIGKILL 清理所属进程组，最后删除快照；重复关闭幂等。
关闭过程中上游报错仍会释放本地进程和快照；操作返回错误，调用者可重试确认关闭。
如果本地目录清理本身失败，目标不会标为 closed，重试将继续清理。
超时/传输异常会断开并终止 REA 进程，将目标标为 failed，随后可 close 并重新打开。
正常服务 EOF 会释放所有目标；强制杀进程、主机掉电时不保证 finally 执行，也不自动删除
上次残留的 target-* 目录。REA 自己管理其引擎子进程；强制终止后的引擎残留需单独排查。

当前客户端只支持 POSIX（已针对 macOS 验证）；Windows 明确返回 UNSUPPORTED。
每条上游响应最多 16 MiB，超出会返回错误并断开；需要更大目标/输出时应先设计摘要接口。
MCP 仍是同步服务，分析会阻塞同进程设备请求；不提供调用中取消、任务队列或后台调度。
后端 TIMEOUT/CANCELLED 映射不代表已实现 MCP cancellation notification。

这不是多用户隔离沙箱。允许目录限制输入选择，但 REA/Ghidra 作为本机工具仍具有当前用户
的权限；需要共享给多用户时，应增加独立 OS/容器隔离和访问控制。

## 验证

受控 MCP 子进程测试（无真实引擎）：

```bash
python3 -m unittest discover -s tests -p test_rea_backend.py -v
```

显式启用真实 REA + Ghidra 冒烟测试：

```bash
SOUTHBOUND_REA_LIVE_CONFIG=/Volumes/ORICO/southbound/config/analysis.local.json \
python3 -m unittest discover -s tests -p test_rea_live.py -v
```

该测试自行编译一个含 `southbound_demo` 函数和 `SOUTHBOUND_REA_SMOKE` 字符串的小程序，
只静态分析，不执行样本。通过真实 Southbound MCP 进程调用 7 个工具，检查非空伪代码、
证据记录和重复关闭；调用记录写入 state_dir/live-smoke.json。默认回归不会安装或启动引擎。

固定部署所用版本与下载摘要应记录在运行目录中；升级 REA 或 Ghidra 后重新运行真实测试，
不能仅凭受控测试通过就宣称真实引擎兼容。
