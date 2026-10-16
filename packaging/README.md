# 心动微信 Windows 安装包

当前版本：0.1.0，Windows 10 2004（19041）及以上 / Windows 11，x64。安装器为 Inno Setup 6.7.3，载荷沿用现有 PyInstaller 单文件方案。安装前先完全退出旧程序。

## 产物

- `../release/installer/HeartWeChat-Setup-0.1.0-x64.exe`：中文、当前用户安装器。
- `../release/installer/SHA256SUMS.txt`：最终安装包 SHA-256。
- `../release/installer/resource-audit.json`：资源白名单、前端一致性、架构、内嵌图标检查。
- `../release/installer/source-audit.json`：16 个关键 Python 模块和 200 个前端文件与当前源码一致。
- `../release/installer/installation-check.json`：本机安装、覆盖安装、隔离运行、卸载及保留数据检查。

程序默认安装到 `%LOCALAPPDATA%\Programs\HeartWeChat`；个人设置和记录继续位于 `%LOCALAPPDATA%\HeartWeChat`。不修改原来的项目根目录单文件入口，不强制关闭正在运行的程序。卸载没有递归删除安装目录或个人数据目录的自定义动作。

## 环境与构建

使用项目已有 `.venv`（来自 `wechat-ai-quiz-py312` Conda 环境），Python 3.12.11、PyInstaller 6.22.3；Node 位于 `D:/tools`，构建脚本使用 PowerShell 7（本机 7.6.5）。没有向 Conda base 安装依赖。`pip check` 已通过。

准备官方 Inno Setup 6.7.3 编译器，当前项目路径为 `build/installer-tools/InnoSetup/ISCC.exe`；也可以用 `-Compiler` 参数指定。官方安装器 Authenticode 校验有效，签发主体为 Pyrsys B.V.。简体中文翻译来自同一官方源码 tag 下 `Files/Languages/Unofficial/ChineseSimplified.isl`，保留原维护者声明。

```powershell
.venv/Scripts/python.exe scripts/collect_installer_notices.py
./scripts/build_installer.ps1
./scripts/verify_installer.ps1
```

复用本次已审计载荷时可使用 `./scripts/build_installer.ps1 -SkipAppBuild`。构建脚本不复制到项目根目录，不覆盖正在使用的便携入口。个人数据不作为打包输入；`data/` 下只允许三份固定、已审查的依赖资源。

## WebView2 与发布边界

本次 Microsoft 引导程序下载多次遇到 TLS 连接重置/超时，因此最终安装包**没有附带 WebView2 引导程序或运行库**。安装器检查 .NET Framework 4.8 和 WebView2；缺少运行库时停止安装并提供 Microsoft 官方入口。运行库安装后重新运行安装包即可。本机已有 WebView2，安装及运行时检查通过；缺失运行库分支尚未在干净虚拟机实测。

脚本保留可选的正式引导程序打包入口：将 Microsoft 官方文件置于 `build/installer-prerequisites/MicrosoftEdgeWebview2Setup.exe`，构建脚本会要求有效的 Microsoft Corporation 数字签名。若启用该模式，需同步更新安装说明与网页文案，再验证缺失运行库场景。

当前产物未签名，Windows 可能显示未知发布者。没有将本机测试宣称为其他电脑兼容性、真实微信操作或真实模型效果验收。第三方许可证及可获得的版权文本在 `THIRD-PARTY-NOTICES.txt`；固定审核依赖中没有独立许可文件的情况已原样标注，面向外部公开分发前仍需确认对应许可。

## 代码签名与下载同步

已接入基于 Windows 证书库的 SignTool 签名流程：先签主程序，再由 Inno 签署卸载程序和最终安装器。`-RequireSignedRelease` 缺少证书时直接失败；不会悄悄降级为未签名发布。真实签名成功路径仍待取得 CA 证书后验证。

完整的个人证书选型、身份限制、操作命令及费用核查见 [签名接入说明](../docs/windows-code-signing-2026-10-15.md)。

网页下载同步使用 `scripts/sync_installer_download.ps1`；签名后的文件会生成新的哈希文件名、SHA-256 和实际发布者说明。未签名预览必须显式传入 `-AllowUnsigned`。所有输出仅写到本地项目，不自动部署公网。

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
