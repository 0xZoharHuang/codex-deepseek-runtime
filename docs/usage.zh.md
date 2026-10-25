# 使用与维护

安装入口与当前版本状态见 [README](../README.md) 和 [0.6.2 验收材料](../reports/releases/0.6.2/README.md)。本文提供通用操作；[0.6.0 说明](release-0.6.0-candidate.zh-CN.md)保留该版本的图片传输与 Windows namespace 背景，历史通过结论不能替代当前候选验收。

这个发行包保留固定版本 Codex CLI 的原生执行循环、工具、沙箱、审批、会话、
多智能体、Code Mode、MCP、Skills、Hooks、队列、goals、记忆及 daemon。
DeepSeek 通过 OpenRouter 接入；适配器负责协议和传输边界，不执行模型工具。

## 日常使用

```sh
codex-deepseek runtime doctor
cd /path/to/project
codex-deepseek
codex-deepseek exec "实现修改并运行相关检查"
codex-deepseek resume --last
codex-deepseek app-server
```

macOS/Linux 的运行入口为 `~/.local/bin/codex-deepseek`，默认 home 是
`~/.local/share/codex-deepseek/codex-home/`。Windows 入口为 `$HOME\.local\bin\codex-deepseek.exe`，默认 home 是 `%LOCALAPPDATA%\codex-deepseek\codex-home`。本机 App 的 `~/.codex` 不参与运行。
需要独立工作配置时：

```sh
CODEX_DEEPSEEK_HOME=/absolute/path/to/profile codex-deepseek
```

独立 home 仍然使用原生项目规则、项目工具和共享 `~/.agents/skills`；它不是虚拟机。
`runtime setup-key` 通过隐藏输入设置专属系统凭据：macOS Keychain、Windows Credential Manager 或 Linux Secret Service。SSH/CI 可以从安全的父进程环境注入 `OPENROUTER_API_KEY`；环境值优先于系统存储。
不要把 Key 放进命令行参数、Git、TOML 或截图。

`runtime doctor` 会实际运行原生回合并用 OpenRouter generation 元数据确认
实际模型和提供商，需要少量付费调用。原生 `doctor` 保留原命令。
`--help`、`--version` 离线可用，不要求 Key 或创建新的 home。

## 工具模式与后台

本发行包默认启用 Code Mode 和 v2 协作；原生 feature 配置仍可覆盖这些默认值。
模型目录不设置强制 `tool_mode`，因此直接工具和混合工具模式仍可选择。
上游以同一个 metadata 字段表示工具模式偏好及支持声明；缺少该字段时，
原生界面可能显示模型未声明 Code Mode 的性能提示。保留该提示，不用伪造
模型训练能力来隐藏它；是否可执行以原生工具效果和验收场景为依据。
预发布版本还可能提示启用了实验性功能。

默认交互界面可以自动启动并连接自己的原生共享 daemon。包装层不额外注入
会迫使原生 TUI 改用内嵌模式的 `-c` 参数。用户显式传入某些 `-c`、profile 或
`--strict-config` 时，原生 Codex 自身可能选择内嵌模式并显示原因。

```sh
codex-deepseek app-server daemon start
codex-deepseek app-server daemon stop
codex-deepseek app-server daemon update --from-cli --yes
codex-deepseek app-server proxy
```

Unix socket 在 home 内公布地址，原生实际绑定固定临时目录下的 home 哈希地址。
检查器保留这项原生行为，只接受当前 home 的精确哈希及正确所有者/权限。
WebSocket 的端口 0 也保留，由系统分配后通过原生进程的内核监听记录登记。

## 提供商与维护

普通调用使用官方 DeepSeek 路由；已验证无法由官方端点处理的参数组合，
在请求前选择同一固定模型的兼容端点：严格结构化输出用 Morph，明确串行工具
约束用 Prime Intellect，Thinking + hosted search + peer 或未完成回答上下文组合用 Morph。
禁止 provider fallback，不通过失败后重放工具动作来换路由。generation 记录
可以核对实际模型和提供商。

