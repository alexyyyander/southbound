# Southbound 反编译能力更新说明

文档更新：2026-11-09。对应主项目 Southbound 的未发布变更；Python 包版本保持 0.11.0，
尚未创建新版本发布。REA 是独立分析后端依赖，本次能力和工具入口属于 Southbound。

## 本次交付

Southbound MCP 从 15 个既有工具扩展为 22 个工具，新增：

| 工具 | 用途 |
|---|---|
| `decompile_open_target` | 打开允许目录内的本地文件，建立目标与会话 |
| `decompile_target_overview` | 获取格式、架构及提供方观察到的函数/字符串数量 |
| `decompile_search_functions` | 字面子串搜索函数，支持分页 |
| `decompile_search_strings` | 字面子串搜索字符串，支持分页 |
| `decompile_analyze_function` | 按精确名称或十六进制地址获取函数伪代码、汇编及可用调用关系 |
| `decompile_analysis_status` | 查询目标的 ready/analyzing/failed/closed 状态 |
| `decompile_close_target` | 释放目标资源；重复关闭幂等 |

新增 `AnalysisBackend` 抽象、输入/输出/错误契约和真实 `ReaBackend` 实现。MCP 仅通过后端
接口访问分析能力，不导入 REA 私有源码。每个目标使用独立 REA 进程和文件快照；证据在关闭
后保留。无配置或依赖不可用时返回明确错误，测试替身不作为生产后备。

## 使用方式

已配置的 ORICO 部署：将待分析文件放入 `/Volumes/ORICO/southbound/data/analysis-inputs/`，
重启已有 Southbound MCP，按“打开 → 保存标识 → 搜索/分析 → 关闭”的顺序调用。
启动命令仍为 `python3 -m southbound mcp`。本地配置为 `config/analysis.local.json`。

新部署需自行准备兼容的 Node、REA、分析引擎和 JDK，并填写
[配置示例](../config/analysis.example.json)。安装 Python 包不会自动获得反编译引擎。
`SOUTHBOUND_ANALYSIS_CONFIG` 可指定配置文件，空字符串可关闭自动加载。

结果包含 `ok`、`session_id`、`target_id`、`data`、`evidence`、`completeness` 和 `limitations`。
`ok: true` 只表示操作取得了结果；`partial` / `unknown` 及其局限需要保留。尤其搜索为空
不能证明二进制中没有相关函数或字符串。

## 已验证部署与证据

2026-11-06 在本机 macOS ARM 环境完成真实验收：

- REA 6.8.0、Node 26.12.1、Ghidra 12.1.4、Corretto JDK 25.0.5。
- Ghidra 使用从官方固定标签源码编译的 ARM64 原生反编译器。
- 完整测试 114 项全部通过，含真实 Southbound MCP → REA → Ghidra 调用。
- 真实测试覆盖全部 7 类工具、按地址/精确名称分析、重复关闭和证据保存。
- 自行编译的样本函数被恢复为 `return param_1 * 3 + 7;`；测试没有执行该样本。

本地验收记录在 `data/analysis/validation.log` 和 `data/analysis/live-smoke.json`；
原始证据在 `data/analysis/evidence/`。依赖版本、发布包摘要和原生构建记录在
`/Volumes/ORICO/rea-runtime/deployment.json` 及其 `native-builds/` 目录。
这些记录不随仓库分发。以上是已保存的历史验收结果，不表示其他平台或后续版本已经通过。

## 兼容性与当前限制

既有 IoT 工具、设备适配器和共享 Runtime HTTP API 保持原有职责。`--remote` 与
`SOUTHBOUND_REMOTE_URL` 只影响 IoT；分析在 MCP 主机执行，没有新增分析 HTTP 或面板入口。

目前 MCP 同步处理，分析可能阻塞同进程设备请求；尚无任务队列、调度和调用中取消。
REA 客户端仅支持 POSIX，真实引擎验收仅覆盖本机 macOS ARM。文件上限 128 MiB、同时活动
目标上限 4、上游响应上限 16 MiB；证据保留策略由部署方管理。

已发布的 REA 6.8.0 与本地较新源码存在兼容判断差异：本部署使用其验证的 Ghidra 12.1.4，
不据此宣称支持 12.2。外置盘的 `._*.jar` 元数据会干扰 Java 扫描，安装后需检查，详见
[后端部署与排错](REA_BACKEND.md)。

## 文档入口

- [README](../README.md)：能力总览、启动入口与开发方式。
- [调用演示](DEMO.md#反编译-mcp-demo)：请求示例与关闭步骤。
- [接口契约](DECOMPILATION_MCP.md)：参数、标识、证据、完整性和错误码。
- [后端部署](REA_BACKEND.md)：依赖、配置、资源预算和验证方法。
- [更新日志](../CHANGELOG.md)：未发布变更及后续版本记录。
