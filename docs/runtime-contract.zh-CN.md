# 原生 runtime 契约与适配边界

CDS 的目标是让 Codex CLI 的本地 runtime 使用固定 DeepSeek 模型。工具执行、审批与沙箱、会话持久化、压缩、队列、目标、原生多智能体都由 Codex 的实现负责。适配器不得另建 agent loop、审批器、工具调度器或子智能体系统。

## 实际实现

发行包从固定上游 `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3` 加公开补丁构建。Windows 使用 P41，Linux/macOS 使用 P35。这是带补丁的 native build。补丁除 provider 集成外，还涉及请求容量恢复、历史安装与 Windows 沙箱、辅助进程生命周期，不能描述成“完全未修改的官方二进制”。

Python 网关连接 OpenRouter `/api/v1/responses`。它转换工具声明、调用与流事件，不执行模型生成的工具。网页读取由原生 MCP 调用随包文档读取器。OpenRouter 路由和图片传输策略独立记账；选择了某个 provider 只证明请求的选择，服务端没有返回身份时不能声称已经独立观察到实际 provider。

## 一项修复应满足什么

| 边界 | 必须保留的事实 | 不足以证明等价的做法 |
| --- | --- | --- |
| 指令与 peer 消息 | 原始角色、消息来源、收件线程、内容与生命周期。兼容映射还须检验目标 provider 的实际语义 | 只比较文本字节；把 peer 内容升级成系统指令；增加授权或工作流指示 |
| 工具调用与历史 | 原生 call ID、输出归属、原生执行证据与终态 | 编造一次工具执行来装载上下文；按参数相同合并不同原生调用 |
| Reasoning | 内容、摘要、签名及加密字段的各自来源和可重放条件 | 用摘要补造完整 reasoning；把协议说明当成模型生成的推理 |
| 压缩与恢复 | 原生状态、待处理输入、取消 fence、持久化顺序、同会话冷恢复和已执行副作用 | 仅把字节 413 改名为 token 超限；丢掉旧片段后继续；自动建立无历史新会话 |
| 成功、取消与失败 | 原生最终事件和文件/进程效果，保留中途失败 | 以 HTTP 200、测试进程退出或最后任务成功覆盖中间失败 |
| 平台隔离 | 被测账户的实际权限和已核验进程身份 | 用提权后的 runtime 代验普通用户；按进程名称批量结束任务 |

## 当前独立复核的范围

`codex/native-contract-r1210` 是未晋升候选。原生 `AgentMessage` 是收到的上下文，不是模型自己的 assistant 输出。固定源码的生产路径使用 `to_model_input_item`，历史处理把 AgentMessage 视为输入边界。之前曾按 `to_response_input_item` 的 assistant 序列化推断模型 fallback；实际调用点核查只找到定义与测试，这一判断已纠正。[固定原生输入边界](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/core/src/context_manager/history.rs#L1372)

OpenRouter 明确将请求末尾的 assistant 项用于 prefill。候选因此把真实 AgentMessage 编码成 model API 的 user 输入：首个文本 part 仅保存原生 type/author/recipient 元数据，后续有序 content parts 逐字展开。它不把正文再次包进 JSON 字符串，不按 Message Type 选择工作流，不增加身份、授权或任务指令。这个 wire 角色不会向原生会话追加一个人类用户事件；消息来源、审批、线程和任务生命周期仍由 runtime 决定。[OpenRouter prefill 契约](https://openrouter.ai/docs/api_reference/overview#assistant-prefill)

原生消息 ID 与内部记录仍保存在 runtime 和映射证据中，不被冒充为跨提供方的 response ID。模型输入 AgentMessage 本身不带 other_recipients/trigger_turn，适配器不会猜补。

Peer 验收须绑定转换发生处的源消息 ID、准备请求中的实际位置与摘要、同一接收线程的原生 rollout。普通 user/assistant 的相同文本、其他消息的 ID、重复 JSON 键或另一份请求的来源文件都不能作为投递证明。追踪只证明 native→准备请求的映射；它不证明上游已消费内容。真实 native 进程加确定性模型夹具用于检验机制，这不等于真实 DeepSeek 已理解相同内容或角色优先级。

一份固定 0.7.0 的长历史任务复核显示，当前导出的准备请求保留全部 80 组重复调用/输出，61 条 reasoning 与原生记录字段一致。历史成功请求的完整发送体未保存，不能仅凭后来的导出判定过去模型已经消费内容，也不能直接归咎于模型。0.7.0 与 0.7.2 的 peer/continuation 转换不同，诊断必须绑定实际版本。

官方 stock 0.167.0 已在同一隔离 Windows 中做机制对照。它可执行直接工具与 Code Mode，但已复现字节 413 无同 turn 压缩恢复、插件 Git 辅助进程退场不完整。直接换成 stock 版本会丢掉这些已验证的修复。现有 fork 的其他改动仍需逐项说明其必要性，不能由这两个对照结果一并担保。

DeepSeek 官方 Responses 文档写明其直接服务把 developer 当作 user；OpenRouter 的转换路径需要单独实测。两个路由的有限角色冲突对照中，保留原生 developer 与旧 system 映射均返回预期结果；它没有发现新映射退化，也不能证明任意提示下的优先级等价。严格结构化输出、未完成 assistant 历史与辅助请求仍须分别验证。[DeepSeek Responses 契约](https://api-docs.deepseek.com/api/create-response/)

OpenRouter 以预算拒绝请求时，只能证明计费准入失败。它不证明请求格式兼容或模型能力失败，也不能计作真实模型验收通过。验收不得为通过而自动换 key、模型或 provider。

## 维护与发布

先从上游源码和真实失败定位，再缩小必要改动，最后用同一触发条件复测。新增补丁须列出原生责任边界、改动原因、实际消费者与回退方法。删除补丁须证明能力没有退化；减少补丁文件数量本身不等于减少运行时改动。

回归结果按纯协议测试、正常安装、真实 native 机制、真实模型任务和平台分别记录。原始失败保留。候选不能因为旧版本某项通过就自动获得相同资格，也不能因为测试总数增加就宣布全部原生等价。

[当前发行记录](../reports/releases/0.7.2/README.md) · [使用与升级方案](STABLE_PLAN.zh-CN.md) · [容量恢复边界](request-capacity-recovery.zh-CN.md)
