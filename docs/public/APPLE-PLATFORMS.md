# macOS 与 iOS 可行性（2026-10-16）

本轮用户没有可测试的 Mac。**当前可下载程序仍只有 Windows 版**；下文为经过源码和上游资料核对的移植设计，不是已发布的 Mac 或 iOS 功能。

## macOS：可以开发，需要单独适配

React 界面、角色和宣传素材、双方消息与时间的数据结构、模型 HTTP 协议、回复批次及大部分上下文逻辑可以复用。pywebview 本身有 Cocoa/WebKit 后端；当前应用的 Windows 适配不能直接使用。[pywebview 官方资料](https://pywebview.flowrl.com/guide/installation.html)

| 能力 | 当前 Windows 实现 | Mac 需要的适配与验收 |
|---|---|---|
| 当前聊天身份、输入草稿 | UI Automation、原生 Qt 输入控件 | AXUIElement 读取姓名和草稿，验证唯一联系人；不能用最新消息冒充当前窗口 |
| 微信历史、群成员、媒体 | 固定版本 Windows 读取模块 | 独立数据库提供方，保留发送者、分片身份、时间和缺失媒体，不直接套用 Windows 密钥提取 |
| 窗口跟随、前后台暂停 | Win32 HWND、WinForms | NSWorkspace 前台应用与 AX 窗口边界；权限失效、切到其他软件即停 |
| 密钥保存 | Windows DPAPI | macOS Keychain，不能导入 Windows 的 DPAPI 密文当成跨平台密钥 |
| 点选填入 | ValuePattern 写入/回读及原文保护 | AX 写入/回读；存在手改、其他草稿、身份变化时不覆盖，仍不自动发送 |
| 打包与分发 | EXE / Inno Setup | 在 macOS 构建 .app，分别验证 Apple Silicon / Intel；正式分发还需签名、公证 |

成熟方案比较：

- [Hammerspoon](https://github.com/Hammerspoon/hammerspoon)：超过一万 stars 的 Mac 自动化项目，可借鉴 AX、应用/窗口事件和权限诊断；不是微信历史 API，不为它更换现有界面框架。
- [wx-cli](https://github.com/pandorafuture/wx-cli)：规模较小但贴合微信 Mac 历史读取，提供 JSON/REST/SSE。其说明目前限定 Apple Silicon 与微信 4.1.7+；提取密钥要求 SIP 关闭，已有密钥可手动配置。这会显著影响普通用户安装体验，不应静默关闭保护或当作已经实测通过。只考虑独立本地只读适配，不引入其发送用途。
- [wechat-exporter](https://github.com/robbin/wechat-exporter)：可研究导出链路，不等于当前聊天身份、可靠草稿填入和连续前台联动都已经解决。

建议先实现可替换的 CurrentChat / HistoryProvider / DraftWriter / WindowGroup / SecretStore 边界，并用虚构消息做共享契约检查；再在真实 Mac 上逐项验证。Apple 的 [AXUIElement](https://developer.apple.com/documentation/applicationservices/axuielement) 与 [Keychain](https://developer.apple.com/documentation/security/keychain-services) 是原生能力参考。

GitHub macOS runner 可以帮助编译和做基础烟雾检查，但没有已登录微信的真实桌面，不能替代上述微信验收。PyInstaller 不支持把现有 Windows EXE 交叉编译成 Mac 应用。[打包器官方说明](https://pyinstaller.org/en/stable/index.html) · [Apple 公证说明](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

验收顺序：前后台停工与权限拒绝 → 当前聊天唯一识别 → 双方历史和时间 → 空草稿填入/回读 → 原有草稿和手改保护 → 连发与切群 → 图片/语音 → 多显示器及打包。Intel 与 Apple Silicon 分开记录，不能由一个平台代证。

## iOS：可做辅助产品，不能照搬桌面联动

普通 iOS 应用受沙盒限制，不能读取微信私有目录或进程内历史库。这不是补装一个 Python 包即可解决的问题。[Apple 平台安全说明](https://support.apple.com/guide/security/security-of-runtime-process-sec15bfe098e/web)

| 入口 | 可以做什么 | 关键边界 |
|---|---|---|
| 独立应用 / 轻量网页 | 用户主动贴入双方聊天或选截图，生成建议并复制 | 不是自动同步微信；截图内容可直接送用户选定的多模态模型，不先 OCR |
| 分享扩展 | 接收宿主实际共享的文字、图片或文件 | 微信是否给出需要的内容要实测，不承诺能分享完整聊天记录 |
| 自定义键盘 | 显示建议，用 textDocumentProxy.insertText 插入当前编辑框 | 插入点上下文是输入框文字，不是聊天气泡；拿不到另一方完整历史 |

键盘如需联网调用模型，需要申请 RequestsOpenAccess 并由用户启用完整访问；宿主也能拒绝第三方键盘，安全输入场景有额外限制。它必须提供正常键盘与切换键能力，不能把建议面板冒充一个全功能键盘。依据 [Apple 自定义键盘文档](https://developer.apple.com/library/archive/documentation/General/Conceptual/ExtensibilityPG/CustomKeyboard.html) 和 [审核指南 4.4.1](https://developer.apple.com/app-store/review/guidelines/#extensions)。

产品顺序建议：先验证用户是否愿意主动提供一小段上下文，以及是否确有可发送的建议，再决定原生分享扩展或键盘投入。键盘插入不等于发送；不把完整访问解释成取得微信历史授权。当前官网是静态宣传页，不存放模型密钥或接收私人聊天。

## 本轮交付边界

完成了资料、已有代码的平台依赖和接入路线核对。没有 Mac/iOS 安装包、设备验收或商店上架；没有要求用户关闭 SIP，也没有运行这些第三方读取器。下一阶段真正开始 Mac 开发时，应先选定是否接受上述读取器的安装条件，再取得真实 Mac 样本。
