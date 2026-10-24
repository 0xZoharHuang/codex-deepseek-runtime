# 0.6.0 验收记录

**固定组合的三平台本地 runtime 与公开原生资产路径的托管 CI 验收已通过。** 产品发布状态及公开安装读回回执以对应 GitHub release 为准。 本报告分别记录运行时机制、模型业务结果和发布资格，并保留原始失败。独立 Codex App 网关另行验收，不属于本报告。

## 固定组合

| 项目 | 身份 |
| --- | --- |
| 适配层产品源码 | 2d43ed50050d0a687f55；95 个产品文件 |
| 原生 Codex | 0.164.0-alpha.7；fe136d4e70ef2b5bbed40a2d22b3aabf267796d3 |
| Windows CLI SHA-256 | 739617253b01787c644cb72c704fee290e53b49ba32d6e7ae6d86691744c0e64 |
| Windows 原生归档 | 64c323e8a08337c8cebba77bc16405c5cf4aa3ad251e490df9cf79b9c8e821b7 |
| macOS ARM64 原生归档 | aef450933d716b0c9451d5a487eee59eb346c188c894f8fee1b8155c836052b0 |
| Linux x64 GNU 原生归档 | 3fe87429998f460406d91ca32aae0134ccc1bf5a95da3110735935c17dcb3cd5 |
| 固定模型 | deepseek/deepseek-v4.1-flash-1017 |
| Windows 宿主 | Windows 10 19045；真实流程使用普通交互式账户的 Limited 权限 |

普通请求优先使用官方 deepseek。特定参数组合显式选择同模型的 morph/fp8 或 primeintellect；没有请求失败后的隐式换模型或换提供方。具体条件见 README。

产品代码和原生包已经固定。本轮后续修改集中在验收断言、原失败归档和 CI 资产获取；没有为这些修改重新编译 Rust。每份历史证据保留自己的实际源码、安装和验收脚本身份。

## 最新实际状态

最近完整 Windows 标准运行 R13 已自然结束：**30 项中 28 项通过，OAuth keyring 与 auto 两项失败。** 原始 28/2 结果永久保留，不改写为一次全绿。

| 范围 | 已观察的实际效果 | 验收条件与保留记录 |
| --- | --- | --- |
| 文件、命令、终端、沙箱与撤销 | 工作区允许、外部拒绝、只读读取/拒写、TUI、后台控制、中断、网络权限撤销通过 | 已绑定最终平台组合报告 |
| Guardian、记忆与压缩 | 真实人工/自动审批、准确 action/call/cwd 评分归属、两版记忆的本轮精确目录、原生压缩通过 | 已绑定最终平台组合报告 |
| 主/子/孙线程及目标 | 原生线程关系、等待、跟进、打断、关闭、恢复、队列和目标接口通过 | 已绑定最终平台组合报告 |
| Code Mode 与协作版本 | R13 真实 custom exec 调用及输出通过，legacy-v1 的 spawn/wait/close 完整执行 | 原 R11 模型选用 direct 工具的失败保留 |
| 崩溃恢复 | R13 在当前 reasoning 流中实际终止 owner，子进程退出、在途回执取消、原会话冷恢复成功 | 原旧脚本等待 assistant 文本而未真正崩溃的失败保留 |
| OAuth | R15 四种凭据模式的各 5 项实际断言全部通过；27 个回执 completed/200，8 个真实轮次，0 次反馈补发；冷恢复、跨 home 隔离及注销通过 | R13 的两次非行动和 R14 模型前夹具失败保留，不改写原运行结果 |
| 网页导航 | 8 项通过，含实际 open/find/click、公共 HTTPS、冷引用、模型文件效果、中断后继续和 owner 退出 | 精确复用当前安装及未变化生产者证据 |
| CDS 操作技能 | Unicode、Decimal、重复冲突任务通过；7 个真实请求逐字节消费候选 SKILL 正文 | 不把同名全局技能或输入 flag 误作正文加载证据 |
| Hooks | R14 首次信任、跳过及声明变更三个实际 UI 场景全部通过；正式消费者重新核验 115 个原始文件通过 | 旧目录的 6 个无用 QA junction 导致原汇总被拒，失败保留；新生产者不创建多余 junction |
| 剩余本地方法 | R14 59 项正向方法、4 项后端边界及 1 项平台边界通过，7 组全部正常结束 | Windows 命令控制已按原 30 秒请求预算实际重验；最终汇总保留原生产者快照 |
| 协议恢复 | 6 类无效工具/Schema/终态/超限错误及真实恢复通过 | 不能将错误转成成功或重复派发已有工具 |
| 故障与账户边界 | 14 项官方/候选故障对照、MCP 生命周期及用户验证边界通过；所观察 157 个进程身份已退出，无强制清理 | 不将 providerUnavailable 写成生物认证成功 |
| 原生 Schema 与源码 | 456 份接口定义与同版官方一致；完整源码、声明补丁和原生包清单校验通过 | 结果只适用于固定原生包 |
| 全部 RPC | 基线 174 个客户端方法；Windows 本地 131 个，128 个正向调用及 3 个实际官方对照的平台控制边界；70 个原生会话，0 缺失、0 配对错误 | 最终严格 RPC 与组合报告均通过；三个边界保留实际官方对照 |

