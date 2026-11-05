# Windows v0.6.14 实测记录

本版修复了 Windows 上两种受管安装故障。一种发生在普通用户首次执行工具时，旧版需要修改管理员拥有的上层目录，于是收到访问拒绝。另一种发生在管理员维护既有安装时，普通 UAC 会话无法重新打开 SYSTEM 创建的沙箱程序目录。P39 已用正式安装包在两种身份下完成复测。

| 平台 | 安装版本 | 版本记录 |
| --- | --- | --- |
| Windows x64 | v0.6.14 | P39 原生组合。见下文的正式包和维护回执。 |
| Linux x86_64 GNU、macOS Apple Silicon | v0.6.13 | 按该平台 tag 安装并使用对应清单。[v0.6.13 记录](../0.6.13/README.md) |

## 正式包与普通用户工具

正式包包含六个同源编译的程序，以及来自固定官方归档的 Code Mode Host 和 ripgrep。Git 归档与安装器使用的 138 个文件逐字节一致，源码身份为 `b78fbc4332161ca41d7f`。Windows 原生归档的 SHA-256 为 `5672435dbc8673ff18213cb61fd4e3e7d0f52208c2aa364ea7c0cfb67e529328`。正常安装验证了 32 个 Python 模块的实际来源、app-server 初始化、EOF 和后台 provider 自然退出。[正式包回执](windows-runtime-qualification.json)

实际 Windows 管理员通过 `sandbox setup --elevated --user … --codex-home …` 维护同一个普通用户的 home。正式安装的 setup 进程返回 0。原 namespace、沙箱账户、物理目录身份和已有权限项均通过独立回读。普通用户在维护前后都无法修改上层目录的权限描述符。[Windows 维护指南](../../../docs/windows-managed-maintenance.md)

| 实际执行策略 | 工作区读 | 工作区写 | 上层目录列举和写入、相邻私有文件读取 |
| --- | --- | --- | --- |
| 只读 | 成功 | 拒绝 | 拒绝 |
| 工作区可写 | 成功 | 成功 | 拒绝 |
| 再次只读 | 成功 | 拒绝 | 拒绝 |

首次启动和冷启动都执行了这三步。冷启动使用新的原生进程和原来的 home。把同一工具放在授权读取范围之外时，错误报告包含 `SpawnChild`、Windows 错误 5 和实际进程状态；同一 app-server 随后成功执行了授权工具。[正式包回执](windows-runtime-qualification.json)

## 宿主创建的私有工作区

CDS 准备自己 home 的祖先目录，以及已授权且物理位于该 home 内的私有中间目录。宿主在 home 外创建私有工作区时，应在原有的一次性目录初始化中，为已核验的 namespace group 设置非继承的 `READ_ATTRIBUTES | SYNCHRONIZE`，即 `0x100080`。

实测在两层宿主私有目录上保留旧授权 `0x80`，在只读和工作区可写策略下都重现了 Node 的 `EPERM/lstat`。宿主只修正这两条元数据授权后，同一 Node 成功加载真实 JS 文件；父目录和相邻私有目录的越界访问继续被拒绝。第一次夹具意外禁止了工具目录继承权限，该次失败单独保留。[外部工作区对照](external-workspace-qualification.json)

## 原始失败与修复依据

- 上一个 P38 候选可以由 SYSTEM 维护，但在普通 UAC 管理员会话中遇到访问拒绝。该候选的清单、marker 共享冲突，以及测试夹具中的原始错误均已保留。[P38 清单](retained-p38-native-manifest.json) · [初次诊断](qualification-progress-r532.json) · [普通用户复核](qualification-progress-r534.json)
- P39 只在原生维护已验证的、SYSTEM 拥有的受保护沙箱程序目录时使用窄范围重试。它检查原生四条权限项、固定目录身份并重新验证写句柄。管理员进程的权限标志前后一致。[维护与继承实测](p39-maintenance-qualification.json)
- 一次断言预期旧式显式 ACL 会新增继承授权。同机对照证明，P38 和 P39 都保留旧显式条目并转换其保护标志；真正自动继承的条目则由 `0x80` 更新为 `0x1200a9`。[子文件对照](p39-maintenance-qualification.json)
- 正式编译退出码为 0，构建前后源码一致。观察器清理了一个已确认归属的 MSVC 遥测进程，保留其原生命周期判定。归档内 3,018 个文件逐一核验；扫描器在两个程序中找到的疑似 token 序列，由上游源码里的六个公开前缀常量组成，两个二进制都保存了六组独立的指针和长度。[归档核验](archive-inspection.json)

## 长图片会话

与完成 Windows 96 图任务的 P36 源码相比，P39 的九个变化文件均位于 Windows 沙箱组件。与公开 v0.6.13 相比，Python 适配器只变更了版本声明。Linux 的 96 图工程、六次自然压缩及两次冷恢复保留原通过记录。Windows 的原任务完成了 96 图、压缩和冷恢复，但当时模型生成的代码仍有一个工程测试失败。原运行保留各自的版本身份；桌面 App 的真实运行由 CADSG 单独验收。[源码与原始结果对照](capacity-lineage.json)

## 模型驱动工程

DeepSeek 在这个已知的 Jinja 回归工程中修改了三个源文件，控制器没有代改业务代码。在允许本机回环且限制外部网络的配置下，独立原始工作树重现了 6 项失败、902 项通过。父线程和冷恢复后同一个线程都实际执行了完整公开测试，各有 908 项通过。独立评测器从干净原件重建，核实 930 个唯一用例全部通过，包含 22 个留出用例，零跳过。

原生子智能体完成了代码审阅，父线程收到了结果。运行中实际完成了网页打开、页内查找和跟随文档链接。OpenRouter 的三笔独立 generation 回读匹配同一固定模型：主任务样本由 DeepSeek 提供，冷恢复和审阅子任务样本由已选择的 Morph 路由提供。原离线测试的阻塞、两次 HTTP 200 后的未终止流，以及随后同一提供方的恢复都保留在记录中。[工程与提供方回执](model-workflow-qualification.json) · [Windows asyncio 实测说明](../../../docs/windows-python-loopback.zh-CN.md)

GitHub 默认下载的安装验证以单独的公开交付回执记录。
