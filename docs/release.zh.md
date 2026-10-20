# 发布与验收记录

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
