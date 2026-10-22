# Codex CLI + DeepSeek 的稳定运行方案

本项目保留 Codex CLI 的原生运行时，用独立安装、协议转换和平台适配，把模型请求接到 OpenRouter 上固定版本的 DeepSeek。模型质量、OpenAI 账户权益和专属云端服务不属于运行时等价的承诺。

平台是否验收完成，以发布版本随附的 `reports/platforms/<target>/runtime-qualification.json` 为准。源码里存在某个平台的实现、原生二进制编译成功，或某个测试子集通过，都不能替代整个平台的验收报告。

## 已验收的固定版本

v0.5.0 使用 Codex `0.164.0-alpha.7` 与 DeepSeek `deepseek/deepseek-v4.1-flash-1017`。原生基线仍是上游预发布版，因此 GitHub release 也保留 prerelease 标记。

| 平台 | 本地方法范围 | 当前交付的完整离线测试 | 结论入口 |
| --- | --- | --- | --- |
| Windows x86_64 | 131 项，含实际双模式沙箱 | 666 通过；1 项 Linux 专用 | [Windows 报告](../reports/platforms/x86_64-pc-windows-msvc/runtime-qualification.json) |
| macOS arm64 | 129 项，通过明确组件谱系关联 | 653 通过；12 项平台不适用 | [macOS 报告](../reports/platforms/aarch64-apple-darwin/runtime-qualification.json) |
| Linux x86_64 GNU | 129 项，通过明确组件谱系关联 | 654 通过；11 项平台不适用 | [Linux 报告](../reports/platforms/x86_64-unknown-linux-gnu/runtime-qualification.json) |

Windows 标准门保留原 29 项通过与独立 Guardian 补测；Linux 保留 memory-v1 的提供方失败和独立成功补测；macOS 的四项凭据机制复用未变组件的原始实测，加上当前 File/Ephemeral 桥接。这些是明确组合的证据，不是一次全绿运行。

[发布源码谱系](../reports/release-source-lineage.json) 将实测 94c、069、3a 的精确 Git 对象与最终发布源码关联。最后产品差异只有 README 内容和 Windows manifest 的 CRLF→LF，JSON 对象、全部 runtime Python、原生归档、catalog 与依赖完全相同。历史精确字节用 `git archive <commit>` 恢复；正常 checkout 会按 `.gitattributes` 统一换行符。

## 实际执行路径

```text
你的终端
    |
    v
codex-deepseek 独立入口
    |  独立安装目录 / CODEX_HOME / 进程身份 / 系统凭据
    v
原生 Codex CLI runtime
    |
    +--> 命令、文件修改、审批、沙箱
    +--> 会话、恢复、压缩、队列、目标、后台进程
    +--> Code Mode、MCP、Skills、Hooks、子智能体
    |
    +---- Responses 请求 ----> 协议适配 ----> 原生 HTTP ----> OpenRouter
    |                             ^                           |
    |                             |                           v
    +<--- 原生事件 / 工具调用 ------+                         DeepSeek
    |
    +--> 原生 MCP 调度 --> codex_web --> 实际网页 / PDF
```

模型提出的工具调用仍交给 Codex 的原生工具管理器执行。适配层不调度子智能体、不代做审批、不运行模型提出的命令，也不自行重试或重放已经发出的工具操作。网页读取是使用官方 MCP SDK 注册的工具服务，仍由原生 MCP 机制发现、授权和调用。

## 安装、运行和隔离

使用发布版本中与系统匹配的源码、原生归档和哈希清单。安装器按内容标识保存不可变版本；不要直接修改已经安装的 Python 环境或原生二进制。

```sh
python3 install.py --native-archive /path/to/native-release.tar.gz
codex-deepseek runtime setup-key
codex-deepseek runtime doctor
cd /path/to/project
codex-deepseek
```

