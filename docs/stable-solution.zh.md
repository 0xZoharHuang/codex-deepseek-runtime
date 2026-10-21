# Codex + DeepSeek 的固定版本运行方案

目标是保留 Codex CLI 的本地执行机制，用 DeepSeek 提供模型推理。版本基线固定为 Codex `0.164.0-alpha.7`、macOS arm64，模型为 `deepseek/deepseek-v4.1-flash-1017`。这是可复现的冻结方案；上游 CLI 是预发布版本，不能由本项目把它变成上游稳定版。

```text
用户任务
   |
   v
codex-deepseek 独立入口和独立 home
   |
   v
原生 Codex CLI
   |-- 原生任务循环、Code Mode、工具发现、MCP
   |-- 原生沙箱、权限审批、Hooks、Skills
   |-- 原生会话、分页历史、队列、目标、子智能体
   |
   |-- 模型请求 --> 本地 Responses 协议桥 --> OpenRouter --> DeepSeek
   |
   `-- 网页工具 --> 原生 MCP 客户端 --> 独立 codex_web 文档读取器
                                    |-- open：真实抓取，编号文本和链接
                                    |-- find：查已抓取页面的准确文字
                                    `-- click：跟随页面中的链接并抓取
```

模型桥不执行 shell、文件修改或子智能体动作，不另写 agent loop。浏览器通过官方 MCP SDK 提供普通工具，由原生 Codex 发现、审批、调度、记录和管理生命周期；不能把它与协议桥混为一个工具执行循环。

## 安装与使用

在独立 Python 3.11+ 中运行发布源代码的安装器。安装器下载固定 URL 的原生包并逐文件校验；已有完整原生包时直接校验复用，避免重新下载。依赖通过 `requirements.lock` 固定版本和哈希。它只切换 `~/.local/bin/codex-deepseek`，不替换全局 Codex。

```sh
python3 install.py
codex-deepseek runtime setup-key
codex-deepseek runtime doctor
cd /path/to/project
codex-deepseek
```

OpenRouter Key 放在服务 `ai.codex.deepseek.openrouter`、账户 `default` 的 macOS Keychain 中。模型工具进程不会继承 OpenRouter Key。不要把 Key 写入命令参数、TOML、仓库或验收报告。

默认 home 是 `~/.local/share/codex-deepseek/codex-home`。额外任务可设置 `CODEX_DEEPSEEK_HOME=/absolute/profile`，与 App 的 `~/.codex` 不重叠。多个独立 home 有各自的 provider 状态、会话、服务和网页缓存；原生共享项目规则与 `~/.agents/skills` 仍按 CLI 自身规则使用。

```sh
codex-deepseek exec "修改代码并运行测试"
codex-deepseek resume --last
codex-deepseek app-server
```

网页搜索默认使用 live 模式，经 OpenRouter Exa 返回来源与引用。读指定网页时应实际调用 `codex_web.open`；用返回的页面引用执行 find，用编号链接执行 click。搜索摘录不证明已打开页面。

## 适配层做了什么

- 转换 Responses 消息、命名空间、自定义工具与语法、延迟工具发现、peer 上下文和流式事件，保留调用身份与原生历史。
- 普通模型请求固定官方 DeepSeek；严格结构化输出、明确串行工具约束及特定 Thinking/上下文/hosted-search 组合在请求前选择同一模型的已验证兼容端点。禁止 provider fallback，不在协议桥里重放工具动作。
- 原生重试可能带着未完成的推理或回答片段继续请求。适配器将其原文保留为明确标记的不可执行上下文，避免后端误当成 assistant prefill；没有把适配器标记冒充模型推理。
- 对要求 JSON Schema 的最终回答做本地校验，不访问远端 schema。错误的 Markdown 包装、类型或字段不能变成“完成的结构化结果”；它们进入原生错误和重试路径。文本增量与工具参数仍可流式传输。
- 原生源码补丁维持 provider 绑定、传输与独立凭据边界；此次网页扩展没有再改任务循环、文件编辑、沙箱、审批或子智能体业务处理器。

