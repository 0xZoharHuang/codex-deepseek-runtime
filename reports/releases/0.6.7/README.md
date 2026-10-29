# 0.6.7 验收候选（尚未发布）

本轮修复两处具体问题：严格 JSON Schema 最终输出前，允许原生客户端工具搜索的合法中间调用；以及压缩历史冷恢复后，容量检查点因默认 metadata 表示不同而无法匹配的问题。最终 Schema 校验仍然保留。工具、审批、线程和压缩仍由原生 Codex 执行。

## 固定范围

- Codex CLI 0.164.0-alpha.7，源提交 fe136d4e70ef2b5bbed40a2d22b3aabf267796d3。
- 当前代码验收源 ce701f7f9c4f2dd333db53c4e1a1779db646f9e4；最终发布还会加入本报告和实际产物 manifest。不能把新增文档/产物清单误写成当时的原测试源。
- 模型固定 deepseek/deepseek-v4.1-flash-1017；模型质量、模型不支持的模态及 OpenAI 私有云服务/账户权益单列。
- 本轮没有本机 Mac 测试。macOS 和 Linux 构建、运行发生在 GitHub 官方临时 runner；Windows 开发和安装验收在独立目录完成，没有改变防护设置或正在运行的旧 CDS。

## 已完成的验证

| 层次 | 结果 | 边界 |
| --- | --- | --- |
| Windows 新原生 CLI，普通用户 token | 22/22 原生传输/恢复用例通过，零跳过 | 包含三个真实 CLI 进程、连续两次冷恢复/413/原生压缩/后续完成；不使用模型 |
| Windows 当前安装版完整离线套件 | 2388 唯一用例，2384 通过、4 个既定系统不适用项、零失败 | 使用已有管理员测试 token 构造 symlink 和 BA/SYSTEM owner 等对抗 fixture；不等同普通用户 runtime 执行 |
| macOS arm64 GitHub runner | 22/22 原生用例；2366 个 adapter 用例中 2321 通过、45 个系统不适用 | [原运行](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40730907886)；无提供方凭据/真实模型 |
| Linux x64 GNU GitHub runner | 22/22 原生用例；2366 个 adapter 用例中 2322 通过、44 个系统不适用 | 同一固定运行；当前 GNU 包不是 musl 静态包 |
| 三平台源码与包 | 完整源码清单、补丁重放、外部 Cargo 依赖不变、所有实际包文件哈希通过 | Windows 上只读核验 Unix 包，不执行 Unix 二进制 |
| 真实 DeepSeek 当前安装版冷续跑 | 待完成 | 保留首次续跑的 MCP 工具目录缺失/反复搜索失败，不以此前旧 native 的成功代替 |

Windows 普通 token 首轮全套出现 15 个 fixture 构造失败：11 个 Win1314 符号链接权限错误和4个 Win1307 owner 赋值错误；另3项因同类权限被既有测试跳过。没有放松断言、加入新 skip 或改变系统设置。随后管理员 fixture 运行的同2388个ID中，这18项均实际执行通过，原失败保留。4个最终 Windows 不适用项为两项 POSIX shell/shebang、原生 Unix symlink extraction 和 Linux daemon identity。

私有外层控制器还有一次额外失败：把仅适用 Unix CI 的 Windows-only skip 目录加到了 Windows 预期。实际 pytest 和公开严格结果 reader 当时已通过；此处用原 reader 独立回读，同一原始 XML 和 origin 记录验证，没有重写原结果。27个产品模块、4774次 import origin 检查均来自安装产物。

## 容量恢复的改动

这不是另写一个 agent loop。已有扩展在原生 runtime 内持久化容量检查点、epoch/fence 和恢复证据，然后调用原有 compactor；新补丁只修正 metadata 的哈希投影及固定旧格式验证。

新 v3 将缺省 metadata 与序列化往返得到的 typed Default 归一。非默认和 opaque metadata、内容、工具调用 ID、顺序与媒体仍严格比较。仅接受新 v3 或两个固定 v2 形式；不枚举任意 metadata 组合、不编辑历史/检查点、不以最终任务成功掩盖中途失败。

旧 v2 runtime 不能读取新 v3 检查点；降级后容量恢复可能明确失败，历史保留。不要手改 checkpoint 来制造兼容。新常规完成会记录自己的正确 epoch。

Windows 只重新编译 CLI；现有5个 helper 的原始 producer/哈希留在 build receipt 的 reused_components，不把它们冒称为新源码重新编译，也不承诺未来防病毒判定。Linux/macOS 本轮是完整新 native 构建。

## 真实任务证据的使用

0.6.6 的真实 Jinja 修复、独立审查、网页访问、权限与清理记录仍保留为各自固定版本证据。本轮只在未变化组件有明确来源比对时复用，不能把旧运行标成新安装版全绿。新的真实模型冷续跑和最终发布安装回读尚未关闭，因此本报告当前仍为候选，不是稳定发布声明。
