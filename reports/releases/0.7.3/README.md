# CDS v0.7.3 维护更新与验收边界

0.7.3 修复其他 agent 调用 CDS、原生子任务交接和长任务恢复时暴露的协议与启动问题。关键运行机制仍由固定版本 Codex CLI 负责；适配层不执行工具，不另写审批器、agent loop、线程管理或压缩器。本页记录修复范围、发布条件和 2026-11-11 的原身份验收快照。最终 0.7.3 的安装、精确提交 CI、各平台开放状态与证据附件以 [GitHub Releases](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases) 的本版说明为准；未开放的平台继续使用 0.7.2。

## 实际问题与最小修复

| 实际问题 | 处理方式 | 责任边界 |
| --- | --- | --- |
| 原生收到的 `AgentMessage` 与继承历史并不足以让后端确定当前执行者；父级上下文可能被误当成当前身份或新授权。 | 保留原生 incoming-agent-message 事件与消息归属，以当前请求的原生元数据表达 agent 名和历史初始化方式；校验身份、body/header 投影和冲突，拒绝不可表达的 peer 身份。 | 提供当前事实，不重写原历史、不虚构授权、不替原生分配任务。普通文本/JSON 中的类似字段不会冒充原生事件。 |
| 网页 MCP 启动时加载网页/PDF 依赖，可能拖延原生握手并触发启动超时。 | MCP lifespan 先建立轻量对象，在第一次真实 open/find/click 时才创建浏览器并载入工具依赖；退出时仍关闭已创建实例。 | 使用原生 MCP 调度与退出机制，保留实际网页工具语义，不靠增加超时或跳过握手。 |
| 带代理环境中，部分原生 HTTP 客户端路径把认证的本机 provider 连接送往外部代理，出现无网关回执的 503。 | 仅向对应原生子进程的 `NO_PROXY` 与 `no_proxy` 合并 loopback 地址；保留已有条目，provider worker 仍使用自己的外网环境。 | 只修复本地 IPC 的传输边界，不修改系统代理或外网提供方选择，不把失败改成成功。 |

单个请求仍固定模型 `deepseek/deepseek-v4.1-flash-1017`，提供方选择显式、关闭回退。复杂长图任务本轮实际使用 `morph/fp8`；它不是缺省官方 DeepSeek 路由的全场景验收。模型能力、模型生成错误、OpenAI 云端权益与本地 runtime 机制分别记录。

## 固定原生组件

| 平台 | 继续使用的原生归档 | 状态 |
| --- | --- | --- |
| Windows x86_64 / P41 | [v0.7.1 Windows 归档](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/v0.7.1/codex-deepseek-native-0.7.1-x86_64-pc-windows-msvc.tar.gz) | 归档 URL、SHA-256、recipe 与 native 版本不变。 |
| Linux x86_64 GNU / P35 | [v0.6.10 Linux 归档](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/v0.6.10/codex-deepseek-native-0.6.10-x86_64-unknown-linux-gnu.tar.gz) | 归档 URL、SHA-256、recipe 与 native 版本不变。 |
| macOS Apple Silicon / P35 | [native-macos-p35 归档](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/native-macos-p35/codex-native-aarch64-apple-darwin-p35.tar.gz) | 归档 URL、SHA-256、recipe 与 native 版本不变。 |

原生基线是 `0.164.0-alpha.7` / `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`。这些是带公开补丁的原生构建，不能称作未修改的上游 Rust。0.7.3 本次维护不新增 native 补丁；原有 provider、容量恢复和 Windows 隔离/生命周期补丁继续保留。详见[原生 runtime 契约](../../../docs/runtime-contract.zh-CN.md)。

## 发布前证据快照：2026-11-11

**修复源码的托管验证。** [提交362f0a3的双平台CI](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/42947812597)验证了 `362f0a3de896cecaef66b15f0546eb6892a40239` 的正常安装、固定原生机制和完整离线回归。每平台共3332项，包含22项原生与3310项适配层收集；原生22项两平台各自通过；适配层Linux为3251通过、59项明确平台跳过，macOS为3250通过、60项平台跳过；两平台均无失败、错误或 deselection。新增的 loopback 环境回归两平台均通过。这证明该提交的已测安装与本地机制，不能把结果改称最终 0.7.3 版本身份的 CI。

