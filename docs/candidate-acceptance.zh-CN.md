# 私有候选验收矩阵与稳定方案边界

状态快照：2026-11-04。**Linux CDS 0.6.10 已正式发布**，公开下载、正常安装和真实96图长任务均有回执；Windows/macOS 当前公开版本仍为 0.6.9，CADSG 为 0.1.0 / P7。本页保留历史失败与后续候选边界。Windows P36、macOS新资格和CADSG继续独立验收。

## 原生职责与本次修复

```text
DeepSeek 模型事件
       |
       v
CDS / CADSG 协议网关
       |
       v
原生 Codex runtime
       +-- 工具、审批、沙箱
       +-- 主任务、子智能体、队列、目标、记忆
       +-- 会话、压缩、落盘与恢复

原生压缩器读取超大历史时：
原始已确认历史 --> 只读分片 --> 新摘要连续携带 --> 全部读取完成
                                                      |
                                                      v
                                         原生 history/window 一次提交
```

网关负责协议、认证和数据传输。候选继续使用原生 agent loop、工具执行、审批与子智能体调度。CDS 固定 fe136 基线的外层控制器仍为 `f60826f32040ac7f95a5cf351767d58fe3fb7cfc09c90c873bba484b175cfc44`；CADSG 的 0.165 基线单独验证。

历史工具的一次返回可能包含多张图片；单个历史文本 part 也可能大于上游请求容量。压缩输入现在可按完整来源绑定的 typed part 或 UTF-8 文本范围读取，保持原始数据、次序和元数据。全部片段取得新的成功摘要，并验证摘要连续携带后，才沿既有 owned commit 提交一次。未确认的当前输入、pending 工具结果和来源不明项保持保护；当前 turn 的例外只限原生普通请求已经确认、且与同一快照匹配的前缀。

失败后的下一次投影使用原生请求构造器比较大小；若没有缩小请求，就保留原错误。成功摘要后的后续片段属于同一覆盖事务，不被误当成同一请求的 HTTP 重放。取消、上游失败、来源变化或覆盖不完整都不能安装半截摘要。

Windows 后台普通用户启动失败已定位到不同登录令牌对非交互 Service window station 的访问。最小修复位于原生 desktop bootstrap：只向本次 caller 所有的对象增量授予目标账号所需的 `0x20062`，保留其他权限规则，通过现有受保护文件锁协调跨进程修改；不切换进程 station，不接管交互桌面。原文件与网络权限配置继续由原生沙箱执行。

原生 100 ms grace 仍允许取消请求之后、`TurnAborted`/ACK 之前完成已接受的提交。“取消请求后绝无 Compacted”不是上游保证。详见[取消与提交说明](request-capacity-recovery.zh-CN.md)。

## 当前证据与待验收范围

历史失败行保留其当时结果；不能把它们误读为下方 P35 已修复版本的当前状态。发布仍按各平台与真实任务分别晋升。