Windows 使用独立 Python 执行同一个安装器，入口是 `codex-deepseek.exe`。默认安装目录为 `%LOCALAPPDATA%/codex-deepseek`；macOS/Linux 默认为 `~/.local/share/codex-deepseek`。需要并行验证候选版本时，使用 `--root <独立目录> --no-activate`，再直接执行该目录下不可变版本的入口。

`CODEX_DEEPSEEK_HOME` 可以指定另一个独立配置与会话目录。路径不能与 `~/.codex` 重叠。入口会清除继承的 Codex App IPC 和会话标识；项目内再次调用 `codex` 时，独立 profile 中的命令入口会保持在这套安装内。Windows 使用标准 Python `.exe` 启动器，参数和路径不会交给 CMD 展开。

`runtime setup-key` 保存的是本项目使用的 OpenRouter 凭据：macOS Keychain、Windows Credential Manager，或 Linux Secret Service。原生 `login` 命令仍遵循原生账户存储语义，不负责替换这里的模型提供方凭据。SSH/CI 可从安全的父进程环境提供 `OPENROUTER_API_KEY`；该值在启动原生工具前会被移除。Windows 网络登录会话没有可用凭据库时，应使用进程环境或交互式 Windows 登录会话。

`runtime doctor` 会实际运行原生模型请求，并读取 OpenRouter 的生成记录核实模型和提供方。它不是仅检查 TCP 连接或根据配置文件推断接通。

## 模型与协议约束

使用固定模型 `deepseek/deepseek-v4.1-flash-1017`，不使用自动变化的 latest 别名。普通请求优先选择官方 `deepseek` 提供方。严格 JSON Schema、明确串行工具调用、带原生子智能体或不完整历史的思考加联网组合，按已验证的协议条件选择兼容的同模型提供方。

每次请求都固定模型，并明确设置 `provider.only`、`allow_fallbacks=false`、`require_parameters=true`。选择发生在请求发出前，路由及原因会写入本地回执。提供方报错不会触发适配层改换提供方重试；原生重试、取消和续跑机制继续负责请求生命周期。

历史中的调用 ID、工具输出和可表达的思考文本不会为了通过接口而被丢弃。只有验证过的完成响应才记录完成指纹；缺乏完成证据的历史走明确的兼容路径。无法表达的语义、无效参数或不符合 Schema 的完成结果会显式失败，交给原生机制处理。

Code Mode 的可用性、原生模式选择和模型元数据是不同事项。当前 catalog 不强制 `tool_mode`，使用户可以切换原生直接工具、Code Mode 及协作模式。原生界面因此可能显示模型未宣告 Code Mode 元数据的提醒；提醒会保留，不通过隐藏告警或强制模式来制造通过。

## 平台资源与生命周期

原生基线固定为 Codex `0.164.0-alpha.7`，提交 `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`。原生包包含匹配的 Code Mode Host、ripgrep、HTTP 辅助程序及许可证。包的目录结构由该版本上游的打包验证器核对。

Linux 额外包含原生构建的 `codex-resources/bwrap`。其最终字节先被哈希，再将摘要嵌入 CLI；验收同时检查强制使用捆绑资源、实际文件权限限制和篡改拒绝。当前 GNU 构建环境是 Ubuntu 24.04，依赖 glibc、OpenSSL 3 和 libcap，不宣称为 musl 静态包。

Windows 包含原生沙箱 setup、runner、service 和诊断辅助程序。进程使用内核创建时间识别，避免误杀被复用的 PID；文件锁使用系统文件锁，私有目录使用受保护 ACL。Ctrl-C/Ctrl-Break 保留 Windows 原生控制台广播语义，不翻译成另一种信号。

网关工作进程崩溃后，可以在同一监听端口恢复服务；未知的在途模型结果仍标为未知，不重新执行工具。采样轮次中断与后台命令、MCP 请求退出是不同事件，按同版本原生行为验收。关闭拥有者后的清理，另外用实际进程、连接和文件效果证明。

## 完成的判据

发布验收必须覆盖下列互补证据：

