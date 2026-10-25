# v0.6.2 发布与资格说明

**本提交是候选交付声明，不独立承诺 stable。** 实际 draft/prerelease/stable 状态及公开资产验证记录以同名 [v0.6.2 GitHub Release](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.2) 为准；Release 不存在或为 draft 时，不表示资产 URL 已可用，prerelease 也不等于 stable。Windows 资格和 CF9 安装已闭合，当前 macOS/Linux 托管复用 CI 已通过。

本版保留原生 Codex 的任务循环、工具、审批、沙箱、会话和子智能体。固定上游为 Codex `0.164.0-alpha.7`、commit `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`，模型为 `deepseek/deepseek-v4.1-flash-1017`，经 OpenRouter 调用。上游仍是 alpha 版本。

## Windows 资格与失败边界

D69 `d69a4edd5b8de66f3db3` 的严格聚合已通过并封存：2,150 项 offline 通过、4 项平台不适用，30 个有效合并场景，128 项正向 RPC 与 3 项原生配置边界、456 份 Schema 无差异。完整 64 方法包含 59 个正向方法、4 个后端边界和 1 个平台边界。原始一轮 **29 通过/1 项 TUI 失败**和后续两个终端补测分别保留，`single_run_all_passed=false`。

Jinja 的 runtime 14 项和业务验证均通过；HTTPcore 的 runtime 14 项通过，**业务仍失败**。原始超时、provider 错误和失败 suite 不被后续结果覆盖。同轮请求恢复后明确受控中断，只有原始 RPC、回执和实际工具事件精确对应时才单独分类为 `request_recovered_then_explicit_interrupt`。见[资格摘要](windows-qualification-summary.json)。

## CF9 新安装与 QA 谱系

CF9 `cf9f19eaf67b0fdd8151` 已在新不可变 root 中正常安装，未激活稳定入口。[实际组件比较](component-equivalence.json)确认 runtime 29 文件、catalog、依赖锁、installed native manifest 和全部 2,991 个 native 文件相同。产品99仅变化 manifest 生成器的 LF 设置及 Windows manifest 换行；JSON 相同。新入口保留自己的部署哈希，D69 raw tests 不改签为 CF9 全量重跑。

[当前完整 offline](current-offline-summary.json)为 **2,181 通过、4 项平台不适用、0 失败/错误**。正常 doctor 已核实固定模型和官方 DeepSeek；加上一个保存线程的请求，共2次真实 completed/HTTP200 请求。同 home 后续0模型调用验证了受限原生 Python 写入、外部拒绝、EOF退出0和同线程历史/文件冷恢复。15个 continuation actor 与 provider 自然退出，forced/remaining/unknown/errors均0。初始受约束语言模式拒绝与控制器记录错误保留，见[安装健康摘要](candidate-installation-summary.json)。

D69源码和consumer先封存，再应用五项[QA observer安全升级](qa-upgrade-lineage-summary.json)。升级不属于product99；新mixed/payload/Low+High专项及当前offline单独记录，旧helper `0af…`证据不标成新helper `1cc…`执行。

## 发布与回滚

提交 `71224bc1ebb40374ea816f9ae12cf78134537a83` 的 [CI 40227053597](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40227053597) 已通过：Linux 171 controller＋21 native＋2,124 adapter/40 skip，macOS 171＋21＋2,123/41 skip；完整集合各执行一次，进程 hygiene 通过，0 模型请求、0 native rebuild。见[托管精简报告](hosted-qualification-summary.json)。此轮使用 `actions-artifact`，不代表公开 release-asset 路由已验收。

此前 Windows 2,179/4 结果及 [CI 40225926715 的 collection 失败](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40225926715) 保留。修复仅让隔离 Python 显式导入可信 ROOT 中的 QA helper，仍保留 `-I` 和 installed package，不加入 `ROOT/src`；新增两个真实隔离入口用例，当前 Windows 完整集合为 2,181/4。

交付配置仅将来源显式切为 `release-asset`，原 producer、recipe 和 pins 不变，也没有自动 fallback。资产公开前不运行该路由；公开后须有真实资产路由 CI、普通下载和完整性/安装读回，再依据同名 Release 的实际状态完成 stable/main。验证链接记录在 [Release](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.2)，不能由本文件、未来 URL 或旧 CI 推定成功。

安装须匹配源码、manifest和原生归档，普通用户无需重新编译Rust。新版本使用独立不可变目录；保留上一已验证安装和原home。维护前核对该home的任务与服务已结束，不复制sandbox身份目录、不删除用户会话、不按全局进程名清理。

公开报告仅保留必要身份、效果、失败与摘要；凭据、完整模型提示、私有home、SQLite和raw trace不公开。模型业务质量、OpenAI云端权益与Codex App网关仍属独立范围。
