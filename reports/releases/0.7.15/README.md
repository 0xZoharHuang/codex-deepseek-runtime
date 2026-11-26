# CDS v0.7.15

本版修复在 OpenRouter Morph/fp8 网页搜索或续办中，响应带有 `encrypted_content` 时报 `stream disconnected before completion` 的问题。固定模型、各平台的原生二进制与 Codex 的网页工具调度保持原样。

适配器将完整的成功响应绑定到同一模型、精确路由和凭据，再将原生会话中的加密项还原为原始 Responses 形状。已核验的 Morph 历史继续使用 Morph，即使普通默认是官方 DeepSeek。旧版官方加密回执仍可读取。来源不明、会话中混有不同来源或与固定串行和特殊路由冲突时，会在出站前显示兼容性错误。显式迁移允许按既有规则只投影掉已核验的官方加密项。

回滚到 v0.7.14 后，已经写入 Morph 加密项的会话需在 v0.7.15 恢复。采用全新独立目录验证发布包后，再逐个切换各调用方的入口。保留原会话和原 home，等 active turn 自然退役再变更入口。[安装、配置与回滚](../../../docs/STABLE_PLAN.zh-CN.md#可选的故障切换与线程迁移)
