# CDS v0.7.14

v0.7.14 为固定模型 `deepseek/deepseek-v4.1-flash-1017` 增加可选的普通路由故障切换。它由操作者逐 home 启用。默认路由故障须由重复的上游 HTTP 错误和 OpenRouter 的公开端点状态共同确认。确认后，新线程可从显式配置的备用 provider 开始，并保留选中的普通路由。每次模型请求仍只发到一个 provider。

已有线程在故障期间沿用原路由。线程没有进行中的模型请求时，可用 `codex-deepseek runtime thread move` 显式切换普通路由。原生会话保留消息、工具调用和结果，官方 DeepSeek 的加密推理仍只能在官方路由上重放。若托管搜索的原始结果只存在于加密推理中，备用 provider 能看到查询和已有回答，但无法读取原始结果。自动切换保留原有的串行工具、严格结构化输出和 peer/continuation 路由策略。显式迁移仅能在原策略支持的官方 DeepSeek 与 Morph 路径间切换托管网页工具续办，并先核验加密推理的来源。

使用 v0.7.14 tag 的源码和 `install.py`，由平台清单选择已固定的 Windows P41 或 Linux/macOS P35 归档。本文的运行行为说明与各平台的实际验证记录可分别核对。[设置、迁移与回滚](../../../docs/STABLE_PLAN.zh-CN.md#可选的故障切换与线程迁移) · [发行及平台证据](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.7.14)