两次 OAuth 非行动轮次均正常完成且回执为 HTTP 200，原生没有开始或完成工具动作。工具定义一致，keyring 同配置的首次调用确实成功，冷启动也没有重新交换 token。没有发现适配层丢调用的证据；成功上游 SSE 未保存，而 generation 元数据的 finish_reason 为空，因此不将这些观察写成逐字还原上游的证明。新的验收设计只允许在完整正常结束且没有任何工具/控制/资源副作用时，给同一原生线程一次明确的执行反馈；原轮次保留，有部分操作时不补发，第二次仍未行动仍失败。这属于验收任务，产品的 agent loop 不变。

R14 的 OAuth 失败来自测试夹具以截断覆盖方式写事件文件，控制器在并发读时读到不完整 JSON，发生于模型请求之前。夹具现通过其已有本机 HTTP 服务提供受独立随机 controller token 保护的完整内存快照，磁盘原始证据用原子替换发布。该 token 不进入 MCP 配置或模型上下文。真实并发发布测试也保留了 Windows 文件共享句柄的失败反例。四种 OAuth 必须在同一当前版本重新完成真实调用、冷启动、跨 home 隔离和注销；不会用夹具单测代替。

Windows 命令控制原失败的时间线已复核：首次原生 setup/ACL 准备后约 22.55 秒出现准确 nonce 的受限进程就绪文件，约 26.56 秒正常退出 0。脚本原 15 秒观察窗提前失败，未实际调用 terminate。修复将观察窗与原 30,000 ms 请求预算统一，仍要求命令存活时真正检查控制边界，不提高原请求预算或重试到绿。

## 离线和跨平台结果

最终统一 QA 为 179 个文件，freeze SHA-256 为 `7f7f6a603958c5b1b9c89423fe260c50cf6722dc74cb170fe1c7a0aaa7c9c653`。三个平台各自完整收集 **1,807 项**，无取消选择、无失败、无错误；平台跳过按明确的测试名称与条件逐项核对。产品 95 个文件及原生包不变。

| 最终平台 | 完整离线通过 | 明确平台不适用 | 本地 RPC 实证 | 正式报告 |
| --- | ---: | ---: | --- | --- |
| Windows x64 | 1804 | 3 | 128 项正向、3 项原生配置边界；70 个会话 | [原始报告](../reports/releases/0.6.0/x86_64-pc-windows-msvc/acceptance.json) |
| macOS ARM64 | 1775 | 32 | 129 项正向；64 个会话 | [原始报告](../reports/releases/0.6.0/aarch64-apple-darwin/acceptance.json) |
| Linux x64 GNU | 1776 | 31 | 129 项正向 | [原始报告](../reports/releases/0.6.0/x86_64-unknown-linux-gnu/acceptance.json) |

全部原始请求/响应的重建均无缺失、无配对错误；每个目录的 `proof-index.json` 固定公开报告的原字节 SHA。原 R14、R15、R16 及 R17 早期运行保留自己的身份，不改签为本次结果。

已公开的验收提交 dd832a9fa45ab12ed87cf98942cb2d1c8c34d237 在三个平台分别收集同一 1,547 项：

| 平台 | 通过 | 明确平台不适用 |
| --- | ---: | ---: |
| Windows x64 | 1544 | 3 |
| macOS ARM64 | 1515 | 32 |
| Linux x64 GNU | 1516 | 31 |

[托管运行 40064650715](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40064650715) 在每个平台执行 15 项真实原生传输，完整集合包含大请求、资源关闭、证据遍历及截流前置条件检查。JUnit、测试名称、跳过原因和进程身份逐项核对，无取消选择；warnings-as-errors，未调用真实模型，未重新构建未变化的原生包。

macOS/Linux 已对当前产品另做真实 runtime 复验，不从 v0.5.0 的 129 项报告推定通过。Linux 的 30/30 标准门、七组方法及 129/129 原始 RPC 重建通过；macOS 原 29/30 的唯一失败在四 OAuth 的清理辅助程序，五项业务断言均通过，改为专属 metadata-only 清理后四种 OAuth 已重新实跑通过。两平台的网页八项、协议恢复六类、Hook 三场景、官方/候选 MCP 生命周期、用户验证账户边界、456 份 Schema 和完整源码/包审计均已实际执行并纳入最终严格报告。

