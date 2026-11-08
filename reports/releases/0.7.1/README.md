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

这些回执分别记录平台安装、进程退出、适配层测试和真实任务。使用时应按实际平台与任务配置读取对应证据。 [v0.7.0 历史发布与原始失败记录](../0.7.0/README.md)