| 层级 | 实际取得的证据 | 尚待关闭 |
| --- | --- | --- |
| P31 多图历史及取消机制 | 原 44 项加新 16 项，实际 60 通过、0 失败/忽略/跳过；原生执行 624.20 秒，名称逐项匹配，产物首尾 SHA 相同 | 该机制测试采用调用级 core opt0，不能替代生产 CLI 或真实模型业务 |
| 历史 P31 GNU 生产候选 | 正常 release opt3 构建、完整组件与许可证收集、正常 `--no-activate` 安装通过；原 public8 和 strict 两次冷恢复通过 | 仅为限定私有机械候选；原 native14 补跑发现 7 失败，候选已暂停准入 |
| 上述两次冷恢复 | 旧 P27 实际 orphan/CP6 对照仍失败；新进程在相同 UUID、固定 6 图预算下恢复，保护当前 pending7，第二次冷恢复覆盖全14及 fresh7；ordinary 请求、epoch、工具不重跑、原图不变与 EOF 均通过 | 使用 loopback 模拟服务，不能称为真实 DeepSeek 原业务恢复或全部容量场景完成 |
| P33/P34 历史文本机制 | 512 KiB 原文/256 KiB 请求预算复现通过；旧负例强化拒绝条件后，完整原生64项实际64通过、0失败/忽略/跳过，716.81秒，正式CI校验器独立解析通过 | 原63/1失败保留；本层不代表原native14、真实DeepSeek或App通过 |
| 历史 P34 正常 GNU 安装的原 native14 | 原数据、固定262144字节预算、原断言、正常installed来源及产物SHA均保持；实际11通过、3失败、0跳过，211.18秒且无生命周期错误 | compact-none 旧断言误将请求容量恢复要求成 suffix；根据原始完整来源覆盖修正为严格 oldest-unread prefix 后，原数据单项实际通过，原11/3记录保留。两条cold local-reset仍须修复复测：虽最终推理成功，但原要求零摘要而实际各16次摘要，不能据最终成功关闭。继续阻断发行 |
| P34 源码与配方 | 严格配方、三目标源码映射及相关 source 检查通过；P34 必要 source gate 实际 284 通过、0 跳过，含缓存官方源码的 9 组补丁栈重放 | 这些检查不计作 native 64、原 native14 或平台实际运行通过 |
| P35 本地重置与历史片段续处理 | 真实新3项全部通过、0失败/忽略/跳过，106.34秒；571622字节/7图普通413→本地重置后52727字节/7图普通413→原生新摘要完整读取3+4原图→51830字节/0图普通200；当前输入、真实落盘、epoch/fence/window与前后钩子各2次通过。停止钩子及零模型请求重置两项同时通过；529项source gate通过 | 原64已以同一固定测试程序单独实际64/0/0通过（716.46秒）；新增3项与原64为两批，不伪称一次67项。原3项的hook fixture失败和P34原11/3全部保留；真实96/reviewer已闭环，详细范围另列 |
| P35 正常 GNU 安装 | 正常 release opt3 CLI、完整 native/许可证包、正常 no-activate 安装通过；最终c6e native14实际14/0失败/0跳过，208.87秒；新CLI public8无错误/无摘要安装，strict两次冷恢复同UUID、固定6预算覆盖14图/fresh7/current/fence/once/自然EOF均通过 | public8为2个公开receive-window与6个early-cancel，不称8个精确token窗口；mechanical loopback不替代真实96/reviewer。原生代码/来源与原失败仍绑定各自产物；真实DeepSeek96图后继现已完成，不把loopback当替代 |
| Linux 0.6.10 显式 peer policy | 五个纯Python产品文件增加schema1可选peer选择，原默认payload/registry/digest保持；普通安装追加新不可变release并原样复用P35 native目录。32模块来源/SHA首尾一致，297相关installed回归全过、零skip，原HTTP helper | 不是重编Rust或重复64/14，也不借Windows140算Linux通过；Prime受限文本payload/generation有独立回执，但22图续接400仍保留；真实长图片组合为普通Morph+peerMorph。既有原生只读child的新最终review turn已实跑，未以probe替代。公开交付状态以外部发行回执为准 |
| Windows provider 冷启动端口冲突 | 真实复现旧端口 bind 10048 后 unbound getsockname 10022；显式绑定状态修复后，4项真实socket检查、27项已安装service相关回归，以及正常supervisor/worker的端口冲突完整恢复单项通过，未发模型请求或操作占用端口的其他应用 | 后续正常Windows入口已含端口修复，公共CLI四项见下一行；活动native会话仍禁止换稳定端口。初始10048/10022与修复前失败保留，原冻结构建不倒改来源 |
| Windows 自动授权 | 普通账号真实 Session0 原生 SDK：fresh 零目标 ACE、cache hit 重新授权、真实读允许/写拒绝、幂等、跨 home 并发、预有权限及拒绝路径通过；1 项明确执行、0 ignored，1.31 秒 | 正常完整包与2172普通账号安装、后续Admin正常安装（83.69秒）已完成；同正常入口公共CLI readonly、workspace-write、Ctrl-C、有数据stdin→严格EOF四项均实际通过。先前IPC就绪前runner退出1与high-admin --user的Win5原失败单独保留，不倒改。这些公共CLI结果来自其已核定的Session0上下文。最新原old-home在Session3/可见WinSta0的unified_exec仍有真实IPC失败，r338已停止自己的turn并定位；不能用fresh public4替代该old-home路径，也不能反向抹掉fresh通过。Windows96/全部接口及App仍未完成；权限与ACL标准保持 |
| Linux原96图/Jinja实际长任务 | 全96原PNG与严格字段/summary匹配且原图不变；912/908独立套件均0失败/错误/skip；6次自然压缩，66个唯一大图片请求完成链严格join（62普通/4压缩），cold4/8通过。既有readonly child新最终review turn、独立新事务真实RPC interrupt与worker退出、两ledger各一次均闭环；173toolcall审计未见gold/controller/credential目标；5个实际generation GET为固定模型/Morph | 经多次受控继续完成，不称单次连续75分钟，原phase预算未延长。R443缺5同轮witness等失败与补读、原EOF失败事务、429/400/502/延迟GET及provider CAS全部保留。已知完成pin0/未知上传0；3消费未知calls的36pin正常reconcile后仍保护，不强清。Windows原UUID96续跑不借Linux通过 |
| P2 实际业务 | 接收方已在原UUID实际恢复：318,538,647字节旧历史前缀hash保持，技术回执出现compacted计11项，后续12个普通请求DeepSeek completed，official_default/deepseek；Root独立读回技术JSON | 不是318MB HTTP请求，也不据此称全工具/业务完成。早先wrapper --version 40秒超时原反馈保留；own QA对照未复现、宿主块缓存未排除，不能用温缓存对照替代接收方实际反馈闭环 |
| CADSG 后续候选 | P9 及后续增量只按 0.165 自身源码准备和比对 | 新组合尚未完成独立编译、真实 App GUI、全部本地机制与复杂工作流验收；CDS 结果不能移作 App 通过 |
| 发行与平台 | 既有 0.6.9 / 0.1.0 历史资格按原范围保留 | Linux正常候选产物、0.6.10正常安装与本地回归已有；Linux真实96现已闭环；最终公开下载/tag安装由外部真实发行回执确认，独立于Windows/App先发。Windows/macOS未取得的新资格不混标ready；旧0.6.9 tag/资产不移不改，CADSG独立 |