复杂真实工程在 Windows 执行。两份 Unix 报告明确使用跨平台共同组件参考：95 个产品文件、28 个适配模块及 185 个保持上游原字节的保护范围源码相同，59 处目标平台差异逐项保留。`executed_target` 为 Windows，`local_task_execution_claimed=false`；这不是 Mac/Linux 重新执行该工程任务。固定参考的[原验收](../reports/releases/0.6.0/references/windows-complex-runtime/acceptance.json)与[原安装](../reports/releases/0.6.0/references/windows-complex-runtime/installation.json)保留原字节。

固定原生输入已发布为独立的 [v0.6.0-native.1 prerelease](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.0-native.1)，它不是完整 CDS 产品发布。原始归档、recipe 和 SHA 清单均保留；Mac 已实际匿名读取 recipe 并核对大小与 SHA。最终提交 b01ce01418170eefae4f1253ff4860fe688a027e 的[托管运行 40087081457](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40087081457)已通过两个目标的实际公开资产下载、15 项原生传输及完整离线集合分区检查。

CI 获取已迁移为固定版本的原生发布资产，避免依赖会过期的 Actions 文件。完整 tar 成员、字节数、SHA-256、原 recipe、当前目标 recipe、源码和构建回执校验全部保留。Windows 使用新的 recipe，macOS/Linux 保留其原 recipe。本次两个托管目标均实际通过该固定 release 资产路径；原生包没有重编译。草稿或资产上传自身仍不构成验收。

macOS 的新版验收清理器调用系统 security 工具，只查询 service=codex、按当前 canonical 测试 home 的 SHA 计算账号名的元数据，再删除该确切条目并确认不存在。它不请求密码值，不修改默认钥匙串或 App/OpenRouter 现有条目；原清理超时及原始 29/1 结果保留。driver 身份、实际前后状态、原生注销冷读、目录与 marker 身份均参与汇总校验。

Linux 的一次 Hook 补验使用了 3 秒的外层收尾观察窗：三个 Hook 场景实际通过，但控制器随后强制清理了 15 个后代。其中 3 个身份可与服务状态准确对应，另外 12 个缺少历史 argv，仍标为未知，不能事后推定归属。独立零模型验证中，适配服务按默认 120 秒空闲策略于约 122 秒自然退出，6 个准确进程身份均结束、无强制清理。

另一项原生 TUI→退出→daemon stop→metadata EOF 对照发现：候选在 155 秒后仍有 4 个插件 Git 下载进程，同版本完整官方包在相同路径下也残留 3 个；两次都保留 natural_all_ended=false，各进行了两次准确内核身份清理，全部记录身份随后结束。插件同步源码与固定上游逐字节相同；没有发现候选新增的生命周期退化，也没有将任一自然清理失败改签为通过。这是固定原生的已知后台下载行为，不承诺关闭 daemon 会立即结束此类下载。详见[实际官方/候选对照](../reports/releases/0.6.0/x86_64-unknown-linux-gnu/native-lifecycle-boundary-comparison.json)。

## 复杂真实工程

下面两个复杂工程均在 Windows 实际执行。Unix 以明确的共同组件参考与各平台本地机制实测组成验收，不宣称在 Unix 重跑了这两个第三方工程。业务补丁由 DeepSeek 经原生 Codex 编写；外部控制器负责准备、观察、明确验收反馈和独立检查，不代写补丁，也不向模型提供隔离 oracle 或上游修复答案。

**Jinja：当前组合业务验收通过。** 模型修复分支重赋值导致输入参数丢失的问题，最终修改 3 个文件。工作区 916 项、隔离验收 908 项全部通过，0 失败/错误/跳过。两套检查均实际导入当前物理工作区；118 个 oracle 源文件、控制输入与工具环境保持原字节。最终轮次真实 open/find/click 两个官方文档页面；新原生 reviewer 的父线程、轮次、完成正文和向父模型回传均已核对。过程中的 6 次命令失败保留。

**HTTPcore：业务验收未通过。** 原 30 分钟运行未收敛并按原生流程中断。一次有界的原会话续跑自然完成，最终工作区 233 项、独立 asyncio/trio 取消 2 项及真实 TLS 检查通过；隔离上游验收仍为 205 通过、8 失败，3 轮真实 TCP 检查失败。原模型补丁、全部 JUnit、输出与两次结果均保留。