固定基线、构建哈希、复杂工程验证和逐方法证据须按发行版本阅读。v0.5.0 的
三平台入口是 `reports/platforms/<target>/runtime-qualification.json`；当前候选的待完成项见 [0.6.2 报告](../reports/releases/0.6.2/README.md)。
矩阵区分实际场景调用、保留的原生处理器、后端/平台依赖和上游未实现方法。

## 0.6.0 可选图片维护

图片传输默认关闭；使用匹配候选包并按[配置说明](release-0.6.0-candidate.zh-CN.md)启用后，可在选定 home 下执行：

```sh
codex-deepseek runtime image-status
codex-deepseek runtime image-cleanup --all-unpinned
codex-deepseek runtime image-reconcile-uploads
codex-deepseek runtime image-reconcile-calls
```

Windows 使用对应的 `.exe` 入口；并行候选应使用自己的绝对入口。`image-status` 返回状态，退出 0 不代表远端文件已清空。其他维护命令仅在对应操作完成时返回 0；未确认消费、未知上传、失败或超时均需保留回执。`--all-unpinned` 不会越过仍在使用或结果未知的文件锁定。

不要复制账本到另一个 home 后尝试清理。账本绑定缓存根路径与 credential scope，非空旧无绑定账本会被保留并拒绝自动采用。更换 key 不会自动删除旧 scope 的文件。删除本地目录或关闭功能不能代替远端 DELETE 与不存在读回。

## 网页读取

原生 live 搜索继续由 OpenRouter Exa 提供；打开具体网页、页内查找和跟随链接由
随包注册的 `codex_web` MCP 工具完成。`open` 真正抓取网页，返回页面引用、编号
文本、链接和源数据哈希；`find` 查该页面的文字；`click` 根据实际链接编号继续
读取。页面引用可以在相同独立 home 的 MCP 进程重启后继续使用，受缓存淘汰限制。
它是 HTML/文本/PDF 文档读取器，不执行 JavaScript，也不使用登录浏览器的 cookies。
关闭或调整它使用原生 MCP 配置；其他用户 MCP 配置、注释和显式禁用会保留。

```toml
[mcp_servers.codex_web]
enabled = false
```

turn interrupt 与 MCP cancellation 是不同事件。按同版官方 CLI 的原生行为，
中断采样后，已经发出的 MCP 请求可能仍在执行；关闭所有者会关闭请求。读取器
收到 MCP 的显式 cancellation 通知时会取消 HTTP。不要将这三种情况合并成
“任意中断都立即停止所有工具”。

完整架构和 v0.5.0 证据范围见 [稳定运行方案](STABLE_PLAN.zh-CN.md)；v0.4.1 的[历史方案](stable-solution.zh.md)仅保留当时结果。

升级通过本发行包的验证安装器；通用上游 updater 被拒绝，以免覆盖专用补丁。
先在新 root 与独立 home 验证候选，并保留旧入口。做同 home 变更前，确认该 home
的任务、子进程与 daemon 均已结束；必要时才使用 `daemon update --from-cli --yes`。
0.6.0 Windows 的 namespace 不能直接采用旧 `.sandbox*` 状态；自动迁移尚未提供。
候选回退和未来同 home 迁移的区别见 [0.6.0 说明](release-0.6.0-candidate.zh-CN.md)。

每个等价结论仅适用于其报告绑定的 Codex 版本、平台与产物；历史三平台结果不自动延伸到 0.6.2 或后续源码。
模型判断质量、厂商隐藏状态、OpenAI 云端权益不属于可由适配器复制的能力。
原生进程遭 macOS SIGKILL 后可能遗留命令后代，这是官方二进制同样复现的
上游限制；正常退出、回合取消、包装层退出、服务故障和冷恢复各自独立验证。
