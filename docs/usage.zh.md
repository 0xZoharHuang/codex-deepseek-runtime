# 使用与维护

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

运行入口为 `~/.local/bin/codex-deepseek`。默认 home 是
`~/.local/share/codex-deepseek/codex-home/`；本机 App 的 `~/.codex` 不参与运行。
需要独立工作配置时：

```sh
CODEX_DEEPSEEK_HOME=/absolute/path/to/profile codex-deepseek
```

独立 home 仍然使用原生项目规则、项目工具和共享 `~/.agents/skills`；它不是虚拟机。
API Key 使用专属 macOS Keychain 项，`runtime setup-key` 通过隐藏输入设置。
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
约束用 Prime Intellect，Thinking + hosted search + peer 上下文组合用 Morph。
禁止 provider fallback，不通过失败后重放工具动作来换路由。generation 记录
可以核对实际模型和提供商。

固定基线、构建哈希、复杂工程验证和逐方法证据范围见随发布提供的
`reports/acceptance.json` 与 `reports/runtime-method-matrix.json`。
矩阵区分实际场景调用、保留的原生处理器、后端/平台依赖和上游未实现方法。

升级必须通过本发行包的验证安装器。原生生产 updater 会覆盖专用补丁，因此被
明确拒绝；先停止当前 home 的 daemon，再安装通过验收的新发行包，必要时用
`daemon update --from-cli --yes` 更新其包。不要直接用 npm 更新原生二进制来
替换本包。旧安装目录在新包验证前保留，便于回退。

等价范围限于已冻结 Codex 版本和已验证 macOS arm64 的本地 runtime。
模型判断质量、厂商隐藏状态、OpenAI 云端权益不属于可由适配器复制的能力。
原生进程遭 macOS SIGKILL 后可能遗留命令后代，这是官方二进制同样复现的
上游限制；正常退出、回合取消、包装层退出、服务故障和冷恢复各自独立验证。
