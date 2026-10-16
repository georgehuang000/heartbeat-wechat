# 结构与当前边界

- `src/galchat`：本地窗口、当前聊天识别、历史组装、媒体处理、模型协议和草稿保护。
- `prototype-heart-wechat/src`：React 界面，五页设置与独立赞助作者页。
- `prototype-heart-wechat/public/assets`：角色、场景、品牌、表情素材和作者指定的支付宝收款码。第三方素材保留原许可。
- `scripts/prepare_reader.py`：下载并校验固定版本读取模块，只解出必要源码，不导入或执行模块，不读取微信。
- `tests`：虚构样本与离线检查。通过检查不代表所有微信版本、消息资源和回复内容都已验收。

消息默认读取最近 100 条，可精确设置 10–5000 条；读取上限与实际模型上下文预算不同。双方消息、发送身份、原时间和相邻间隔参与组装。严格等待 3 秒是可选设置，关闭后仍只运行一个生成任务。

回复采用外部 API，不下载或运行本地大模型。支持 Chat Completions、Responses、Messages 协议；图片能力取决于所选模型。网页链接当前只使用卡片信息，正文抓取留待第二期。

目前仍是预览项目。回复可能出现角色或事实归属错误；连续切换草稿、群聊指向、语音首次转换与跨机器表现需要更多实际验证。程序只填入草稿，用户确认并自己发送。

## 作者页面与官网端口

设置右上角「赞助」进入作者页，支付宝图片原文件显示，支持放大；GitHub 与官网在外部浏览器打开。入口不触发读取聊天、模型请求或支付。

地址集中在 `prototype-heart-wechat/src/authorLinks.js`。官网使用 `https://82.157.178.52/heartbeat-wechat/`，本机开发预览可使用 `scripts/preview_website.ps1`。GitHub README 直接显示支付宝原始收款码，Sponsor 按钮指向仓库内的赞助说明，不跳转个人网站。

网站有独立的本地 Git 仓库，不作为这个应用仓库的嵌套仓库上传。

[Mac / iOS 移植可行性](APPLE-PLATFORMS.md) · [本次公开上传检查](PRIVACY-CHECK.md) · [赞助作者](SPONSOR.md)

## 第三方资料

- 本地读取器：[`wechatauto-replica`](https://github.com/fanyuantaier/wechatauto-replica)，固定版本 1.2.5.1，Apache-2.0；读取模块按原样校验，本项目在外层适配。
- 桌面：pywebview、Microsoft WebView2、UI Automation。
- 界面：React、Vite、Phosphor Icons；表情素材许可位于其素材目录。
- 依赖通知：`packaging/THIRD-PARTY-NOTICES.txt`。第三方依其原许可；项目尚未指定整体开源许可证。
