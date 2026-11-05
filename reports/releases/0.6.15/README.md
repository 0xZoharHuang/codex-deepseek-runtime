# CDS v0.6.15 安装与运行记录

v0.6.15 修复了下载大型原生归档时的中途断流。三个平台都可使用同一个源码 tag。Windows 继续使用 v0.6.14 的 P39 包，Linux 与 macOS 使用 v0.6.13 已验证的组件。归档 URL、原生 recipe、补丁和每个文件的 SHA-256 都已与原发行逐项核对。[原生组件来源](native-inputs.json)

| 平台 | 默认清单全新安装 | 实际运行 |
| --- | --- | --- |
| Windows x64 | 24.5 秒，独立 Windows Server 2025 从 GitHub 直连 | 3,018 个原生文件、32 个模块、原生 app-server 初始化、EOF 和进程退出。 |
| Linux x86_64 GNU | 17.8 秒，Ubuntu 24.04 | 22 项原生传输与恢复用例通过。完整集合中 2,897 项通过，55 项按平台条件跳过。 |
| macOS Apple Silicon | 17.8 秒，独立 macOS runner | 22 项原生传输与恢复用例通过。完整集合中 2,896 项通过，56 项按平台条件跳过。 |

三次安装都使用固定公开归档及独立目录。上表对应已核验的候选源码，安装身份为 `89d90737f607ae21d13b`。Linux 与 macOS 的完整集合均为 2,952 项，22 项原生用例已计入总数。[三平台回执](candidate-platform-qualification.json) · [Windows 实测](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/42011237299) · [Linux / macOS 实测](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/42011237224)

## Windows 10 的断流与恢复

实际 TLS 客户端在握手被对方提前关闭时报告 `SSLEOFError`。早期候选在这种情况下直接失败，其正常安装通过记录与这个故障分别保留。最终安装器能对同一 URL 重试，遇到无效证书时第一次连接就返回错误。部分文件续传还会验证响应的字节区间和总长度。Windows 的 226 项针对性测试通过。

在目标 Windows 10 上，测试让真实 TLS 握手中断一次。安装器按预期重试，收到 160,301,056 字节时测试达到原有的 900 秒上限。收到的字节与完整公开归档的前缀一致。在这台机器上另用官方 `--native-archive` 入口安装同一份完整归档，81.2 秒完成。核验了全部 3,018 个文件和 32 个模块，原生 app-server 初始化、EOF 和后台进程退出均通过。[目标 Windows 回执](windows-installer-qualification.json)

慢速网络可先下载平台清单给出的归档，然后从本 tag 的源码目录执行 `python install.py --native-archive "<归档路径>" --root "<独立安装目录>" --no-activate`。现有任务可以继续使用原来的不可变入口。[安装、升级与回滚](../../../docs/STABLE_PLAN.zh-CN.md)

## 原生 runtime 的证据

本版改动涉及安装器和版本声明。协议适配、沙箱、工具执行、压缩及子智能体沿用各平台已验证的代码。Windows 的 P39 实测包含普通用户权限、允许与拒绝、冷恢复、模型驱动多文件工程及原生审阅子智能体，独立工程评分 930/930。96 图任务绑定其实际运行的原版本和提供方，原始失败均保留。[Windows 运行与模型记录](../0.6.14/README.md) · [长会话来源](../0.6.14/capacity-lineage.json) · [Linux 与 macOS 基线](../0.6.13/README.md)

Codex 桌面 App 的接入由独立 CADSG 项目验收。回滚 CLI 时，Windows 可固定 v0.6.14，Linux 与 macOS 可固定 v0.6.13。

## 正式 tag 回读

三个独立 runner 从正式 tag 17917162b707 再次使用默认清单安装，32 个模块、原生初始化与 EOF 均通过。Windows 安装耗时 26.055 秒。Linux 和 macOS 的原生传输与完整测试集合也已在这个 tag 上复测，逐平台结果、条件跳过与匿名资产摘要均见[正式发布回执](publication-receipt.json)。
