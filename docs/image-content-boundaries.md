# 0.6.1 候选的图片内容边界

这一轮只修协议与图片编码的语义边界，复用固定的原生 Codex runtime。它不重写工具执行、审批、线程、会话或子智能体。

## detail 与 Files 引用

DeepSeek 的 Files 图片引用会忽略 detail，而 inline 的 low 有明确的下采样语义。因此，显式 low 图片保持原位置的 inline image_url、原始 bytes 和 detail，不替换为 file_id；同一张图片的另一个 high/original/auto 出现位置仍可独立选择 Files。是否转换按出现位置判断，不能仅按图片 digest 批量替换。

其余图像仍按固定的参考数量与硬 wire budget 选择无重编码的混合表示。无法在保留低 detail 语义后满足预算时，明确返回预算错误，交给原生流程压缩或由用户调整操作；不偷偷缩放、删图、挪角色、换模型或失败后更换提供方。wire_budget 不得低于 activate threshold 的既有构造校验保持不变，没有另加一个并不存在的合法策略修复。

文档依据：[DeepSeek vision](https://api-docs.deepseek.com/guides/vision/)。

## 图片所在的角色与工具结果

当前 DeepSeek Responses 允许 user、developer 消息包含图片，也允许 function_call_output / custom_tool_call_output 的 typed output 包含图片；system 和 assistant 消息不允许 input_image。

带图片的 developer 消息保留整条原 developer 角色和图文数组，不转成 system、不给图片另造 user 消息，也不将图片字符串化。既有纯文本 developer 的 system 映射保持原有行为。这是局部图片兼容处理，不扩大本次纯文本策略变更范围。

system/assistant 历史图片在协议入口明确失败；错误的 provider assistant 图片输出也明确失败。有效 function/custom 工具结果继续保留 call_id、图文顺序、detail 和原始图片 bytes，仅对符合条件的图片出现位置使用 Files 编码。

文档依据：[DeepSeek Responses](https://api-docs.deepseek.com/guides/responses_api/) 与 [Create Response](https://api-docs.deepseek.com/api/create-response/)。

## 验证范围与尚未归因的 400

私有 2d 基线先产生六个真实反例失败；修复后十个定向检查、153 个协议/peer/图片相关离线检查通过，覆盖真实 prepare → transport → wire 的角色、call-ID、顺序、detail、原 bytes 回还和固定 provider/fallback 设置。第一轮 QA 类型拼写及预算假设错误单独保留，不计为产品缺陷。

这些事实不能证明某次 49 图业务请求的上游 400 已被归因或解决。固定官方路由的合成 user/tool、inline/Files 对照成功，也不能抹掉原始 400；必须分别保存其请求结构、路由和真实错误边界。没有凭空将 400 当作 token 超窗或 typed tool 图片普遍不支持。

0.6.0 的报告、归档与资产链接仍对应原来的冻结源码和原生版本。0.6.1 需要记录自己的源码身份与验收结果，不能自动继承旧 source 的全平台、完整任务或 App GUI 结论。本轮不重跑付费 30 任务，不将离线回归写成新的 App 验收。
