# Southbound Android Endpoint

Android 8.0+ 的通用终端协议 v1 客户端（App 0.5.2）。支持系统通知、TTS、电量与硬件信息查询、系统授权截图、App 内消息和无障碍界面单步操作。
终端按已获批的能力执行动作；服务端不需要 Android 专用 adapter。

## 构建

需要 JDK 17、Android SDK Platform 35、Build Tools 35.0.0。Gradle Wrapper 固定为
8.11.1（带 SHA-256 校验），Android Gradle Plugin 固定为 8.9.2。

```bash
cd clients/android
export ANDROID_HOME=/path/to/android-sdk
export ANDROID_USER_HOME="$PWD/.local/android"  # 保留本地调试签名；已 gitignore
./gradlew --no-daemon assembleDebug lintDebug
```

也可以在 Android Studio 中打开此目录并使用 JDK 17。调试 APK 输出位置：
`app/build/outputs/apk/debug/app-debug.apk`。

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

debug 允许局域网 HTTP，release 禁止明文流量且不绕过证书检查。
release 的签名由部署者配置，仓库不含签名密钥。

## 接入

先在电脑启动共享服务，然后生成接入二维码（URL 使用手机能访问的电脑地址）：

```bash
python3 -m pip install -e '.[android]'
export SOUTHBOUND_REMOTE_TOKEN='<服务端 owner token>'
python3 -m southbound terminal-connect alex_phone \
  --url http://192.168.1.10:8765 --confirm --qr /tmp/phone-connect.png
```

手机流程：

1. 用系统相机扫描二维码，打开安装页，下载 App；安装后回到浏览器页面。
2. 点“连接并启用”打开 App，再点主按钮确认所显示的电脑地址；地址和配对码自动带入。
3. 依次允许系统通知和锁屏联网请求。拒绝权限不会反复弹窗，可以稍后从“连接详情与手动设置”补开。
4. 点“测试锁屏任务”，30 秒内锁屏。通知先发出，播报完成后，App 和电脑上的同一接入页显示回执结果。

浏览器若不支持 intent 链接，使用页面中的重试链接；扫码 App 内置浏览器若拦截跳转，改用系统浏览器。
首次安装仍需 Android 的安装来源授权。App 不需要相机权限；电脑接入链接包含 10 分钟有效的配对凭据，
请只分享给自己的手机。链接过期后已连接手机仍可用；尚未配对的设备重新运行命令生成二维码。
已连接设备不能直接重新配对，需先在服务端撤销旧设备并清除 App 数据后再接入。

手机主界面保留接收开关、锁屏测试和结果。手动地址/配对码作为折叠设置保留。
后台服务正常每两秒轮询，失败时退避至最多 60 秒；页面退出或锁屏不停止服务。
持续 CPU 唤醒和联网会增加耗电，停止服务会释放 wake lock。
锁屏支持 notify / speak / read_battery / device_info；show_message 只有页面解锁可见时才成功。
电池优化未豁免时深度 Doze 仍可能暂停联网，部分厂商另有后台限制。
强制停止/系统任务管理器停止/手机重启后需要手动恢复。

更新旧调试 APK 如果遇到 INSTALL_FAILED_UPDATE_INCOMPATIBLE，说明调试签名不同。
需先撤销服务端旧设备，卸载旧 App 后安装并重新配对。卸载会删除该 App 的凭据与本地任务记录。
本次开始保留 `.local/android` 中的调试签名，以便后续本机构建可覆盖升级。

0.4.0 已完成 APK 构建、签名校验及 lint（0 errors，1 条 BatteryLife 提示，来自电池优化豁免请求）。
服务端 221 项测试通过。2026-11-07 已在一台 vivo V2309A（Android 16）上取得自然语言通知、播报、硬件查询与截图的成功回执，并取回有效图片。此前用户确认锁屏通知和播报正常；其他机型、长时间 Doze 与厂商后台限制仍需分别验收。

客户端执行前记录 command_id，执行结果写入本地 SQLite 后才上传。网络重试只重发回执；
进程崩溃后遇到未记录结果的命令报告 unknown，不自动重复外部动作。
凭据使用 Android Keystore 的 AES-GCM 加密保存，应用备份关闭。

