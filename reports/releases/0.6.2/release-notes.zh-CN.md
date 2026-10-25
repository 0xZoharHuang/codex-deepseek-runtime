# v0.6.2 候选发布说明（尚未发布）

**Windows 固定组合的严格资格、CF9 正常新安装与最小实际运行验证已闭合；最终 hosted CI（包括本轮 macOS/Linux）和正式 main/tag 发布待办。** 现有稳定入口保留。

本版保留原生 Codex 的任务循环、工具、审批、沙箱、会话和子智能体。固定上游为 Codex `0.164.0-alpha.7`、commit `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`，模型为 `deepseek/deepseek-v4.1-flash-1017`，经 OpenRouter 调用。上游仍是 alpha 版本。

## Windows 资格与失败边界

D69 `d69a4edd5b8de66f3db3` 的严格聚合已通过并封存：2,150 项 offline 通过、4 项平台不适用，30 个有效合并场景，128 项正向 RPC 与 3 项原生配置边界、456 份 Schema 无差异。完整 64 方法包含 59 个正向方法、4 个后端边界和 1 个平台边界。原始一轮 **29 通过/1 项 TUI 失败**和后续两个终端补测分别保留，`single_run_all_passed=false`。

Jinja 的 runtime 14 项和业务验证均通过；HTTPcore 的 runtime 14 项通过，**业务仍失败**。原始超时、provider 错误和失败 suite 不被后续结果覆盖。同轮请求恢复后明确受控中断，只有原始 RPC、回执和实际工具事件精确对应时才单独分类为 `request_recovered_then_explicit_interrupt`。见[资格摘要](windows-qualification-summary.json)。

## CF9 新安装与 QA 谱系

CF9 `cf9f19eaf67b0fdd8151` 已在新不可变 root 中正常安装，未激活稳定入口。[实际组件比较](component-equivalence.json)确认 runtime 29 文件、catalog、依赖锁、installed native manifest 和全部 2,991 个 native 文件相同。产品99仅变化 manifest 生成器的 LF 设置及 Windows manifest 换行；JSON 相同。新入口保留自己的部署哈希，D69 raw tests 不改签为 CF9 全量重跑。

[当前完整 offline](current-offline-summary.json)为 **2,179 通过、4 项平台不适用、0 失败/错误**。正常 doctor 已核实固定模型和官方 DeepSeek；加上一个保存线程的请求，共2次真实 completed/HTTP200 请求。同 home 后续0模型调用验证了受限原生 Python 写入、外部拒绝、EOF退出0和同线程历史/文件冷恢复。15个 continuation actor 与 provider 自然退出，forced/remaining/unknown/errors均0。初始受约束语言模式拒绝与控制器记录错误保留，见[安装健康摘要](candidate-installation-summary.json)。

D69源码和consumer先封存，再应用五项[QA observer安全升级](qa-upgrade-lineage-summary.json)。升级不属于product99；新mixed/payload/Low+High专项及当前offline单独记录，旧helper `0af…`证据不标成新helper `1cc…`执行。

## 发布与回滚

仍待最终提交的hosted CI、正式main/tag、匹配公开资产及下载/完整性/安装读回。旧版本CI和manifest中的未来URL不证明本候选已发布。完成这些步骤后，再统一更新发布状态并记录实际 commit、tag 和 CI 链接。

安装须匹配源码、manifest和原生归档，普通用户无需重新编译Rust。新版本使用独立不可变目录；保留上一已验证安装和原home。维护前核对该home的任务与服务已结束，不复制sandbox身份目录、不删除用户会话、不按全局进程名清理。

公开报告仅保留必要身份、效果、失败与摘要；凭据、完整模型提示、私有home、SQLite和raw trace不公开。模型业务质量、OpenAI云端权益与Codex App网关仍属独立范围。
