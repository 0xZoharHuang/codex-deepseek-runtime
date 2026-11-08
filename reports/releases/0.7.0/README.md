# 0.7.0 图片缓存并发与容量修复

本文保留 v0.7.0 当时的发布与验收记录。当前发行版见 [v0.7.1 发布与验收](../0.7.1/README.md)。

v0.7.0 发布范围是 Linux x86_64 GNU 和 macOS Apple Silicon。该版本发布时，Windows 稳定安装版本为 v0.6.15，Windows Server 2025 的原生插件 Git 子进程尚待独立 stdout 退出验收。

本版修复共享 home 下的大图并发请求可能被全局缓存锁串行、逐图 FULL 提交放大同步等待的问题。原图片字节、调用历史、原生 agent loop、线程、工具、审批和沙箱均不变。Windows 继续使用 P39，Linux/macOS 继续使用已经固定的 P35 原生组件，无需为本次协议修改重编 Rust。

- 远端上传、核验和清理只持对应图片的操作锁；共享账本不跨网络等待。
- 本地状态转换、逐图回执和保护 pin 原子提交，上传意图仍在远端请求前 durable。
- 格式 3 对旧 reader 建立明确 fence。升级与回滚都是已退役 home 上的显式元数据维护，保留全部新旧资产、回执与未知消费 pin，不覆盖旧数据库快照。
- Windows 私有 lock/metadata 文件使用同一 extended-path 规范，保留 no-follow、单链接和实际文件身份核验；新增长 asset 锁名不削弱原长 home 用例。
- 维护入口不启动 native HTTP、不读取 provider key，也不替操作者停止任何进程。未知或活跃 owner、无可核验的 provider 记录、损坏的 daemon PID 记录、忙锁与 dirty schema 都明确拒绝。

[迁移、回滚与故障恢复步骤](../../../docs/image-cache-formats.zh-CN.md)

## 已保留的否定证据

原共享会话错误在模型请求送出前发生，上游状态与 response ID 均为空。现场只读证据显示同步 journal commit 等待与全局锁争用；没有据此虚构某个请求独占锁 120 秒。

最初只合并本地写入事务的候选，在完全相同的四请求、每请求 16 张不同 cold 图、单次 fake Files 上传固定 3 秒、真实时钟和默认 120 秒锁等待下，只有两个请求成功，另两个仍在 prepare 返回前超时。该候选没有发布为问题已解决。

完整按图加锁的候选以同一脚本完成四请求、64 次确认上传，上传重放为零，默认 120 秒不变。所有 fake Files 证据均明确没有真实模型、云上传或原生工具执行，不替代本轮真实 native/DeepSeek 并发任务。

原版的逐图自动提交、第三条回执故障留下半状态，以及迁移 trigger、salt 原子发布、Windows 长路径和测试注入别名问题均保留其原始失败。最终资格应绑定最终源码和完整回执，不把某个中途 hash 的通过外推给后续变更。

## 模型请求中的合计引用字节

真实原生四并发测试确认所有 64 次冷上传，随后四个主任务均在上游收到合计文件大小
HTTP 413。每组 16 张原图约 25.45 MB，测试专用 1 MiB wire 配置要求全部转成引用。
该次失败、原图和工程输入均保留。

随后独立受控探针确认 20,971,520 字节被受理，20,971,521 字节被拒绝，重复同一 ID
也按位置累加。探针所建两个 Files 对象均完成删除与不存在回读。上传单文件的 100 MiB
上限与推理引用合计规则分别适用。[脱敏边界探针](referenced-files-byte-boundary.json) · [OpenRouter Files API 文档](https://openrouter.ai/docs/guides/features/files-api#upload-limits)

候选选择器同时约束位置、引用原始字节和 wire。超限时尝试无损混合内联与 Files。
仅在有证据确定不可行时返回容量拒绝。搜索上限和未知元数据有各自的明确状态。

## 真实任务与平台验收

四个原生会话并发完成多文件对账修复。每组使用原来的 16 张 PNG 和 12 项业务检查。采用已部署的 30 MiB 运行策略时，29 个上游请求都保留全部原图，其中每次两张转为文件引用，其余十四张内联。业务阶段共记录 77 个 HTTP 200。选取四个请求回读实际服务商和固定模型。

一个完成的会话随后通过原生压缩生成七项替换历史。退出原生进程并等待网关自然退役后，新进程恢复同一会话，找回压缩前的会话口令，重新读取原图和业务结果，并通过原测试。独立的本地 TLS 故障夹具检查了工具副作用之后的三种流故障，并核对冷恢复和副作用恰好一次。[脱敏资格回执](real-four-image-task-qualification.json)

Linux 与 macOS 各自完成了正常安装、22 项真实原生传输及恢复验证和完整离线回归。四个真实模型任务和冷恢复在 Windows 隔离安装中执行。平台原生证据见对应工作流。[Linux 验收](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/42339266398/job/128303096434) · [macOS 验收](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/42339266398/job/128303096638)
