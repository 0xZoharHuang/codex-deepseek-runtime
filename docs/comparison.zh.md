# Codex CLI、DSH 与 DeepSeek：固定版本历史比较

本文保留 2026-10-20 至 2026-10-21 的研究与早期 macOS 验收范围。当前发布状态见 [README](../README.md)；0.6.0 候选不能继承本页的历史验收结论。

本次发布研究快照：2026-10-20 至 2026-10-21。Codex 最新预发布在审计期间
由 `0.164.0-alpha.6` 更新为 `0.164.0-alpha.7`；最终原生基线固定为 alpha.7，
commit `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`。稳定版研究起点为
`0.163.0`。DSH 选择 `dsh-v0.2.1-rc.2`，commit
`687f282e8625c6b8d11ff58211a90524be2e4317`。OpenRouter 的模型固定为
`deepseek/deepseek-v4.1-flash-1017`，generation 记录使用其规范名
`deepseek/deepseek-v4.1-flash-20261017`。

版本来源：[Codex 发布记录](https://github.com/openai/codex/releases)、
[DSH 发布记录](https://github.com/deepseek-ai/deepseek-harness/releases)、
[OpenRouter 模型页](https://openrouter.ai/deepseek/deepseek-v4.1-flash-1017)。
这些是本次冻结快照，不是对未来 latest 的承诺；更新需要重新验收。

## Runtime 的主要区别

| 维度 | Codex CLI | DSH |
|---|---|---|
| 组成 | 原生 Rust 执行链；provider 和工具是接入边界 | Cordis 插件树，模型、会话、工具注册和 agent loop 都可替换 |
| 运行入口 | TUI、exec、app-server、SDK 客户端、原生 daemon | web、headless、sdk、sdk-minimal、acp 等 profile/bundle 组合 |
| 工具与约束 | Code Mode、直接工具、工具发现、MCP、Skills、Hooks、沙箱和审批 | 通过插件服务和执行守卫组合工具、沙箱与审批策略 |
| 会话与协作 | 原生持久化、分页历史、fork、压缩、队列、goals、多智能体生命周期 | 会话事件日志与投影、可替换的会话/子智能体 provider |
| 扩展重心 | 保留 Codex 客户端协议和执行行为，接入模型/外部工具 | 替换产品组成、UI、执行环境或编排方式 |
| 当前目标的维护面 | 对固定原生源码做小范围接入补丁，并保留原生处理器 | 若要求 Codex 行为等价，还需另外实现并维护那套行为和协议 |

DSH 并非缺少沙箱、审批或多智能体；其优势是这些组成部分可被替换和组合。
对“继续使用 Codex CLI 的完整本地 runtime”这个明确目标，保留 Codex 自身的
执行实现有更直接的验收基准。本次没有做相同模型、相同任务下两者速度或
模型成功率的排名，也没有声称 DSH 适合或不适合所有产品。

固定源码依据：[DSH 架构](https://github.com/deepseek-ai/deepseek-harness/blob/687f282e8625c6b8d11ff58211a90524be2e4317/docs/architecture.md)、
[Codex provider 定义](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/model-provider-info/src/lib.rs)、
[Codex app-server 协议](https://github.com/openai/codex/tree/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/app-server-protocol)。

## 为什么不能只改 base_url 就宣称完整适配

固定版本的 Codex 使用 Responses 协议及其原生工具/消息结构。OpenRouter 的
Responses 接口和各个 DeepSeek 托管端点对参数、消息和工具结构的支持不同。
实际发现的接入边界包括 custom tool grammar、namespaces、延迟工具发现、
原生 peer messages、Thinking 与 hosted search 的组合、严格 JSON schema、
串行工具约束、流式终止和调用身份、辅助审批/记忆模型，以及后端传输策略。

本实现将这些差异限制在明确的接入层：不在适配器里执行工具，不另写 agent
loop，不取消原生沙箱、用户审批或 Hook 信任。普通请求优先官方 DeepSeek
提供商；官方端点不能满足的已验证参数组合在请求前选择同一模型的固定
兼容端点，禁止 fallback，并保留实际 generation 提供商证据。具体路由、
源码补丁和复现方法见主 README 与 `upstream/README.md`。

## 本次“等价”的验收范围

等价对象是固定 Codex CLI 在已验证 macOS arm64 平台上的本地 runtime 能力。
模型判断与推理质量、厂商隐藏状态、OpenAI 托管模型服务和账号权益，不可能
通过一个 API 适配器变得相同，也不属于本任务的等价条件。

证据分为三类，不能互相替代：

1. 固定源码与官方二进制：原生处理器保留、补丁可审计、协议 schema 对照。
2. 运行时场景：工具实际效果、审批、认证、进程、冷恢复、协作、daemon、记忆。
3. 复杂真实工程：修复前失败、独立 oracle、完整回归、取消/连接压力、冷恢复。

0.4.0 对 0.3.0 遗留的 64 个方法逐项复核，其中 60 个本地操作通过，另外 4 个
经固定源码确认属于托管 Apps/远端插件服务。修正后的矩阵中，125 个本地方法
均有正向运行证据，并分别标记场景、后端边界、平台依赖及上游未实现项。
例如 alpha.7 声明了 `thread/prediction/request`，其原生实现仍返回未实现；
这不是新增适配能力，也不能因为 schema 存在就计入实测通过数量。

网页搜索采用 OpenRouter Exa；实际页面读取、查找和链接导航通过 Codex 官方
支持的 MCP 接口扩展，使用官方 Python SDK。它保留 native 工具调度，提供
HTML/文本/PDF 文档阅读，不能被描述为拥有登录态和 JavaScript 的视觉浏览器。
v0.4.1 的运行方案保留在[历史方案](stable-solution.zh.md)。后续发布状态与证据范围见 [README](../README.md) 和 [0.6.0 候选说明](release-0.6.0-candidate.zh-CN.md)。

本方案始终使用独立 home、进程、服务、Keychain 命名空间和发布入口。运行
Codex CLI + DeepSeek 不需要改动本机 Codex App 的登录、会话或配置。
