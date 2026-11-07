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
estimated_plan_bytes 是该无损方案使用 32 字符 file ID 占位符得到的请求字节估算。
只有证明它达到最小值时，才同时填入 estimated_min_bytes。可行方案一旦达到配置的
wire budget 就停止追加转换。组合搜索同时检查 wire、引用位置和引用文件原始字节，
搜索到达工作上限时返回 selection_work_limit_exceeded，容量可行性仍未知。

重复图片的每次引用各占一个位置，也分别累计其原始字节。相同图片的上传仍按内容去重。
planned_unique_images 仅表示选中图中的唯一原始字节数量，实际缓存复用与新上传保持未知。
明确的 low detail 保持内联。已有引用需要可信 metadata。离线命令无法读取现有 file ID
的大小，因此报告 existing_file_metadata_required。实际运行先取 metadata，再决定是否
上传，属于本缓存的现有引用在 GET 与 pin 建立期间持有单图操作锁。

当前本地策略默认 16 次引用，并限制配置为 0–16；依据是 2026-10-24 对固定模型、官方 DeepSeek 路由的受控 Responses 实测：同一文件 ID 重复 16 次被接受，17 次返回明确的 16-reference-limit 错误，并非只有上传不同文件才计数。这是当时服务端的观测结果，不是所有提供方、后续服务版本或请求形态的永久上限。
[原始结果的脱敏摘要](../reports/payload-capacity/file-reference-boundary-20261025.json)同时记录固定 DeepSeek、官方 DeepSeek 路由及生成元数据回读。
[官方 Files API 文档](https://openrouter.ai/docs/guides/features/files-api)仍将该 API 标为 beta，未把此请求引用数写成长期合同；升级或上游行为改变时需重新验证。 2026-11-03 的[官方 OpenAPI](https://openrouter.ai/openapi.json)中，`input_image.file_id` 及 Responses 输入数组没有全请求 16 次的 schema 上限；schema 合法不等于实际服务准入，应以隔离、低字节的多引用请求重新区分引用数量限制与服务端展开后的字节限制。

引用转换只减少此次发送的 JSON 字节，不扩大模型上下文或上游展开图片后的容量。图片历史持续累积时，可能先于 token 自动压缩触及 wire budget；12 或 16 引用的一次通过不能证明长任务稳定。容量恢复必须走[原生已确认前缀压缩与冷恢复机制](request-capacity-recovery.zh-CN.md)，保留尚未确认的完整后缀，并分别验收压缩终态及后续普通 turn；不能靠删图、缩图或网关生成摘要通过。

小于等于 activation threshold 时不解析或转换内联图片，仍会核算已有引用。
image_scan_performed=false 指内联图片未扫描，已有引用位置的计数可以非空。
没有显式 wire budget 时沿用按节省量排序的转换方案，并约束引用原始字节与位置数。
返回码 0 表示得到估算结果。返回码 2 表示已确定不符合容量限制或输入无效。
返回码 3 表示现有引用大小未获验证，或组合搜索达到工作上限，不能判定可行性。

实际发送前还会复查序列化字节与引用原始字节。2026-11-06 的固定 DeepSeek / Morph
Responses 探针使用同像素、合法 PNG，确认 20,971,520 字节被受理，增加 1 字节返回
HTTP 413。重复引用按位置累加，与工作区上传单文件 100 MiB 上限分别适用。
[OpenRouter Files API 文档](https://openrouter.ai/docs/guides/features/files-api#upload-limits)

运行时与诊断命令共用一个纯规划函数，转换选择、字节、顺序、detail、模型与路由保持原样。
新的本地预算失败在 adapter.sqlite 的 receipt detail 与图片 request-receipts.sqlite 中保存
image_budget / budget_failure 聚合字段，不记录 URL、正文、原图、图片哈希或 file ID。
预上传失败的 phase=pre_upload；实际 file ID 使预算超限时为 post_upload，
并记录 actual_wire_bytes。此时上传资源仍受原有 pin/cleanup 机制管理。
旧回执没有这些字段，不会追补或伪造。

预算错误不保证一次 compact 就能恢复。先看准确请求与策略的聚合结果，再由原生恢复机制
处理历史容量；此命令不丢图、不缩放、不改模态、不替换提供方，也不执行 compaction。
本地网关的既有请求路径仍可能先读取凭据；只有本离线命令保证不读取凭据。
