# Windows 0.6.11：原生启动修复与 runtime 验收

本版固定 Windows x64 Codex `0.164.0-alpha.7` 与 P36。修复交互桌面下受限 runner 在建立 IPC 之前启动失败的问题：只恢复上游在可见 WinSta0 上的默认启动描述，保留非交互 Service station 的原有处理。没有扩大桌面 ACL、文件或网络权限，也没有替换 agent loop、工具调度或子智能体实现。

Windows v0.6.11 已正式发布；公开下载、全新正常安装、32 模块一致性和正常入口/EOF/provider idle 均通过。[发布回执](publication-receipt.json)与[发行页](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.11)记录真实来源，tag 保持不变。Linux v0.6.10 保持独立，CADSG / Codex App 单独验收。

实际证据包括：

- 同一正常安装的原生包，在普通权限交互桌面和独立普通用户的 Session 0 两种环境中，分别验证只读、工作区写入/越界拒绝、Ctrl-C 和真实 stdin EOF。目标进程由原始句柄核对退出。
- 原有 Windows 会话连续保留 96 张原图、96 条记录与准确汇总，两次关闭后以同一 UUID 恢复并重新读取早期原图；4 次自然压缩有真实上游请求与原生事件对应。
- 至少 6 个同一 P36 组合的大请求完成回执：图片适配前超过 30 MiB，适配后在限额内；3 个 generation 经 OpenRouter 独立回读，固定模型的 canonical ID 与 Morph 提供方匹配。官方 DeepSeek 缺省选择未改，测试中的 Morph 是显式配置。
- 原生子智能体实际完成两轮独立复核，并把发现交回主线程；一次性副作用账本始终只有 1 条。
- `turn/interrupt` 保留可恢复后台终端，原生 list 后按准确 processId terminate，实际 worker 在 0.028 秒内退出。最终 CLI/stdin EOF 正常退出；provider 按原定 120 秒 idle 策略自行结束，外部观察器没有强制清理，也没有剩余进程。
- 正常独立安装的相关离线回归 336 项通过，无跳过；构建观察器及验收逻辑另有 501 项通过。测试组有交集，不相加宣称覆盖所有输入组合。

## 业务基准的原始失败

96 图审核及汇总通过。DeepSeek 写出的 Jinja 业务补丁仍有一个新发现的元数据回归：最后一次工作区测试为 917 项、1 项失败；独立上游测试为 908 项、0 失败。最终复核阶段达到原定 1200 秒界限。这里明确保留 `FAIL_OR_INCOMPLETE`，不把它写成完整业务任务成功，也不把模型生成代码正确性冒充本地 runtime 等价保证。

原先 R478 的“turn/interrupt 必须自动杀掉后台终端”断言与上游契约不符，该失败保留；后续显式 list/terminate 验证另存。原先临时目录冲突、衍生图片而非本轮原图、上游流错误、超时和复核发现均没有被最后的 runtime 结论抹去。

## 图片状态与使用边界

已知完成请求的保护 pin 为 0，未知上传资产为 0。仍有 14 个上游消费结果不明的历史调用、142 个保护 pin；正常有界 reconcile 后仍未知，继续按原有规则保留。此处没有宣称云端资产已全部删除，也没有强制清库。

[机器可读索引](qualification-index.json)、[runtime 审计摘要](runtime-validation.json)、[安装与回滚](../../../docs/release-0.6.11.zh-CN.md)。原始详细证据保留在独立验收目录；公开摘要记录内容哈希和准确范围。
