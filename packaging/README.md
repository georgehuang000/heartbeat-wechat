# 心动微信 Windows 安装包

当前版本：0.1.1，Windows 10 2004（19041）及以上 / Windows 11，x64。安装器为 Inno Setup 6.7.3，载荷沿用现有 PyInstaller 单文件方案。安装前先完全退出旧程序。

2026-10-19 更新为 v0.1.1：默认下载 **63.6 MB 标准包**，缺少 WebView2 时自动从 Microsoft 下载并安装；已有时跳过。275.8 MB 完整离线包为备用，两者通过 [GitHub Releases v0.1.1](https://github.com/georgehuang000/heartbeat-wechat/releases/tag/v0.1.1) 提供下载，使用相同应用载荷，均为未签名预览版。旧版附件保留。服务器部署排除本地 `downloads/*.exe`，只保留网页所需的元数据与校验文本。

## 产物

- `../release/installer/HeartWeChat-Setup-0.1.1-x64.exe`：中文、当前用户标准安装器。
- `../build/standard-installer-2026-10-16/offline-release/`：原已发布完整离线包及完整校验记录；新的离线构建输出到 `release/installer-offline/`。
- `../release/installer/SHA256SUMS.txt`：最终安装包 SHA-256。
- `../release/installer/resource-audit.json`：资源白名单、前端一致性、架构、内嵌图标检查。
- `../release/installer/source-audit.json`：随本次构建生成的 Python 模块与前端文件一致性检查，以报告中的 SHA-256 和文件数为准。
- `../release/installer/installation-check.json`：本机安装、覆盖安装、隔离运行、卸载及保留数据检查。

程序默认安装到 `%LOCALAPPDATA%\Programs\HeartWeChat`；个人设置和记录继续位于 `%LOCALAPPDATA%\HeartWeChat`。不修改原来的项目根目录单文件入口，不强制关闭正在运行的程序。卸载没有递归删除安装目录或个人数据目录的自定义动作。

## 环境与构建

使用项目已有 `.venv`（来自 `wechat-ai-quiz-py312` Conda 环境），Python 3.12.11、PyInstaller 6.22.3；Node 位于 `D:/tools`，构建脚本使用 PowerShell 7（本机 7.6.5）。没有向 Conda base 安装依赖。`pip check` 已通过。

准备官方 Inno Setup 6.7.3 编译器，当前项目路径为 `build/installer-tools/InnoSetup/ISCC.exe`；也可以用 `-Compiler` 参数指定。官方安装器 Authenticode 校验有效，签发主体为 Pyrsys B.V.。简体中文翻译来自同一官方源码 tag 下 `Files/Languages/Unofficial/ChineseSimplified.isl`，保留原维护者声明。

```powershell
.venv/Scripts/python.exe scripts/collect_installer_notices.py
./scripts/prepare_webview2.ps1
./scripts/build_installer.ps1
./scripts/verify_installer.ps1
```

复用本次已审计载荷时可使用 `./scripts/build_installer.ps1 -SkipAppBuild`。构建脚本不复制到项目根目录，不覆盖正在使用的便携入口。个人数据不作为打包输入；`data/` 下只允许三份固定、已审查的依赖资源。

## WebView2 与发布边界

默认构建标准包，内置约 1.9 MB 的 **Evergreen Bootstrapper**；配置在 `webview2-bootstrapper.json`。文件来自微软官方跳转地址，下载后通过 Microsoft Authenticode 校验，再固定 SHA-256；该摘要为本机计算值，不是微软另行公布的摘要。配置中的 1.3.287.9 是引导程序文件版本，实际运行库由 Microsoft 在线选择。完整离线包仍使用 `webview2-runtime.json` 的 Microsoft WinGet 版本、下载地址与官方摘要。准备和打包都验证 SHA-256 及有效微软签名；缺文件、模式不匹配或校验失败时停止。

`prepare_webview2.ps1` 只下载、验签并保存到 `build/installer-prerequisites`，不会在开发机安装组件。网络不可用时可通过 Microsoft 官方入口下载与固定清单一致的文件，再运行 `./scripts/prepare_webview2.ps1 -SourceFile '文件的完整路径'`。版本升级需重新核对官方来源、签名和摘要。构建的 `release-manifest.json` 记录实际模式、组件文件版本、大小、SHA-256 与签名；`webview2_bundled` 表示携带安装组件，`webview2_runtime_bundled` 才表示携带完整运行库。

标准包命令沿用上面的默认命令。重新生成完整离线包时使用：

```powershell
./scripts/prepare_webview2.ps1 -WebViewMode offline
./scripts/build_installer.ps1 -SkipAppBuild -WebViewMode offline
```

安装器先检测 .NET Framework 4.8 和 WebView2。已有 WebView2 时跳过；缺少时从包内解出微软原始文件，以当前用户运行 `/silent /install`，之后再次确认注册信息。标准包的引导程序直接从 Microsoft 下载运行库，失败时停止并显示错误码、网络重试与离线包入口；不经过自有服务器，也不新增自建 Windows 服务。离线包在包内提供完整组件。需要重启时提示保存工作并重启后重试。共享 Evergreen Runtime 后续由 Microsoft 更新，卸载本应用不删除它。离线安装组件不代表聊天 API 能离线使用。

`scripts/test_webview2_prerequisite.ps1` 用 Inno 编译运行生产逻辑的替身测试：标准包 10 个场景，包括已有跳过、缺少、错误返回、假成功、并发、重启、网络失败与重试；加 `-WebViewMode offline` 验证离线包 8 个场景。不安装真实组件或修改注册表。`verify_installer.ps1` 验证实际标准包的安装/升级/运行/卸载。本机已有 WebView2；干净 Windows 上缺失组件的真实联网补装仍需另行实测，不能用替身测试代替。2026-10-16 本机直连微软 CDN 超时，构建用组件通过现有代理取得且验签有效；不能据此承诺所有大陆网络畅通。

当前产物未签名，Windows 可能显示未知发布者。没有将本机测试宣称为其他电脑兼容性、真实微信操作或真实模型效果验收。第三方许可证及可获得的版权文本在 `THIRD-PARTY-NOTICES.txt`；固定审核依赖中没有独立许可文件的情况已原样标注，面向外部公开分发前仍需确认对应许可。

## 代码签名与下载同步

已接入基于 Windows 证书库的 SignTool 签名流程：先签主程序，再由 Inno 签署卸载程序和最终安装器。`-RequireSignedRelease` 缺少证书时直接失败；不会悄悄降级为未签名发布。真实签名成功路径仍待取得 CA 证书后验证。

完整的个人证书选型、身份限制、操作命令及费用核查见 [签名接入说明](../docs/windows-code-signing-2026-10-15.md)。

免费渠道及开源/闭源比较见 [发布路径比较](../docs/webview2-and-release-options-2026-10-16.md)。仓库已由另一个用户授权任务公开，整体开源许可仍待决定；本轮不改变许可证，也未申请签名服务或提交商店。

网页下载同步使用 `scripts/sync_installer_download.ps1`；签名后的文件会生成新的哈希文件名、SHA-256 和实际发布者说明。未签名预览必须显式传入 `-AllowUnsigned`。所有输出仅写到本地项目，不自动部署公网。

GitHub 渠道需要先发布对应附件，再更新本地网页。同步脚本会核对附件名称、字节数、GitHub 提供的 SHA-256 与公开 URL；未发布、缺附件或摘要不符时拒绝更新。渠道保存在 `downloads/release.json`，后续不传渠道参数会沿用 GitHub，避免新构建意外改回服务器下载。固定 tag 与带内容摘要的文件名确保页面版本、大小和校验对应同一个文件。

```powershell
./scripts/sync_installer_download.ps1 -AllowUnsigned -GitHubRepository georgehuang000/heartbeat-wechat -GitHubTag v0.1.1
./scripts/sync_installer_download.ps1 -AllowUnsigned -CheckOnly
```

正式签名后将 `-AllowUnsigned` 换为已有的 `-ExpectedThumbprint` 校验；签名会改变摘要，必须上传新的文件。同步脚本从同目录构建清单核对模式和摘要，再更新主下载。备用完整包独立记录于网站 `downloads/offline-release.json` 和 `offline-SHA256SUMS.txt`，静态检查校验链接与摘要一致。同步脚本本身不创建 Release，也不部署官网；服务器部署脚本会根据 `provider=github-release` 排除本地 EXE。

```powershell
./scripts/test_release_guards.ps1
./scripts/sync_installer_download.ps1 -AllowUnsigned
./scripts/sync_installer_download.ps1 -AllowUnsigned -CheckOnly
```

## 方案依据

- [Inno Setup 官方下载](https://jrsoftware.org/isdl.php)与[固定版本源码](https://github.com/jrsoftware/issrc/tree/is-6_7_3)。
- [当前用户安装权限](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm)。
- [WebView2 分发与依赖检查](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)。
- [开始菜单可选项](https://jrsoftware.org/ishelp/topic_setup_allownoicons.htm)与[隐藏开始菜单页的影响](https://jrsoftware.org/ishelp/topic_setup_disableprogramgrouppage.htm)。

详细流程及难点见 `../docs/next-line-v3-and-installer-2026-10-15.md`。
