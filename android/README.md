# Android 通知转发端（可选，进阶）

这一份是最小可用的安卓抓取端：装上并授权后，微信/支付宝/银行的通知会**实时**转发到你的记账本服务器。

> ⚠️ 说明：这部分代码没有在本机编译验证过（本机没有 Android SDK）。
> 用 Android Studio 打开 `android/` 目录、Sync 一次即可编译；如果报错把错误发我。
> 如果你不想折腾编译，直接用 `docs/数据来源与抓取.md` 里的 **MacroDroid** 方案，效果一样。

## 编译步骤

1. 安装 [Android Studio](https://developer.android.com/studio)
2. File → Open → 选择本 `android/` 目录（含 `settings.gradle.kts` 的那一层）
3. 等待 Gradle Sync（首次会下载 Gradle 与 SDK，需要能访问 google() 仓库）
4. 手机开 USB 调试连上，点 ▶ 运行；或 Build → Build APK 后把 apk 拷到手机安装

## 使用步骤

1. 打开 App，填服务器地址，例如 `http://192.168.1.100:8787`（设了口令就填口令）
2. 点「保存」
3. 点「① 打开通知访问权限设置」→ 找到「记账本转发」→ 允许
4. 回到 App 点「② 查看授权状态」，显示已授权即可
5. 点「③ 发送一条测试通知」，去记账本「明细」里看有没有一条 ¥0.01 的测试记录
6. 之后正常付款，通知会自动进来

## 代码结构

| 文件 | 作用 |
| --- | --- |
| `NotifyRelayService.kt` | 通知监听服务：过滤来源包名 → 取标题+正文 → POST `/api/ingest` |
| `MainActivity.kt` | 配置界面：服务器地址、口令、授权引导、测试按钮 |
| `AndroidManifest.xml` | 声明 `BIND_NOTIFICATION_LISTENER_SERVICE` 权限 |

## 自己改的话

- 想多监听几个 App：改 `NotifyRelayService.WATCHED` 里的包名
- 想连蓝牙/耳机等其它来源：不用限制包名，但服务端会过滤掉非交易通知（验证码、登录等）
- 服务端地址建议用固定内网 IP 或 Tailscale 地址，手机换 WiFi 也能用

## 为什么不用读数据库的方式？

Android 的应用沙箱不允许第三方 App 读取微信/支付宝的私有数据；root 后强读既有风控风险，
也可能违反用户协议。通知监听是系统官方提供、用户显式授权的能力，最稳妥。