**Unix/P35 的实际长任务。** 在独立安装 `386c7c755f814715b8e8` 上，DeepSeek 实际完成多文件任务与独立结果检查，随后在同一保存会话中执行容量和冷恢复验收。实际模型请求含32个原图 occurrence，序列化为 68,027,335 字节；本地真实返回 413，回执明确 `pre_upload` / `not_sent`，未发上游推理。原生运行时在同一个 turn 压缩已经确认的前缀，当前新用户文本和16张原图按原字节及顺序保留；同 turn 普通请求随后完成，实际 wire 为 29,786,438 字节。

原生检查点从普通完成，经绑定原 response ID/epoch 的前缀翻译，再由实际普通完成推进。先前非幂等工具在原生历史中只有一组真实 call/output，副作用文件保持一行。旧进程自然退出后，新 native 进程冷恢复同一 UUID，实际读回首尾原图并执行工程测试。24条已完成请求逐条通过保存的 generation 回读核对实际 Morph 提供方和固定 DeepSeek 身份。两个 native 及其 provider/helper 已按 PID/birth 退休；全部工作在原始共享30分钟预算内结束，没有延长时限。

这份补验包含真实容量拒绝，而非仅以16图或12个文件引用的成功代替长会话恢复。首次严格命令验收失败和另一次控制器工作目录前置检查失败均保留；续跑没有覆盖原始失败。独立审计报告 SHA-256 为 `fdd52fe65ebdb0d65cbc66ce3c06b4b986ff023abbaa4c06762bdb6f4a522dfa`。历史 checkpoint 原始文件已被后续推进覆盖，审计明确区分留存的观察投影/哈希与可重新读回的当前原始文件；请求 trace 是图片引用转换前的 payload，实际传输字节另由图片回执证明。

上述实际任务限定 macOS arm64 / P35、该安装身份和配置，不外推到 Windows、Linux 同业务任务或任意图像理解表现。原始上下文、图片、受保护 oracle、完整会话与凭据不公开。

**Windows 发布前状态。** 当时最新候选完成了本地零模型检查；这不等于在线长图任务通过。32图任务因共享机器的内存保护中断，未计为通过；保留退出与资源证据。此前发布版 `ea904580eb62e074b27753f1095ac643d3c5d2d6` 的16图业务、显式压缩、受控413和冷恢复仍绑定[0.7.2 原版回执](../0.7.2/README.md)，不能替代本候选的新任务。

## 0.7.3 每个平台的交付门槛

| 平台 | 最终0.7.3身份的安装/CI要求 | 发布前真实任务快照 | 交付判断 |
| --- | --- | --- | --- |
| macOS Apple Silicon | 须记录精确提交的正常安装、原生机制与完整离线回归。 | 上述原身份 Unix/P35 长任务补验已通过。 | 新身份安装/CI关闭后可单独交付；旧真实任务保持原身份。 |
| Linux x86_64 GNU | 须记录精确提交的正常安装、原生机制与完整离线回归。 | 修复提交已有平台本地机制证据；macOS 长任务不算 Linux 同任务实测。 | 依据 Linux 自身结果单独交付。 |
| Windows x86_64 | 须记录最终新身份的平台安装资格。 | 当时32图资源中断未闭环；后续结果须单独记录。 | 不因 Unix 成功而宣布通过。 |

使用现有 `installed-release-qualification.yml` 对最终提交正常安装并验证，复用已核验的原生归档，不重新编译 unchanged native。版本与 README 会改变安装源码身份，必须产生真实的新 installation；不能改旧回执、旧 CI 或 retained pins 来制造新资格。

本维护版不宣称完成 `assemble_release_acceptance.py` 的严格全冻结聚合资格，也未宣称30项全场景在新安装全部重跑。版本字符串之外的代码未变证明只能说明维护版本的代码差异，不能替代安装、CI、模型任务或平台证据。Linux/macOS 可按各自通过状态先交付；最终状态保存在对应 Release 说明及证据附件，未关闭事项仍保留。独立 CADSG / Codex App 项目另行验收，本页不替它通过。

[安装、同 home 升级与回滚](../../../docs/STABLE_PLAN.zh-CN.md) · [CLI 原生责任与补丁边界](../../../docs/runtime-contract.zh-CN.md) · [已发布0.7.2与原始失败记录](../0.7.2/README.md)