原始失败保留。编译包装器的一小时截止曾终止仍在优化的 Windows Rust 编译；最终构建采用项目正常编译预算，生产优化和资源、来源检查不变。该调整不修改运行时40秒、原生测试或业务任务时限。

## 能力边界与阻断项

新检查点只证明已消费输入，不是另一套会话数据库。历史归属不明、无完成证明、半截响应和未确认输入不会借摘要或 token 估算被删除。

Windows Legacy service/RegisteredCore 的隔离互操作检查同时涉及 CDS 的 0.164 补丁与 CADSG 的 0.165 补丁，不能只记为 App 待办，也不能归为云端权益或仅凭 fallback 宣布完整等价。须对固定 Windows 10 宿主的原生实际支持路径逐项取证：宿主本来不可用的能力单列，适配关闭的可用接口继续阻断对应平台的完整等价结论。当前优先关闭其他线程实际遇到的容量和后台启动故障；这项检查不阻塞独立 Linux 发行。

模型推理/视觉质量、模型不具备的模态能力、OpenAI 私有云账户与专属数据服务沿既定范围单列。工具、权限、记忆、队列、目标、会话、网页接线和真实错误传播继续按本地合同验收，不能一并排除。

## 稳定方案晋升

已有Windows/macOS用户继续使用公开v0.6.9的匹配源码与资产；Linux按[逐平台稳定方案](STABLE_PLAN.zh-CN.md)及[0.6.10索引](../reports/releases/0.6.10/README.md)查看候选与交付状态。新候选须绑定最终源码、配方、编译产物和实际运行证据；完成对应全部门槛后，才更新安装、升级、回滚与发行说明。私有候选、源码检查和历史组件通过不构成整体发布资格。

原始回执和临时机器路径保存在维护者私有 evidence 中。公开文档不复制会话正文、凭据、完整历史或这些临时环境。历史组件来源见[容量源记录](payload-capacity-source.md)。