1. 完整原生源码清单与补丁重放，确认保护范围中的会话、工具处理器、调度和沙箱实现保持上游内容。
2. 同版本官方二进制与本项目二进制的全部 456 份协议 Schema 比较。
3. 174 项客户端方法的明确适用范围；macOS/Linux 要求 129 项本地接口，Windows 要求 131 项，其他条目给出原生账户、后端或平台边界的实际证据。
4. 标准场景、实际收集到的全部离线测试、逐方法的状态或副作用，以及对应的真实请求/响应配对。
5. 真终端输入输出、窗口尺寸、取消、正常退出、同一会话冷恢复；普通管道输出不替代终端能力。
6. 文件、系统钥匙串、自动和加密凭据模式中的实际 OAuth、冷启动、跨 profile 隔离与原生清理。
7. Jinja 与 HTTPcore 的复杂真实开发工作流：实际工具调用与文件效果、成功和失败输出的完整传回、原生审查、源码修改后的中断续跑，以及独立测试确实加载工作区代码的证据。保留全部业务测试结果，包括隐藏测试、三轮 TCP 压力测试和真实 TLS 取消；这些测试验证的是被修改的第三方项目，不能与 Codex 自身的中断、网络策略或沙箱验收混为一谈。
8. 被验收父轮次中的实际网页打开、同一引用的正向查找、跟随真实链接到另一文档，绑定明确的 thread、turn 和原始调用。其他轮次、审查者的浏览或搜索摘要不能代替。

验收结论分开记录 runtime 机制与模型任务产出。第三方项目的业务测试失败、模型未完成修复或控制器达到时间预算，都保留原始失败状态，不能改签为任务成功。runtime 机制必须另从原始请求、响应、文件效果、会话和进程状态逐项核验；不能仅删除失败条件或统计工具调用次数后宣布通过。控制器主动中断也必须有对应的原生中断回执和后续恢复证据，不能把未知退出归类为正常中断。

完整通过的开发任务证明正常完成路径；包含真实失败与恢复的开发任务证明错误传递和恢复路径。两者共同补充逐接口与平台验收。模型生成代码的正确率不属于 runtime 等价承诺，任何尚未验证的 runtime 能力仍是发布阻断项。Windows 沙箱、凭据、审批和进程清理的实际结果分别记录在最终平台报告中。

宿主 Windows 的复杂任务可明确使用 full-access 模式，以避免修改用户已有沙箱账户；这种任务结果必须配合同一源码、依赖、catalog 和原生产物的隔离 Windows 客体验证，才能覆盖沙箱能力。客体的正向权限结果不能只由 readiness 或 setup 成功代替。

## 委派与运行中控制

子 CLI 的权限以实际启动参数为准，不能从父任务的 full-access 状态推断。`queue` 进入后续 turn 队列；运行中追加输入使用原 app-server 的 `turn/steer`，明确停止则使用 interrupt 并核对后台进程。原版 REA/Ghidra 的已验证启动方式及 exec resume 用法见[委派和本地工具指南](delegation-and-local-tools.zh-CN.md)。

供外层 agent 和 CDS 自身按需使用的 [`$cds` 操作技能](cds-skill.zh-CN.md) 沿用官方 SKILL.md 格式；它只提供操作知识和只读安装检查，不增加工具调度或会话生命周期实现。技能和文档的更新不重写已发布 v0.5.0 的 tag 或平台证据。

## 更新、回退和清理

升级使用新不可变目录。当前 profile 仍有原生工作时，不替换它的提供方服务；先结束工作，或使用另一个独立 home。独立 daemon 更新使用原生 `app-server daemon update --from-cli --yes`，保留本发行包的模型提供方边界。

保留已验证的运行版本及必要回退材料。构建缓存、重复下载和临时虚拟机在停止其确切拥有者后清理；失败证据先压缩、逐文件读回校验，再删除展开副本。项目源码、用户会话、关键回执和 Codex App 数据不属于批量清理对象。实际释放空间以文件系统读数为准。
