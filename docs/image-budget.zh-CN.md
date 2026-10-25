# 离线图片预算诊断

~~~sh
codex-deepseek runtime image-budget --input-kind normalized-responses-json --request normalized-request.json --policy-json image-transport.json
~~~

也可以显式使用 --profile-home <CDS独立home>，只读其中
provider-state/image-transport.json；它与 --policy-json 二选一。
命令不猜默认 home，不扫描会话，不启动原生 CLI、网关、凭据存储或 Files API。
--request - 从标准输入读取；文件和标准输入均有 96 MiB 的 serialized-byte 上界，
策略 JSON 上界为 16 KiB。输出只包含聚合诊断，输入错误也不回显文件名或 JSON 内容。

**输入必须是同一 CDS 版本实际 protocol.prepare 后、图片转换前的完整 Responses JSON。**
它不是原生 rollout、原生未转换请求或聊天记录。命令不会调用 protocol.prepare
重新猜测状态；工具发现、搜索证据与历史路由依赖真实运行时 Store，不能靠裸 JSON 重建。
拥有者应在自己的环境中提供已保存的准确请求，不向其他人发送原图或业务正文。
严格解析拒绝重复字段、非有限数值、未知顶层控制、未转换的原生工具类型或不支持的模态。
已接受字段的内容和顺序不被改写。该输入校验属于离线诊断契约，不替换运行时校验器。

输出中的 serialized_input_bytes 是输入文件的字节数，budget.before_bytes 是现有
encode_body 的 compact UTF-8 编码大小，二者可能因空白不同。
estimated_min_bytes 是**当前 occurrence 策略及现有 selector 下**，使用 32 字符
file ID 占位符得到的最小估算，不是实际上传后的 wire bytes。
重复图的每次引用各占一个 slot；planned_unique_images 仅表示选中图中的唯一原始字节数量，
不表示新上传数，实际缓存复用与新上传保持未知。明确的 low detail 不会转换为 Files 引用。
当前最大 16 次引用依据受控 Responses 实测：同一文件 ID 重复 16 次被接受，17 次返回明确的 16-reference-limit 错误；并非只有上传不同文件才计数。
[原始结果的脱敏摘要](../reports/payload-capacity/file-reference-boundary-20261025.json)同时记录固定 DeepSeek、官方 DeepSeek 路由及生成元数据回读。
[官方 Files API 文档](https://openrouter.ai/docs/guides/features/files-api)仍将该 API 标为 beta，未把此请求引用数写成长期合同；升级或上游行为改变时需重新验证。

小于等于 activation threshold 时沿用原 prepare 的提前返回，不扫描图或校验引用数量；
因此 image_scan_performed=false，对应计数为 null，不能把它当作“没有图片”。
没有显式 wire budget 时状态为 no_wire_budget_configured，不承诺上游一定接收。
所有状态均不代表上游接受、模型成功或原生任务完成。返回码 0 表示按显式策略完成可行性估算
或沿用提前返回；返回码 2 表示估算不可行、已有引用超限或输入无效，具体看 JSON 状态。

运行时与诊断命令共用一个纯规划函数，转换选择、字节、顺序、detail、模型与路由保持原样。
新的本地预算失败在 adapter.sqlite 的 receipt detail 与图片 request-receipts.sqlite 中保存
image_budget / budget_failure 聚合字段，不记录 URL、正文、原图、图片哈希或 file ID。
预上传失败的 phase=pre_upload；实际 file ID 使预算超限时为 post_upload，
并记录 actual_wire_bytes。此时上传资源仍受原有 pin/cleanup 机制管理。
旧回执没有这些字段，不会追补或伪造。

预算错误不保证一次 compact 就能恢复。先看准确请求与策略的聚合结果，再由原生恢复机制
处理历史容量；此命令不丢图、不缩放、不改模态、不替换提供方，也不执行 compaction。
本地网关的既有请求路径仍可能先读取凭据；只有本离线命令保证不读取凭据。