0.5.0 新增无障碍读屏、打开 App、点击、输入、滑动和返回，详见 [Android 界面执行层](../../docs/ANDROID_UI.md)。
手机只执行明确动作，规划和效果核对留在中心 Agent；默认只允许系统设置，需用户开启无障碍及界面操作。
本版不实现虚拟屏、远程 shell 或远程解锁。

0.5.0 已完成 APK 构建与签名校验、Android lint（0 errors，1 条原有 BatteryLife warning）、
226 项服务端测试和 Java 页面引用守卫测试。真机 UI 任务需在手机覆盖安装并开启无障碍后验收，
不以协议模拟测试代替真机结果。

## 自然语言、硬件查询与截图

通过电脑面板、`southbound intent` 或 MCP `iot_intent` 输入：

- `通知我的手机：该喝水了`
- `让手机播报这句话：十分钟后开会`
- `查看手机硬件信息`
- `手机电量还有多少`
- `捕捉手机的屏幕`

只有一台已配对 Android 时自动选择它；多台时需指定名称或 device_id。没有播报内容时会要求补充，
通知/播报正文按文本处理，不把其中的指令当作额外动作。当前是明确句式路由，不是通用语言模型。

硬件查询返回型号、Android 版本、可用处理器数、ABI/SoC、内存、App 所在卷的存储容量、网络类型、
电池、显示信息及硬件/传感器清单（最多列出 32 个传感器，并返回总数）。不激活传感器，
不调用相机/麦克风/定位，不获取 IMEI、MAC、通讯录或文件内容，也没有振动/手电筒等控制能力。

截图步骤：开启接收 → 点击“允许屏幕共享（5 分钟）” → 完成系统授权 → 切到希望展示的页面 →
让 Agent 请求截图。系统允许选择单个 App 或整个屏幕时，以你的选择为准。
共享有独立常驻通知和停止入口，5 分钟自动停止；停止接收也会停止共享。
锁屏、系统撤销或进程退出后不能复用旧授权，需要重新授权；受保护页面可能显示黑屏。

已配对设备可覆盖升级并保留 token。主人通过 `/api/terminals/capabilities` 显式批准新增能力；
客户端每分钟上报实现版本，实际可调用能力是“主人批准 ∩ 客户端已实现”。更新前不会下发新能力。
图像不放在审计/事件 JSON 中：回执只含 artifact_id 和元数据，Agent 用 `iot_terminal_image` 读取图片。
服务端最多保留每台设备最近 10 张图片，最长 24 小时；撤销设备会删除其图片。需要主人凭据才能下载。

## 参考

- [AGP 8.9 兼容要求](https://developer.android.com/build/releases/agp-8-9-0-release-notes)
- [通知权限](https://developer.android.com/develop/ui/compose/notifications/notification-permission)
- [TTS 完成回调](https://developer.android.com/reference/android/speech/tts/UtteranceProgressListener)

- [Android deep links](https://developer.android.com/training/app-links/create-deeplinks)
- [运行时权限流程](https://developer.android.com/training/permissions/requesting)

- [MediaProjection 系统授权和生命周期](https://developer.android.com/media/grow/media-projection)

## 0.5.1 授权简化

首次访问新 App 时显示本机确认弹窗，无需手填包名；可允许 10 分钟或始终允许。
“启用手机控制”引导通知、后台接收与无障碍；已具备权限的步骤自动跳过。
Mission 等待确认后继续，取消/超时不会恢复执行。0.5.1 构建、签名与 lint 通过，服务端 228 项测试通过。
0.5.0 已在 vivo 真机验证读屏、滑动、搜索输入和点击跳转；电池页面的最终读取及新版弹窗仍待真机验收。

## 0.5.2 设置入口修复

移除需要系统专用权限的无障碍详情页入口，改用公开的无障碍设置列表。
系统拒绝跳转时退回系统设置；两种入口均不可用时提示手动操作，避免未处理异常导致闪退。
打开 App 后会恢复用户此前已开启的接收服务；主动停止接收后不会自动恢复。
APK 构建、签名校验、Java 导航回退与页面引用守卫测试通过；lint 无错误，保留原有 BatteryLife 警告。
本次修复和新应用授权弹窗仍需真机验证。
