# 0.6.8 候选：自动工具选择与严格最终 JSON

**状态：开发候选，尚未公开发布。** 本页记录协议修正的目标和验收边界，不是安装版、三平台或整体 runtime 已通过的声明。最终源码提交、可复现 wheel、安装版回归和真实 CLI 结果须在完成后补入；不要把旧 HEAD 或旧版结果当作新候选的验收身份。

## 候选改变什么

严格 JSON Schema 与自动工具选择一起使用时，已观测到上游直接生成格式合规但没有依据的回答，跳过取得真实数据所需的工具。候选保留 tool_choice=auto、全部工具、调用 ID、历史和原生 Codex 工具循环，只在这一已识别的严格 schema 组合上适配上游输出格式。

完整 schema 作为明确的最终输出要求传递；原始请求仍交给既有 StructuredOutput 校验器严格验证。官方路由对明确 object/array 根使用已验证的 JSON 语法模式，其他已明确的路由或 schema 根使用文本映射。回执记录格式策略、原始格式/schema 摘要及验证方式。未知控制项继续报错，不把 auto 强改成 required，也不在请求失败后换模型或提供方。

这是可观察的协议映射，**不承诺上游使用完整 schema 的受约束解码**。最终 JSON 不合格时拒绝发布成功完成；工具的执行、审批、沙箱、线程和恢复仍由原生 runtime 负责，没有增加第二个 agent loop、工具调度器或 synthetic final 工具。

模型仍固定 deepseek/deepseek-v4.1-flash-1017。模型判断质量、模型不具备的模态和 OpenAI 私有云服务/账户权益沿用原有独立边界。

## Python 与原生组件分别固定；共享传输缺口仍须修复

本候选 Python adapter 版本为 **0.6.8**。最初仅评估 Python 协议修正时，安装器允许逐字复用旧原生组件。随后独立审计确认：固定 0.164 源码也有 Guardian V2 连接池忽略 supports_websockets=false 的缺陷，不能靠沿用旧资产关闭它。最终候选必须在同一 fe136 上游基础做最小 transport guard backport，并产生新的原生资格证明。

下表仅保留 **0.6.7 回滚/差异基线** 的归档与 producer 身份，不代表新的 0.6.8 原生资产已构建或通过：

| 平台 | 现有原生归档 SHA256 |
| --- | --- |
| Windows x64 MSVC | 985575c3fd371b31ad13242f8fa8dd3877142f5a2b10dedd2f00a17c84499bd1 |
| Linux x64 GNU | 01f501fc9ef0876449457bd5416b71b43328a2cf1292062bbecf102e99125ac6 |
| macOS arm64 | 452e387648d5b88f036daf6d1be304bc2deeac23763e65ecfdf84b22d5e41b9f |

原生基础仍为 Codex 0.164.0-alpha.7，upstream commit fe136d4e70ef2b5bbed40a2d22b3aabf267796d3，不为修连接池而升级 runtime 版本。当前尚未因本轮修改编译或重包原生文件，不把旧 producer 改名成 0.6.8。native/runtime*.json 暂时保持原 0.6.7 基线；新摘要与 recipe 只能由真实 backport 构建及验收结果填入。

安装器按平台、原生 recipe、归档及完整文件摘要验证，并分别验证当前 Python 产品源文件，不要求这两个组件的发行版本号相等。安装器允许独立组件版本号，不等于已知传输缺陷可以不修，也不等于新 Python 版本已验收。公开发行必须分别注明新 wheel、实际原生构建及原样复用 helper 的身份。

继续遵守 [0.6.7 原始许可证声明补充](../0.6.7/attribution-correction.json)：现有 native 归档和其独立补充包应一并留存或分发，不能通过改版本字段省略声明。

## 发布前仍需完成

- 在同一固定上游回补最小 Guardian V2 transport capability guard，验证 false 不预热 WebSocket 且保留现成 HTTP 评分，true 仍保留原连接池行为；完成三平台实际原生构建/资格，不能禁用 Guardian 或改评分逻辑来通过。
- 固定经审阅的精确源码提交；从干净、已证明的输入产生两份字节一致的 Python wheel。
- 在新的独立 QA 环境安装该 wheel，严格确认产品 import 来自安装目录且与冻结源码逐文件一致；重验 installed-only CI 入口，保留旧环境不匹配导致的拒绝记录。
- 将 Windows 对抗 fixture 所需的既有管理员测试 token 与普通权限的实际 runtime 执行分开。不得通过放宽断言、增加 skip 或改系统防护通过。
- 用独立 CDS 安装/profile 实测默认 Code Mode 和顶层工具搜索两种原生路径；从真实工具取得未知 nonce，再生成严格 JSON。保留实际 sandbox/审批、原始失败、调用 ID 和结果验证，不用外部控制器代做工具工作。
- 按新 adapter 的实际影响完成 Linux/macOS 安装版回归或明确记录尚未通过；不在 Mac 主机上补跑，不把 Windows 结果外推为 Unix 实测。
- 由实际结果更新本页、README 和公开交付记录后再发布。旧复杂任务和原生验收按原版本保留，只对有逐文件来源证明的未变组件明确复用。

本候选不切换日常 CDS 入口，不修改正在运行任务的环境，也不改变 Codex App 的状态或订阅登录。当前 README 安装命令继续指向已发布的 v0.6.7；它们不是候选 0.6.8 的发布承诺。
