# v0.6.2 发布与资格说明

**v0.6.2 已正式发布为 stable GitHub Release。** Release `423636524` 已核实 draft=false、prerelease=false，并完成 latest 读回；状态与公开验证记录以同名 [Release](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.2) 为准。产品 tag 保持 `4f24af36f62c538ba35817ad48aa49d4308220b5`，QA 验证提交为 `d325437588e7fbde418aa52f008740ea96fab0fd`，product99 与 native 资产未变。main 已快进到已验证的 QA 提交并经 GitHub 读回确认，见[发布回执](publication-record.json)；正式发布不等于已升级或激活日常默认 `cds`。

本版保留原生 Codex 的任务循环、工具、审批、沙箱、会话和子智能体。固定上游为 Codex `0.164.0-alpha.7`、commit `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`，模型为 `deepseek/deepseek-v4.1-flash-1017`，经 OpenRouter 调用。上游仍是 alpha 版本。

## Windows 资格与失败边界

D69 `d69a4edd5b8de66f3db3` 的严格聚合已通过并封存：2,150 项 offline 通过、4 项平台不适用，30 个有效合并场景，128 项正向 RPC 与 3 项原生配置边界、456 份 Schema 无差异。完整 64 方法包含 59 个正向方法、4 个后端边界和 1 个平台边界。原始一轮 **29 通过/1 项 TUI 失败**和后续两个终端补测分别保留，`single_run_all_passed=false`。

Jinja 的 runtime 14 项和业务验证均通过；HTTPcore 的 runtime 14 项通过，**业务仍失败**。原始超时、provider 错误和失败 suite 不被后续结果覆盖。同轮请求恢复后明确受控中断，只有原始 RPC、回执和实际工具事件精确对应时才单独分类为 `request_recovered_then_explicit_interrupt`。见[资格摘要](windows-qualification-summary.json)。

## CF9 新安装与 QA 谱系

CF9 `cf9f19eaf67b0fdd8151` 已在新不可变 root 中正常安装，未激活稳定入口。[实际组件比较](component-equivalence.json)确认 runtime 29 文件、catalog、依赖锁、installed native manifest 和全部 2,991 个 native 文件相同。产品99仅变化 manifest 生成器的 LF 设置及 Windows manifest 换行；JSON 相同。新入口保留自己的部署哈希，D69 raw tests 不改签为 CF9 全量重跑。

[当前完整 offline](current-offline-summary.json)为 **2,229 通过、4 项平台不适用、0 失败/错误**。正常 doctor 已核实固定模型和官方 DeepSeek；加上一个保存线程的请求，共2次真实 completed/HTTP200 请求。同 home 后续0模型调用验证了受限原生 Python 写入、外部拒绝、EOF退出0和同线程历史/文件冷恢复。15个 continuation actor 与 provider 自然退出，forced/remaining/unknown/errors均0。初始受约束语言模式拒绝与控制器记录错误保留，见[安装健康摘要](candidate-installation-summary.json)。

D69源码和consumer先封存，再应用五项[QA observer安全升级](qa-upgrade-lineage-summary.json)。升级不属于product99；新mixed/payload/Low+High专项及当前offline单独记录，旧helper `0af…`证据不标成新helper `1cc…`执行。

## 发布与回滚

提交 `71224bc1ebb40374ea816f9ae12cf78134537a83` 的 [CI 40227053597](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40227053597) 已通过：Linux 171 controller＋21 native＋2,124 adapter/40 skip，macOS 171＋21＋2,123/41 skip；完整集合各执行一次，进程 hygiene 通过，0 模型请求、0 native rebuild。见[托管精简报告](hosted-qualification-summary.json)。该历史轮次使用 `actions-artifact`；后续公开资产路由有独立实际验收。

此前 Windows 2,179/4 结果及 [CI 40225926715 的 collection 失败](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40225926715) 保留。修复仅让隔离 Python 显式导入可信 ROOT 中的 QA helper，仍保留 `-I` 和 installed package，不加入 `ROOT/src`；新增两个真实隔离入口用例后，当时 Windows 完整集合为 2,181/4；该结果和更早的 2,179/4 均保留。

公开 `release-asset` 路由现已由 [CI 40233851528](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40233851528) 实际通过：Linux 171 controller＋21 native＋2,172 adapter/40 skip，macOS 171＋21＋2,171/41 skip；两平台均下载了精确公开归档与原 recipe，进程 hygiene 通过，0 模型调用、0 native rebuild。见[公开资产资格报告](public-asset-qualification-summary.json)。

首次公开资产 [CI 40230459754](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40230459754) 的 macOS 成功、Linux 20/1 metadata-race 失败仍保留。[QA 诊断修复](public-asset-observer-audit.json)增加48个纯反例（连同12个既有共60），保留全部自然退出与身份守卫，并用真实受限 native command 验证严格 JSON guard 能拒绝实际外部执行。原失败不改签，tag 不重写，产品99与native不变；最新 Windows 完整offline为2,229/4。

[正常 Windows 公开安装读回](public-install-readback.json)未传本地 native archive/manifest override，实际下载并校验公开归档和2,991个native文件，真实version/help退出及进程收尾通过；Python依赖使用哈希锁定的本地wheelhouse。此项未激活日常默认入口，也未再次调用模型。Release 已正式 stable，main 合并状态在[发布回执](publication-record.json)中另行确认。

安装须匹配源码、manifest和原生归档，普通用户无需重新编译Rust。新版本使用独立不可变目录；保留上一已验证安装和原home。维护前核对该home的任务与服务已结束，不复制sandbox身份目录、不删除用户会话、不按全局进程名清理。

公开报告仅保留必要身份、效果、失败与摘要；凭据、完整模型提示、私有home、SQLite和raw trace不公开。模型业务质量、OpenAI云端权益与Codex App网关仍属独立范围。
