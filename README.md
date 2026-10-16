# 心动微信 ♥

微信旁边的 galgame 风格聊天提词器：看懂双方聊天，给出几句可选台词。点击选项填入微信草稿，发送由你决定。

粉色对话框、对方角色、聊天风格与表情设置；既可以认真接话，也可以轻松玩梗。

## 当前功能

- 根据当前微信聊天读取双方历史，显示联系人备注或群名称。
- 16 个年龄与性别形象预设，以及黑长直默认形象、群聊伙伴形象；角色外观不代表真实年龄或身份。
- 支持单聊与群聊，保留发送身份、时间、引用来源和消息间隔。
- 历史默认 100 条，可用分段滑块或数字框精确设置 10–5000 条；实际送入模型受上下文预算约束。
- 严格等待 3 秒可开关；保留旧选项直到新一组准备好。可按联系人询问是否开启辅助。
- 支持常见聊天 API 协议和多模态图片输入，微信语音转文字优先；缺失媒体不会被当成已理解。
- 设置分为形象、台词、上下文、显示、连接；另有独立的赞助作者页。

这是 Windows 桌面预览项目。当前适配以本机验证的微信 4.1.13.65 为主，其他版本需要单独验证。实际回复仍可能误判事实或动作归属，使用前请确认。

## 开发运行

需要 Windows x64、Python 3.12、Node.js、Microsoft WebView2，以及你自己的模型 API。优先复用独立的 Conda 环境，不在 Conda base 安装。

在独立 Python 3.12 环境创建项目虚拟环境，然后执行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-reader.txt
.venv/Scripts/python.exe scripts/prepare_reader.py
npm --prefix prototype-heart-wechat ci
npm --prefix prototype-heart-wechat run build
.venv/Scripts/python.exe main.py
```

`prepare_reader.py` 只获取并校验固定版本的读取依赖，不读取微信。本项目不是腾讯官方接口。

只看虚构界面：在 `prototype-heart-wechat` 中运行 `npm run dev`。它不连接真实微信或模型。

构建 Windows 单文件：安装 `requirements-build.txt`，构建前端后，用 PyInstaller 执行 `packaging/heart-wechat.spec`；环境变量 `GALCHAT_BUNDLE_MODE=onefile` 指定单文件模式。安装器与签名不是源码运行的前提。

## 数据与边界

源码仓库不包含聊天记录、数据库缓存、个人密钥、调试截图、运行环境和旧安装包。开发运行的数据留在本地 `data/`；打包版默认使用 `%LOCALAPPDATA%/HeartWeChat`，API 密钥通过 Windows DPAPI 保存。

调用模型时会按设置传递当前选定的聊天上下文及可用附件，使用你配置的服务。切到其他应用后暂停自动操作。程序不自动发送微信消息，不自动支付。

当前链接只使用卡片预览，网页正文读取计划在第二期；全文附件、群聊指向及跨版本兼容仍有待完善。[结构、当前限制与第三方资料](docs/public/README.md)。

## 支持作者

觉得有趣，欢迎 Star、提交 Issue，或在「设置 → 赞助」请作者喝杯奶茶。赞助自愿，不影响功能。

- [GitHub 项目](https://github.com/georgehuang000/heart-wechat)
- 官网尚未上线，本机预览约定使用 `http://127.0.0.1:10001/`，不作为公网地址。

第三方组件与素材遵循各自许可，许可文本保留于对应目录及 `packaging/THIRD-PARTY-NOTICES.txt`；项目尚未指定整体开源许可证。
