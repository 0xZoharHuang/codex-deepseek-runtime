# 官方 DeepSeek 与兼容提供方的历史证据

CDS 区分固定模型、实际服务提供方和原生工具行为。当前默认普通路由仍为 `deepseek`，显式兼容路由按对应请求与提供方记录。以下是各历史版本的验收范围。它们不构成 v0.7.1 对相同业务的重新验收。 [路由配置与回退规则](../../../docs/provider-routing.zh-CN.md)

## 官方 DeepSeek 的实际覆盖

- **v0.6.5，Windows Code Mode。** 12 次真实请求均通过 generation 回读确认由 DeepSeek 服务。记录了 11 次原生 `exec`、1 次 ToolSearch 和 1 次 MCP 摘要调用。只读 PowerShell 取得预期 nonce，文件 SHA 保持一致。原记录保留 3 次命令失败及 3 次 Code Mode JSON 解析失败，本轮没有触发 `wait`。 [真实工具回执](../0.6.5/windows-model-code-mode-toolsearch.json)
- **v0.6.10，既有长历史恢复。** 接收方在原 UUID 恢复，318,538,647 字节本地历史前缀的散列保持一致。技术回执记录 `compacted`，报告项数为 11，随后 12 次普通 `official_default/deepseek` 请求完成。历史文件大小与 HTTP 请求体大小分别计量。较早的 `--version` 40 秒超时反馈仍保留。 [历史恢复资格索引](../0.6.10/qualification-index.json) · [原始范围与失败边界](../../../docs/candidate-acceptance.zh-CN.md)
- **v0.6.1，合成图片请求。** 两次固定角色、detail 和 Files 路径的合成请求实际获得 DeepSeek 完成回执。证据范围是这两个请求组合。 [实际服务商回读](../0.6.1/provider/generation-followup.json)

## 失败记录与显式兼容路由

- **v0.6.9。** 官方路由的真实 nonce 任务与同一小请求的普通和原生对照均返回 HTTP 502，保留 30 条失败回执。显式切换至 Morph 后，Doctor、工具读取、Guardian 和同 UUID 冷恢复取得各自的通过回执。 [原始失败及后续范围](../../../docs/release-0.6.9.zh-CN.md)
- **v0.6.14，Windows Jinja。** 未完成的原父任务已有 52 次官方请求完成。通过验收的父任务含 17 次 DeepSeek 和 5 次 Morph，6 次冷续接和 40 次 reviewer 请求均走 Morph。另有两条 Morph HTTP 200 截断流保留，原记录中这两条流没有工具调用。 [任务、路由与失败回执](../0.6.14/model-workflow-qualification.json)
- **长图片业务。** v0.6.10 的 96 图工程验收显式使用普通和续接 Morph，另保留 Prime 22 图续接 HTTP 400。v0.7.0 四组各 16 图业务也显式使用 `morph/fp8`，四条抽查均回读 Morph，关闭回退。v0.7.1 的 P41 验收范围另列。 [96 图及 Prime 边界](../../../docs/provider-routing.zh-CN.md) · [四组图片业务回执](../0.7.0/real-four-image-task-qualification.json) · [v0.7.1 验收](README.md)

既有 49 图请求的 HTTP 400 尚无确定归因。固定官方路由的合成对照和完整 Morph 工程回执各自保留其实际范围。 [图片失败与对照边界](../../../docs/image-content-boundaries.md)
