# CDS 0.7.5 失败通道安全维护候选

本候选基于已发布1d/0.7.4，未重写原生运行时、审批器、线程、工具或重试。P35/P41归档、SHA、recipe、工具链及原生版本不变，三个平台清单仅更新逻辑distribution_version。

- HTTP非2xx在原receipt.detail补版本化安全摘要：只接受固定白名单machine code/type与静态解析状态；不写message、request、图片、header ID或原文片段。不改变过去已丢失的错误记录，也不声称可恢复那次400原因。
- 一个纯stdlib模块error_privacy.py统一供CLI与独立App网关复用。它不导入network/process/provider/runtime/state。普通已检查无敏错误字节保持；已知credential的literal、JSON Unicode/有界嵌套JSON、标准URL/HTML等价及有界Base64内容进行语义保护，JSON keys碰撞不覆盖其他字段。
- HTTP错误交付和显式error trace使用同一安全body；正常header/retry advice保留，已知敏感或无法检查的header不转发。generation header仅是候选，记录前检查已知gen- framing中的可逆credential，正常候选及消费租约规则不变。
- 失败SSE/nonstream/flat error和捕获credential后的异常详情使用同一纯机制；正常模型成功内容不做新增语义扫描。无法安全检查的失败使用非空、明确说明的failure-shaped fallback，保持原HTTP status，并保留可安全提取的machine code，不伪称原正文。

诊断JSON解析限32KiB；HTTP隐私体检查限96KiB；结构/有界嵌套编码及失败对象work budget分别约束。它们不表示原有upstream.read已改为有界流。超限/坏JSON等安全拒绝可能不保留全部自由正文，说明明确可见；不承诺未知任意编码或所有上游格式皆可无损识别。

修复前独立本地红灯、所有中间失败均保留。候选共享代码在Mac本地实际HTTP helper/loopback目标集80通过；独立Windows网关overlay有14安全、26语义和3边界通过，均非真实模型或新安装身份资格，不能相加当能力数量。新纯模块有AST边界与动态禁止socket/subprocess验证。

实际P35 HTTP400 context控制：已发布1d无敏响应与安全候选6f的served及native实际所见错误body三方SHA相同，原400/一次请求/无工具和compact不变。原先期待专用ContextWindowExceeded的严格断言失败保留；固定fe136对HTTP400通用映射，而专用context code在SSE response.failed映射，不能改分类或断言伪造通过。

真实P35每次仍有原生under-development和Code Mode metadata警告。产品catalog未选tool_mode，正常开启相应feature时亦会出现；不可仅为消警告强制model selector覆盖用户mode。该条件未在本版更改，相关原始警告不称为零错误。

最终0.7.5源码仍需对应平台正常安装/CI与相关实际验收；本说明不替代发布门槛，也不为独立CADSG/App代验。已运行的不可变任务不自动切换。未在本页宣称正式交付或真实上游曾回显/泄露credential；全部安全复现只用合成值与本地后端。
