# v0.7.1 发布与验收

v0.7.1 于 2026 年 11 月 7 日正式发布，源码提交为 df22a98cd8fb26afdda438fd086767aa2c11fb8d。三个受支持平台都从 v0.7.1 源码安装。Windows 使用本版发布的 P41 原生组件，Linux 和 macOS 使用各自清单指定的已有 P35 原生组件。安装器按目标平台、recipe 和 SHA-256 核验原生归档。 [正式发行页](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.7.1) · [逐平台安装和回滚](../../../docs/STABLE_PLAN.zh-CN.md)

| 平台 | 原生归档来源 | 字节数 | 清单 SHA-256 |
| --- | --- | ---: | --- |
| Windows x86_64 | [v0.7.1 P41](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/v0.7.1/codex-deepseek-native-0.7.1-x86_64-pc-windows-msvc.tar.gz) | 204375387 | b0f70d4c4f05be8dc6400146540fb46e9601286a74bb93f71e3aaf559e47186b |
| Linux x86_64 GNU | [v0.6.10 Linux P35](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/v0.6.10/codex-deepseek-native-0.6.10-x86_64-unknown-linux-gnu.tar.gz) | 159347453 | be4192cfb7632ed99f6106d7b429d20097bda58c2648373ec96d0bbe1bc5b6e1 |
| macOS Apple Silicon | [native-macos-p35](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/download/native-macos-p35/codex-native-aarch64-apple-darwin-p35.tar.gz) | 143687920 | bce6bc15400a6b0072dd1c7b13236095f5f80ef869c7f858f8c7420b8a10b00b |

v0.7.1 的发行页只有 Windows 原生归档作为本版上传的二进制资产。Linux 和 macOS 的归档名称保留各自组件原来的发布身份。 [Windows 清单](../../../native/runtime-x86_64-pc-windows-msvc.json) · [Linux 清单](../../../native/runtime-x86_64-unknown-linux-gnu.json) · [macOS 清单](../../../native/runtime.json)

## 验收范围

- Windows P41 完成原生插件 Git 进程和继承句柄的定向回归。Windows Server 2025 对同一 P41 归档完成隔离安装和原生 IPC 退出检查。发布后又直接从公开 tag 和归档安装，核验 33 个模块、原生初始化和物理 EOF。早期 P40 的 marketplace 失败属于保留的历史证据。 [P41 托管验收](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/42365651939) · [公开归档安装回读](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/42367163457)
- Linux 和 macOS 各自从 v0.7.1 提交正常安装，分别运行 22 项原生测试。适配层各收集 2994 项测试，Linux 跳过 55 项，macOS 跳过 56 项，两平台均无失败。 [精确提交的双平台工作流](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/42365028202)
- 四组并发真实图片任务显式使用 `morph/fp8`，关闭了回退。抽查的四个请求均回读到提供方 Morph 和模型 `deepseek/deepseek-v4.1-flash-20261017`。这份四组业务任务回执只覆盖 Morph 路由，并绑定到先前 Windows 原生测试组件与对应适配器源码。原生压缩、冷恢复和副作用验证也沿用原始回执。P41 的独立证据覆盖上述 Windows 安装和 Git/IPC 边界，四图组的完整业务任务仍使用原始资格回执。 [真实任务回执](../0.7.0/real-four-image-task-qualification.json)

这些回执分别记录平台安装、进程退出、适配层测试和真实任务。使用时应按实际平台与任务配置读取对应证据。 [v0.7.0 历史发布与原始失败记录](../0.7.0/README.md) · [官方 DeepSeek 与兼容提供方的历史证据](provider-evidence.zh-CN.md)

## 官方 DeepSeek 路由的 Windows 只读工具复核

2026 年 11 月 9 日在 Windows 上运行了两次独立的原生任务。每轮使用新的隔离 profile 和测试标记，原生审批为 `never`。七条模型请求均经 `official_default/deepseek` 返回 HTTP 200 并完成，提供方回退关闭。逐条 generation 回读确认实际提供方为 DeepSeek，模型为 `deepseek/deepseek-v4.1-flash-20261017`。

| 轮次 | 原生工具结果 | 各请求费用（美元，按请求顺序） | 小计（美元） |
| --- | --- | --- | ---: |
| 首轮 | 模型发起 MCP 调用。测试工具漏报只读注解，原生审批拒绝，工具没有执行。 | 0.0013368、0.00012981、0.000429318、0.000110496 | 0.002006424 |
| 复测 | 补齐标准只读注解后，工具执行一次。原生工具返回和最终回答均与新标记一致。 | 0.0015012、0.000416118、0.000084096 | 0.002001414 |

两轮实测合计 0.004007838 美元，金额取自 generation 的 `total_cost`。复测的标记文件字节保持一致，工作目录仍为空。原生启动器退出码为 0。调度器回执采到了注销前的运行状态，最终调度器退出码未捕获。后续独立回读确认任务注册已移除，本轮工具、启动器、原生进程和专用网关均已退出。

测试使用隔离的 P41 安装，适配器版本仍显示 `0.7.0`。对照 v0.7.1 的 33 个 Python 模块，32 个逐字节一致，唯一差异为版本字符串。原生 Codex `0.164.0-alpha.7` 二进制 SHA-256 `d53f1d62e6ecfabfb0b5f64f8b23c24a8281c5e48d694096523fa4356fe1bb3b` 与 v0.7.1 Windows 发布清单一致。此处仅记录单一只读工具的官方路由实测。此前 Morph 长图业务的验收范围见上文，原始 49 图 HTTP 400 仍未归因。 [Windows 发布清单](../../../native/runtime-x86_64-pc-windows-msvc.json) · [图片失败与对照边界](../../../docs/image-content-boundaries.md)

原始回执 SHA-256 为首轮 `d5900a819cadd84badda99b9ed411c0c79daa2c2e09d64d3c9520dfef0156d67`、复测 `43f552e422e2c3b0ab29fc09b073a2c77676b1235421f5671b57a91c18d7e0d0` 和独立退出回读 `9831b1a3ff06ba55e2b1b9074ce95e35f2b69cea577247138cf205b417dd055c`。

## Windows P41 原生故障复测

2026 年 11 月 9 日，使用与 v0.7.1 Windows 发布清单 SHA-256 完全一致的 P41 codex.exe，在隔离的普通权限会话中运行了未声明工具、错误 JSON 和流提前结束三种本地合成故障。测试使用独立安装 8c48cae49f1757c7ab9e，适配器仍显示 0.7.0，其与 v0.7.1 适配器的逐模块对照见上文。

三种场景均先观察到一次真实工具副作用，再注入故障。原生重试和冷恢复各自读回同一条工具结果，账本保持一次写入，测试进程均退出。未使用真实凭据或外部模型。118 份原始归档成员已逐项读回，归档 SHA-256 为 0b96f03d7c5a14dc2f7333a5c8f50ed1b5799bef988606f0a0ecc5e96d2b1de8。