HTTPcore 已实际使用 Windows 命令、文件修改、原生冷恢复、reviewer 和当前轮次网页导航。运行时机制另由原始协议与文件效果重建，不能由模型宣称完成来判定，也不能把剩余第三方代码回归改写成成功。两个任务的正式 runtime 重建已经完成，各自 14 项机制通过。Windows 物理 workspace 映射和仅初始化的控制连接已正确分类；初始化连接不算任务生命周期成功。HTTPcore 的业务结果仍明确为失败，不能用 runtime 通过覆盖它。

**Terminal-Bench：较早候选的补充证据。** [固定 mvcc-lsm-compaction 原题](https://github.com/harbor-framework/terminal-bench/tree/452bf305c6daa62fc59061d22133a7cbc7c1572e/tasks/mvcc-lsm-compaction) 在 Linux 候选 f3699ef1f89574698b2b 上由 DeepSeek 修改 4 个源码文件，基线 12 失败/3 通过，独立未改动 oracle 最终 15 项全部通过。该任务没有子智能体，不作为协作证据；不是官方 Harbor 或排行榜成绩，也不改写为当前候选的实跑。

## 修复原则和保留的失败

实际产品修复包括 Windows 必要 cwd/只读文件/daemon 程序包的原生权限准备、Git 和 ACL helper 的句柄及进程生命周期、已有 DACL 锁协调，以及本地大 JSON/raw 请求的库支持流式发送和明确关闭。它们沿用原生执行、权限和调度机制，没有另建 agent loop、审批器或子智能体系统。

验收修复包括：记忆证据只检查当前精确目录、递归前拒绝联接逃逸、SQLite 连接显式关闭、TCP 截流前必须收到当前原生 PARTIAL 事件、审查结果与调用 ID 准确对应、JUnit 证明 asyncio/trio 实际执行，以及续跑同时归档原始 XML。原来被错误计为通过的记忆证据已经撤回，不能由后续成功抹掉。

此前版本、失败及修复过程保留在[历史验收记录](https://github.com/0xZoharHuang/codex-deepseek-runtime/blob/dd832a9fa45ab12ed87cf98942cb2d1c8c34d237/docs/release-0.6.0-acceptance.zh-CN.md)和私有原始证据库。公开报告不包含 key、完整模型私人上下文或私人 home 内容。

## 范围和发布门禁

等价基准是固定 Codex 在目标系统支持的本地 runtime。模型代码质量、未具备的模态能力、OpenAI 云端服务及账户权益单独列明。Windows 10 不冒充 Windows 11 专有沙箱能力。固定 Windows restricted-token 的 command/exec/write、resize、terminate 为原生配置限制：同版官方和候选均在真实 unelevated 受限命令仍在运行时返回相同 -32600，原命令随后正常退出 0；实际 token 受限且非管理员、工作区写允许、区外写拒绝。固定源码证明该 guard 不依赖 elevated level。elevated 的既有记录保留其原范围，不冒称该模式三项都做了同样实跑。原生工具的终端、后台进程、权限和终止仍另有实际验证。

MCP 的 turn/interrupt 与 owner exit 分开记录：本基线某些 HTTP 请求在 owner 退出后关闭，不能写成中断立即关闭。101 图片约 34 MB 的真实传输另有原字节/顺序/detail 与远端清理证明；模型将 B 识别为 8 的视觉失败仍保留，传输正确不代表识别正确。

三平台严格组合报告、当前完整离线与真实 runtime 复验均已完成。Windows 最后一次汇总曾因 SSH 观察环境缺少 Git PATH 失败；仅补齐该消费者子进程的 PATH 后，原始模型与业务材料按同一断言重建通过，没有重跑业务来掩盖失败。macOS 旧清理超时导致日志缺结尾标记，仅对固定失败材料按原断言、唯一 nonce 归属及七个原文件 SHA 定位并排除旧根；新的四种 OAuth 仍必须具备完整结尾标记与所有正向证据。

实际公开原生资产路径的托管 CI 已通过。对应产品 release 附件单独记录公开安装读回、检查过的入口与文件清单；发布可用性以 release 为准。组合验收保留原始失败，不宣称一次全绿。Windows 整理放在 CDS 与独立 App 项目全部完成之后。

根据用户后续授权，必要的 macOS 实测在新建的独立安装、home 和测试目录中完成；现有 Mac App、订阅登录、上游凭据及正在使用的 CDS 安装没有被读取或替换。模型凭据从 Windows 经父进程内存与 SSH 标准输入一次性供给测试子进程，不写文件或命令参数。OAuth 仅创建和清理标记明确的本次测试条目。GitHub 发布继续使用已授权的认证中继，Windows 上其他人的固定安装与进程不接管。
