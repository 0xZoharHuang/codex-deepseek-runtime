# 历史发布与验收记录

本文保留 v0.3.0、v0.4.0 时的报告入口与实验结果，文中“当前”“本次”均限定于对应章节的历史版本。v0.5.0 的发布范围见[稳定方案](STABLE_PLAN.zh-CN.md)；当前 0.6.0 尚未发布，新增功能和发布前待完成项见[候选说明](release-0.6.0-candidate.zh-CN.md)。历史报告不替代新产物验收。

## 0.4.0 的新增验收

0.4.0 增加基于官方 MCP Python SDK 的网页 open/find/click 文档读取器，保留
原生调度与生命周期；没有新增 Codex 业务处理器补丁。额外修复了未完成回答
进入后端时的 prefill 兼容，以及严格 JSON Schema 结果在验证前被发布为完成
事件的问题。普通模型调用仍优先官方 DeepSeek，不能表达的后端语义明确报错。

`reports/acceptance.json` 与 `reports/runtime-qualification.json` 是该历史发行包的
入口；0.3.0 的原始聚合报告保留在 `reports/history/v0.3.0/`。方法矩阵修正了
原先范围分类：125 个本地方法均有正向运行证据，其中新增逐项实测 60 个；
原来缺口列表中的另外 4 个实际属于托管 Apps/远端插件后端，只验证其边界。
五个系统用户验证接口也在上游要求 ChatGPT 账户身份，原版/补丁版一致拒绝
无账户环境，不把拒绝算成生物验证的成功注册或签名。

离线回归 127 项。原有 28 个非真实工程场景在同一冻结安装包执行，真实工程
另行要求实际 open/find/click、原生 reviewer、新一轮复核、外部 oracle 和完整
测试。Jinja 通过 918 项工作区测试和 908 项外部测试；HTTPcore 通过 220 项
工作区测试、207 项外部测试，原有 6 项 XPASS 单列，另外通过独立取消回归、
三轮真实 TCP 压力与 asyncio/Trio 的真实 TLS 连接关闭检查。

受控协议故障证明：错误工具和错误 JSON 会由原生 CLI 重试恢复，协议桥没有
执行或重放工具。网页验收包括公开 HTTPS、随机链接内容、冷恢复、显式 MCP
取消和原版 CLI 生命周期对照。原生中断采样不保证取消已发出的 MCP 请求，
而 MCP 显式 cancellation 与所有者退出分别验证关闭 HTTP。

这表示固定平台和指定验收场景内的本地 runtime 机制已经检查，不表示所有
174 个方法、账号服务、平台以及任意参数/故障组合都有完整正向生命周期证明。

重复冷启动验收还暴露了数据库初始化窗口：后台服务可能先看到 SQLite 文件，
再看到完整表结构。修复后对无法读取的所有权状态保守保留服务，等待 worker
完成初始化；不将未知所有权当成闲置，也不让 supervisor 因缺表退出。通过
固定写锁延迟 schema 发布的真实进程测试验证了这一竞态。

下节保留 0.3.0 的历史背景，不能当作当前覆盖数量。

## 0.3.0 历史背景

本次发布固定 Codex `0.164.0-alpha.7`、commit
`fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`，平台为 macOS arm64。
DeepSeek 模型固定为 `deepseek/deepseek-v4.1-flash-1017`。
这些是冻结版本；上游预发布性质、模型判断质量和云端权益不因适配而改变。

## 如何阅读证据

- `reports/acceptance.json`：最终发行包及证据链入口。
- `reports/runtime-qualification.json`：29 个原生 runtime 场景及复杂工程回归。
- `reports/qualification-lineage.json`：原始全量运行、定向复验的哈希关联。
- `reports/diagnostic-repair.json`：最后一处诊断分支修复的精确差异、安装完整性和真实运行证据。
- `reports/runtime-method-matrix.json`：174 个原生客户端方法的范围与执行证据。
- `reports/native-schema-comparison.json`：456 份协议 schema 与同版官方二进制一致。
- `reports/audit-history.json`：发现的问题、验证条件修正和真实任务的迭代历史。

65 个客户端方法在通过的场景中实际出现；其余方法保留原生处理器，但没有
逐个执行。矩阵另列平台依赖、账号/后端依赖和上游声明但未实现的方法。
不能把 schema 一致或源代码保留等同于每个接口都已逐项实测。

原始全量运行曾有三个未通过项：v1/v2 记忆的随机编号保留断言，以及 Jinja
任务的 30 分钟预算。记忆提取、整理、持久化和冷启动注入实际上已成功；
按用户明确的 runtime 范围，把随机编号保留作为模型表现单列，改为核对
生成摘要与新冷会话实际输入逐字一致，并对两个版本重新实测。
Jinja 从同一保存会话继续，保留原补丁和 reviewer，完成最终审查与外部回归。
原始失败和超时记录没有被覆盖。

随后在 Git 仓库之外发现专用 `runtime doctor` 的内部 `exec` 探测缺少
`--skip-git-repo-check`。最终包只在这个只读诊断分支补入该参数。
`diagnostic-only-repair.patch`、完整源码差异和去除诊断函数后的 CLI AST
哈希证明，其余 CLI 执行路径及所有原生二进制均未变化。修复后又通过
106 项离线回归、同一曾失败的非 Git home 实际连接、默认 home 实际连接、
TUI 启动和 daemon 停止后的冷恢复。普通 `exec` 的 Git 信任规则保持原生行为。

## 重新验收

准备 README 所列的独立 Python、完整原生包和独立 native-policy probe。
所有在线测试都调用付费 API，使用单独 home，避免在用户工作目录里运行测试。

```sh
python3 prepare_real_work.py --output /fresh/fixtures --python /path/to/python3.11
python3 run_acceptance.py --entry /immutable/release/venv/bin/codex-deepseek \
  --native-probe /verified/native-policy-test-executable \
  --fixtures /fresh/fixtures --output /private/acceptance --jobs 2
```

真实任务受预算约束；模型可能过度扩展检查。保留失败报告后，
`verify_real_work_resume.py` 可从原生保存会话完成一个预算超时的任务：

```sh
PYTHONPATH=src python3 verify_real_work_resume.py \
  --entry /immutable/release/venv/bin/codex-deepseek \
  --fixtures /existing/fixtures --output /private/continuation
```

它不会重写原始失败，必须完成真实回合、使用原生 reviewer，并重新运行外部
回归。`assemble_scoped_acceptance.py` 是本次特定记忆范围校正的严格凭据合并器，
会拒绝运行代码或无关验证器的变化；它不是通用的“忽略失败”工具。
新的运行时修改原则上重新执行相应验收。精确的诊断分支修复另附组件差异证明。

## 发布与清理

发布源代码、固定原生归档、校验和及公开聚合报告；不上传原始模型上下文、
用户路径、OAuth token、私有 home 或 API Key。Key 保留在专用 macOS Keychain。

使用验证安装器切换入口；停止旧 daemon 后，必要时执行
`app-server daemon update --from-cli --yes`。原生生产 updater 会覆盖此发行包
的接入补丁，因此本发行包使用自己的验证安装流程。

原始日志和会话留在私有证据目录，生成的 Rust target、结束测试的 daemon
包副本、临时插件下载和旧 fixture 环境可在确认所有者结束后清理。
保留源码补丁、依赖锁、构建/验收凭据、用户会话和必要回退材料。
用 `df` 衡量物理空间变化，避免把重叠目录的 `du` 相加当成回收量。