## 验收怎样证明

证据入口为 `reports/acceptance.json`。报告将实际成功操作、后端拒绝边界、模型表现和未适用平台分别记录，不以 schema 存在推导功能成功。

原来 64 个未逐项实跑的“本地”方法已逐项处理。其中 60 个使用实际状态或效果验收；另 4 个经固定源码和运行确认是托管 Apps/远端插件服务接口，不能算正向本地功能。原有 65 个场景观测方法与新增 60 个操作合计覆盖修正后 125 个本地客户端方法。每一项仍以矩阵所列的验收语义为准，不代表所有参数和故障组合都已穷举。

真实工程验收包括修复前失败的 Jinja 与 HTTPcore，独立 oracle、原生子智能体复核、完整套件和实际网页 open/find/click。HTTPcore 还包含编辑后的中断与冷恢复、真实 TCP 并发压力、asyncio/Trio 的 TLS 取消关闭检查。失败的初始任务和独立压力结果保留，修复后通过同一保存任务继续并重新验证。

受控协议故障分别注入未声明工具和错误 JSON；实际运行证明由原生 CLI 发起重试，恢复后完成，未执行或重放客户端工具。浏览验收分别覆盖随机页面、公开 HTTPS、跨进程引用恢复、显式 MCP 取消和官方 CLI 生命周期对照。

## 必须保留的原生边界

- 这里没有 OpenAI cached/indexed 搜索索引、地理偏好或图片搜索后端。当前 Exa 映射遇到不能表达的语义会报错，不静默忽略。
- 文档读取器不执行网页 JavaScript，不登录网站，不提供视觉浏览器自动化。HTML、文本和 PDF 读取与浏览器自动化是不同工具能力。
- 原版和补丁版 CLI 都可能在 turn interrupt 后保留已经发出的 MCP 请求；关闭所有者会关闭它。MCP 客户端明确发送 cancellation 通知时，读取器会取消 HTTP 请求。这三个生命周期分别验收。
- 原版 macOS CLI 遭直接 SIGKILL 后可能遗留命令后代。不能宣称包装层改变了这一上游行为。
- 五个系统用户验证方法在上游就要求 ChatGPT 账户身份；本方案对无账户边界做了原版对照，未注册、删除或提示使用用户的真实 OS 凭据。云端 Apps、Realtime、账号权益、远程控制后端和 Windows 沙箱不属于本次 macOS 本地等价验收。
- 上游声明但未实现的 `thread/prediction/request` 仍按上游范围记录，不伪造实现。

## 更新、回退与磁盘

停止当前独立 home 的 daemon 后，通过验证安装器升级；需要时执行 `codex-deepseek app-server daemon update --from-cli --yes`。不要用原生生产 updater 覆盖专用二进制。旧安装目录不原地修补，新候选通过完整性和验收后才切换入口。保留前一已验证版本，可用其完整 `venv/bin/codex-deepseek` 路径和独立 home 直接运行。

网页缓存按逻辑 payload 设 64 MiB 上限，旧引用会被淘汰并明确报错。下载和解析有限额，截断与分页均显式报告。实际 SQLite 分配可能略高于 payload 上限。

测试结束后清理已关闭测试 home 的 daemon 包副本、临时插件克隆、Rust target 与测试虚拟环境；保留源码、锁文件、构建与验收收据、真实任务补丁和原始失败证据。私有轨迹可以校验后压缩存档，不发布 Key、OAuth 凭据、完整上下文或用户路径。`df` 是全盘可用空间指标，不能把重叠 `du` 数字相加当作物理回收量。

官方实现参考：[Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[Codex 网页搜索](https://learn.chatgpt.com/docs/web-search)、[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[OpenRouter 搜索工具](https://openrouter.ai/docs/guides/features/server-tools/web-search)。
