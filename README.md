# Codex CLI + DeepSeek

**保留原生 Codex CLI 的执行机制，把模型接到 OpenRouter 上的 DeepSeek。**

你仍然用 Codex 的终端界面、命令与文件工具、审批和沙箱、会话恢复、MCP、Code Mode 与子智能体。这个项目提供独立安装、模型协议转换和必要的系统适配；模型给出的工具调用仍交给原生 Codex 执行。

*An isolated native Codex CLI distribution for DeepSeek through OpenRouter. The native runtime owns tool execution, permissions, sessions and agent scheduling.*

需要让另一个 agent 调用、控制或排查 CDS，可安装仓库提供的 [`$cds` 操作技能](docs/cds-skill.zh-CN.md)。直接使用 CDS CLI 不依赖这个技能；技能不另建 runtime。

**0.6.7 是共享 Python 协议层的修订。** 它补齐了 JSON Schema 最终输出与客户端工具搜索中间调用的组合：搜索调用继续交给原生 runtime，真正的最终回答仍须通过原 Schema 校验；server 搜索、缺少调用 ID、空回答和无效最终 JSON 不会因此变成成功。原生 agent loop、工具执行、审批、子智能体、沙箱和三平台原生产物均保持原样。新增回归与本版验证结果见 [v0.6.7 发布页](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.7)。

**此前的 0.6.6 已完成 Windows 主路径的独立安装与真实任务验收。** 它继续使用原生 Codex；本轮修复集中在模型流事件、Windows 私有目录、SQLite 长路径和安装打包。发布后，下载文件与摘要以 [v0.6.6 发布页](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.6)为准；具体版本与证据见[本版报告](reports/releases/0.6.6/README.md)。

- 安装版回归：2364 通过，4 个明确的 Windows 不适用项，零失败；2368 个声明用例全部保留，实际加载的是安装产物。
- 真实开源任务：DeepSeek 修复了 Jinja 的分支变量问题；915 个工程测试、908 个独立 held-out 测试通过，原生子智能体参与审查与跟进，实际完成网页打开、查找与链接导航。
- Windows：正常用户已有目录不再被额外要求“更改所有者”权限；SQLite 数据库与日志使用正确的长路径形式；五个原生产物来自同一固定构建并通过真实权限和清理检查。

模型参数预览不会提前触发工具，最终完成项决定调用内容；流失败不会伪装为完成。适配层保留原生工具、审批、沙箱、会话和子智能体机制。旧错误、取消及补测分别记录，本版报告没有把整段历史改写成一次全绿。

## 它怎样工作

```text
你的终端 / 脚本 / 编辑器客户端
             |
             v
      codex-deepseek 独立入口
      独立安装、配置、会话与凭据
             |
             v
      原生 Codex CLI runtime
             |
             +--> 文件与命令、终端 I/O、审批、系统沙箱
             +--> 会话、恢复、压缩、队列、目标、后台进程
             +--> Code Mode、Skills、Hooks、MCP、子智能体
             |
             +--> 模型请求 --> 协议适配 --> 原生 HTTP --> OpenRouter
             |                  ^                           |
             |                  |                           v
             +<-- 模型事件/工具调用 <--------------------- DeepSeek
             |
             +--> 原生 MCP --> codex_web --> 实际网页 / PDF
```

适配层处理请求和事件格式、提供方选择、系统凭据与进程通信。它不另建 agent loop，不替原生 runtime 调度工具、审批命令或管理子智能体；协议失败、重试及已执行工具结果的保留仍走原生机制。联网搜索使用 OpenRouter Exa；打开网页、页内查找和跟随链接通过随包提供的 MCP 文档读取器完成。

这里的“等价”指**固定版本 Codex CLI 的本地 runtime 机制**。DeepSeek 的代码质量、推理水平和模态能力由模型决定；OpenAI 的云服务及账户权益需要相应服务，换模型不会获得这些权益。

## 支持范围与验收状态

本轮新资格主要覆盖 Windows x86_64。macOS/Linux 继续使用原字节、原 producer 回执的 0.6.2 原生资产，共同 Python 适配器升级为 0.6.7；本轮没有新增 Mac/Linux 实机验收。新证据、旧平台资格和复用范围分别列明，不按单元测试数量推断所有平台均重新验证。

| 平台 | 本版原生资产 | 验收范围 |
| --- | --- | --- |
| Windows x86_64 | 0.6.6，新五产物固定构建 | Windows 10 19045 的独立安装、完整安装版回归、实际 helper 允许／拒绝、真实 Jinja 任务及命名空间卸载；见[本版报告](reports/releases/0.6.6/README.md) |
| Linux x86_64 GNU | 0.6.2，保留原字节和来源 | 延用既有 Linux 原生资格；本轮未新增实机验收。需要 glibc、OpenSSL 3 与 libcap；[既有材料](reports/releases/0.6.2/README.md) |
| macOS Apple Silicon | 0.6.2，保留原字节和来源 | 延用既有 macOS 原生资格；本轮未新增实机验收。[既有材料](reports/releases/0.6.2/README.md) |

原生基线是 **Codex `0.164.0-alpha.7`**，提交 `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`。这是上游预发布版本。模型固定为 **`deepseek/deepseek-v4.1-flash-1017`**，不使用会自行变化的 `latest` 别名。其他 CPU、Linux 发行版或未来 Codex 版本不自动继承这里的验收结果；当前 Linux 包也不是 musl 静态包。

v0.5.0 的验收报告分别记录源码与原生产物身份、真实操作效果、原生接口及复杂开发流程。该版本的 Windows Jinja 与 HTTPcore 任务均通过独立业务验收。Jinja 任务通过；Mac/Linux 的 HTTPcore 流程验证了 runtime 的工具、错误传递、审查和恢复机制，但模型改动仍有 held-out 业务测试失败，报告保留 `application_task_passed=false`。**runtime 通过不代表模型生成的代码全部正确。**

上述 v0.5.0 验收保留原始失败与补测身份：Windows 的 Guardian 初次夹具未触发审批，修正受限权限夹具后单独验证；Linux memory-v1 曾遇到提供方临时失败，另有完整成功运行。报告明确标记组合验收，不把原始失败改写成一次全绿。macOS 凭据部分复用同字节组件的既有真实证据，避免再次触发系统钥匙串提示。

## 安装并运行

Python 源码使用以下固定 `v0.6.7` tag。原生归档必须匹配该源码随附的平台 manifest 与摘要：Windows 仍为 `v0.6.6`，macOS/Linux 仍为 `v0.6.2`，不要求 Python 包与 native 版本号相同。先核验 [v0.6.7 发布资产及清单](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.7)；不要把 main 分支当作固定版本。

Windows 原生包为 0.6.6，macOS/Linux 原生包仍为已发布的 0.6.2。安装器会校验平台、recipe、工具链与完整文件摘要，正常安装不需要编译 Rust。已有任务应继续使用自己的不可变入口；并行验收使用新目录和下文的 `--no-activate`。

准备 Git、独立 Python 3.11+ 和有可用额度的 OpenRouter key。不要使用 Codex App 或 ChatGPT App 内置的 Python。安装器下载当前平台的固定原生归档，检查 SHA-256 和完整文件清单，然后建立独立 Python 环境；正常使用不需要编译 Rust。

### macOS

在终端执行：

```sh
git clone --branch v0.6.7 --depth 1 https://github.com/0xZoharHuang/codex-deepseek-runtime.git codex-deepseek
cd codex-deepseek
python3 --version
python3 install.py
export PATH="$HOME/.local/bin:$PATH"
codex-deepseek runtime setup-key
codex-deepseek runtime doctor
cd /path/to/your/project
codex-deepseek
```

`setup-key` 使用系统隐藏输入提示，把 key 保存到 macOS Keychain。`doctor` 会实际发起一次模型请求并核对 OpenRouter 的模型和提供方记录，因此会产生 API 用量。

### Linux

使用同样的源码和安装器。先确保 Python 能创建 venv，并已安装 OpenSSL 3 与 libcap 的运行库。桌面登录会话如需系统存储 key，还需要提供 `secret-tool` 的 `libsecret-tools` 和已解锁的 Secret Service。

```sh
git clone --branch v0.6.7 --depth 1 https://github.com/0xZoharHuang/codex-deepseek-runtime.git codex-deepseek
cd codex-deepseek
python3 --version
python3 install.py
export PATH="$HOME/.local/bin:$PATH"
codex-deepseek runtime setup-key
codex-deepseek runtime doctor
cd /path/to/your/project
codex-deepseek
```

无桌面的 SSH/CI 会话使用下文的进程环境方式提供 key。Linux 包已包含原生 Codex 构建的 `bwrap` 沙箱资源，无需另装或替换项目自己的沙箱。

### Windows PowerShell

以下 `python` 应指向自行安装的**原生 Windows Python**；它和 WSL 中的 Linux 安装是两套环境。优先在正常交互式 Windows 登录中设置凭据。

```powershell
git clone --branch v0.6.7 --depth 1 https://github.com/0xZoharHuang/codex-deepseek-runtime.git codex-deepseek
Set-Location codex-deepseek
python --version
python install.py
$env:Path = "$HOME\.local\bin;$env:Path"
codex-deepseek.exe runtime setup-key
codex-deepseek.exe runtime doctor
Set-Location C:\path\to\your\project
codex-deepseek.exe
```

想在 PowerShell 中使用短命令 `cds`，可执行 `Set-Alias -Name cds -Value codex-deepseek.exe`；需要每次启动 PowerShell 都生效时，将这一行加入自己的 `$PROFILE`。`cds` 与 `codex-deepseek.exe` 使用同一个 runtime，参数不变。

运行目录默认在 `$env:LOCALAPPDATA\codex-deepseek`，命令入口在 `$HOME\.local\bin\codex-deepseek.exe`。`setup-key` 保存到 Windows Credential Manager。某些 SSH 网络登录没有可用凭据库，应使用安全的进程环境，或回到交互式登录设置凭据。Windows 的沙箱设置由原生 Codex 负责，遵循其提示和系统权限要求。

以上 PATH 设置只影响当前终端。需要长期使用时，将用户的 `.local/bin` 加入自己的终端 PATH。无需覆盖现有 `codex` 命令。

### 下载受限或并行验证版本

可以预先下载与当前源码随附的平台清单和 recipe 匹配的原生归档：Windows 为 0.6.6，macOS/Linux 为 0.6.2，再让安装器校验。不要混用旧版本的同名可执行文件、manifest 或 recipe：

```sh
python3 install.py --native-archive /path/to/codex-deepseek-native-0.6.2-aarch64-apple-darwin.tar.gz
```

另外两份文件名是 `codex-deepseek-native-0.6.2-x86_64-unknown-linux-gnu.tar.gz` 和 `codex-deepseek-native-0.6.6-x86_64-pc-windows-msvc.tar.gz`。Windows 使用 `python install.py --native-archive "C:\path\to\archive.tar.gz"`。离线 Python 依赖可通过 `--wheelhouse <目录>` 提供；每份依赖仍必须符合随源码提供的 hash lock。

`--root <新安装目录> --no-activate` 保留当前命令入口，但仍会初始化新目录中的 `codex-home`。并行验证应使用此前不存在的新 root 和独立 home。直接运行不可变入口时，要显式设置相应 `CODEX_DEEPSEEK_HOME`；Windows 也可使用交付时已绑定 home 的绝对入口。不要假定通用入口会覆盖继承的 home 设置。

离线或并行安装可以显式提供匹配当前平台和 recipe 的 `--native-manifest <本地清单>` 与 `--native-archive <本地归档>`；这些参数仍执行完整校验。先准备此前不存在的新 root，再按[独立安装说明](docs/release-0.6.0-candidate.zh-CN.md)操作。

## Key、配置与 Codex App 隔离

优先使用 `runtime setup-key`。系统存储名称为 `ai.codex.deepseek.openrouter`，账户为 `default`。启动进程中的 `OPENROUTER_API_KEY` 如已设置，会优先于系统存储；SSH/CI 应由自己的秘密管理系统注入，不要把 key 写进命令参数、仓库或配置文件。

macOS/Linux 需要临时输入一次 key 而不落盘时，可用独立 Python 隐藏读取，再启动 CLI；示例中的命令行本身不含 key：

```sh
python3 -c 'import getpass,os,subprocess; os.environ["OPENROUTER_API_KEY"]=getpass.getpass("OpenRouter key: "); raise SystemExit(subprocess.call(["codex-deepseek","runtime","doctor"]))'
```

Windows SSH/CI 同样读取启动进程中的 `OPENROUTER_API_KEY`；由 CI 的 secret 注入或受控父进程设置，不要用包含明文 key 的 PowerShell 命令或 `setx` 持久化。

原生 `login` / `logout` 管的是 Codex 自己的账户存储，不负责配置这里的 OpenRouter key。提供方 key 在启动原生工具前从环境移除；模型操作使用独立的本地通信凭据。

| 内容 | 默认位置或行为 |
| --- | --- |
| macOS/Linux 安装根目录 | `~/.local/share/codex-deepseek` |
| Windows 安装根目录 | `%LOCALAPPDATA%\codex-deepseek` |
| 不可变版本 / 原生资产 | `<安装根>/releases/` / `<安装根>/native/` |
| 默认配置与会话 | `<安装根>/codex-home/`，其中 `config.toml` 使用原生配置格式 |
| 提供方回执 | `<home>/provider-state/adapter.sqlite` |
| 网页缓存 | `<home>/web-cache/pages.sqlite` |
| 0.6.0 可选图片配置 / 私有账本 | `<home>/provider-state/image-transport.json` / `<home>/provider-state/image-transport/` |

入口会清除继承的 App IPC/会话身份，使用独立 `CODEX_HOME`，拒绝与 `~/.codex` 重叠的状态或跟踪目录。由这个 runtime 启动的命令若再次调用 `codex`，profile 内的命令入口会让它继续使用本安装。不要自行把命令硬编码到另一份 Codex/App 的可执行文件。

多个项目需要完全分开的运行状态时，可另外设置 `CODEX_DEEPSEEK_HOME`：

```sh
CODEX_DEEPSEEK_HOME="$HOME/.local/share/codex-deepseek/project-a-home" codex-deepseek
```

PowerShell 对应 `$env:CODEX_DEEPSEEK_HOME = "$env:LOCALAPPDATA\codex-deepseek\project-a-home"`，再执行 `codex-deepseek.exe`。这些 home 分开保存会话、MCP 凭据和服务状态；OpenRouter 提供方 key 仍按上述系统存储或进程环境读取。

项目规则、项目工具和共享 `~/.agents/skills` 保留原生行为；独立 home 不是虚拟机。为保持 App 隔离，`codex-deepseek app` 会被明确拒绝。连接外部远程 app-server 时，服务器的配置、身份与权限由该服务器管理，不能用本机隔离来担保远端。

## 日常使用与能力证据

```sh
codex-deepseek -C /path/to/project -s workspace-write -a on-request
codex-deepseek exec "修复这个项目的问题，运行相关测试并解释结果"
codex-deepseek resume --last
codex-deepseek fork --last
codex-deepseek review --uncommitted
codex-deepseek agents
codex-deepseek --search "查阅官方文档，打开原文、查找相关段落，再跟随链接核实"
codex-deepseek mcp list
codex-deepseek app-server
```

外层 agent 或脚本启动独立 worker 时，固定实际入口、cwd 和 `CODEX_DEEPSEEK_HOME`，通过 stdin 传入任务。例如在已信任的 Git 项目中：

```sh
codex-deepseek -a never -s workspace-write exec --json -C /path/to/project --output-last-message /path/to/last-message.txt -
```

`--json` 属于 `exec` 层；控制器应保存 `thread.started` 的线程 ID 和原 home，将 stdout 按 JSONL 解析、stderr 单独保存；同时确认最终 `turn.completed`、进程退出 0，并独立检查实际文件和测试结果。遇到 `turn.failed`、错误事件、非零退出或缺少终态时保留诊断，不把日志混入 JSON 流。最后文本或退出 0 本身不等于任务正确完成。`-a never` 禁止申请新审批，不授予额外文件或网络权限；需要审批回调、运行中 steer 或 interrupt 时，使用持有同一连接的 `app-server`。独立 CLI worker 不会自动成为父 Codex 的原生子智能体；CDS 内的子智能体仍由原生协作工具管理。恢复参数位置和回执语义见[委派指南](docs/delegation-and-local-tools.zh-CN.md)。

需要从某个 turn 前分出恢复线程时，见[固定原生历史恢复流程](docs/native-history-recovery.md)：分页历史需要原 home 的索引，只继续新 fork 子线程；可读 rollout 路径不等于全新 home 的分页迁移。

安装时的默认权限是 `workspace-write` 和 `on-request`。沙箱、人工审批、Guardian 自动审查、Hooks 信任等仍使用原生实现与配置；不要为让某个命令通过而把全局权限改成无限制。子智能体由任务中的原生协作机制创建和管理，`agents` 用来浏览共享本地 daemon 的会话。

下表记录 v0.5.0 三平台的代表性能力与取证入口；Windows 的宿主、客体和组合证据见[Windows 平台报告](reports/platforms/x86_64-pc-windows-msvc/runtime-qualification.json)。报告涵盖指定场景，不能视为穷尽所有输入和故障。

| 能力 | 实现与验证要点 | 证据入口 |
| --- | --- | --- |
| 文件、命令与终端 | 原生文件修改、命令退出码、PTY 输入/尺寸、后台会话、Ctrl-C 后继续、原生停止和实际进程清理 | [Mac runtime](reports/platforms/aarch64-apple-darwin/runtime-qualification.json)、[Linux runtime](reports/platforms/x86_64-unknown-linux-gnu/runtime-qualification.json) |
| 沙箱与审批 | 原生命令沙箱、允许/拒绝的真实文件效果、人工/Guardian 审批、网络权限撤销；Linux 捆绑 bwrap 校验 | 同上，`native_source_audit`、`acceptance_gates`、`linux_bundled_sandbox_resources` |
| 会话与协作 | 保存、恢复、fork、压缩、原生子智能体生命周期；真实代码编辑后的中断与冷恢复 | 同上，`real_work` 与 `native_terminal_lifecycles` |
| Code Mode 与工具协议 | 原生直接/混合工具、v1/v2 协作、动态发现、命名空间、自定义工具、流式参数、结构化结果校验 | 同上，`acceptance_gates` |
| 图像与结构化结果 | 原生图像附件/工具输入链路、JSON Schema 输出验证；图像理解和任务正确率由模型决定 | 同上，features 验收与协议恢复证据 |
| MCP、Skills、Hooks | 原生加载、权限、调用和生命周期；OAuth/PKCE、冷启动、File/Keyring/Auto/Secrets 凭据隔离 | 同上，标准验收及独立凭据证据 |
| daemon、队列、目标与记忆 | 原生本地调度和持久化；服务崩溃恢复保持端口，在途未知结果保留为未知 | 同上，相关标准验收和本地方法 |
| 联网搜索与文档导航 | Exa 实时搜索；实际 open → find → click 链路、页面引用冷恢复、真实 HTTP 与取消 | 同上，browser/search 验收及 `real_work.web_research` |
| 客户端接口 | stdio、WebSocket、适用平台的 Unix socket；全部原生 Schema 对照与逐方法正向效果 | [Mac 方法矩阵](reports/platforms/aarch64-apple-darwin/runtime-method-matrix.json)、[Linux 方法矩阵](reports/platforms/x86_64-unknown-linux-gnu/runtime-method-matrix.json)；[Schema 对照](reports/platforms/aarch64-apple-darwin/native-schema-comparison.json) |

网页工具 `codex_web.open` 读取实际 HTML、文本或 PDF，返回行号、原始 URL、内容摘要与链接编号；`find` 查找同一已读取文档，`click` 跟随其中真实存在的链接。引用在同一 home 内持久保存。它不执行网页 JavaScript、不继承浏览器登录态；搜索摘要不会被当作已成功打开原文。

默认文档读取面向公共 HTTP(S) 地址，检查地址和重定向，缓存有容量限制，分页/截断会显式说明。它使用原生 MCP 的权限路径；MCP、托管搜索和 shell 的联网权限是不同路径。需要停用它可在独立 `config.toml` 中设置：

```toml
[mcp_servers.codex_web]
enabled = false
```

## 可选图片传输

这项功能默认关闭。启用后，适配器可将体积较大的请求中的部分图片原字节上传为私有 Files 对象，再把对应 HTTP 请求位置改为 file reference。原生历史仍保留原图，顺序、detail 与其余图片保持原样。

```text
原生请求（原图历史保留）
    -> 按字节预算选择图片 -> 私有上传 / 原图内联混合请求
    -> 原生 HTTP -> OpenRouter
    -> 确认消费结束 -> 按所有权清理并读回不存在
```

最多使用 16 个文件引用位置；重复图片的每个引用位置也计入限制。请求仍超过配置的字节预算时会显式失败，继续由原生机制处理压缩与重试。这个预算不是 token 上限，也不承诺任意大小的请求都能通过。图片传输沿用下表的模型路由，不增加 fallback。

使用前请阅读[配置与维护命令](docs/release-0.6.0-candidate.zh-CN.md)：缓存绑定所属 home 和 credential scope，不能靠复制账本接管另一 profile 的云端文件。取消、未知响应或删除未确认时会保留待处理状态。删除本地缓存、禁用功能都不等于完成远端清理。

## 为什么有时不是官方 DeepSeek 路由

所有请求仍固定同一个 DeepSeek 模型。提供方在发送前按能力选择，优先级从上到下：

| 请求条件 | 提供方 | 回执原因 |
| --- | --- | --- |
| 有工具，且明确要求 `parallel_tool_calls=false` | `primeintellect` | `explicit_serial_tools` |
| JSON Schema 结构化输出 | `morph/fp8` | `strict_json_schema` |
| 同时包含思考、托管网页搜索，以及原生 peer/未完成续接/缺少完成证明的模型历史 | `morph/fp8` | `native_peer_or_continuation_with_hosted_tools` 或 `unattested_native_history_with_hosted_tools` |
| 其余普通请求 | **官方 `deepseek`** | `official_default` |

每个请求设置 `provider.only`、`allow_fallbacks=false`、`require_parameters=true`。提供方报错不会触发适配层换路由重试；原生重试和取消仍由原生 runtime 负责。上面几种组合来自实际提供方差异复现，见 [互操作说明](upstream/README.md)。

适配层保留调用 ID、工具输出和能表达的思考内容；不靠丢历史来强行通过官方路由。无法表达的语义、无效工具或不符合 Schema 的结果会显式报错。原生压缩移除相关历史后，普通请求可以重新使用官方路由。OpenAI 与 DeepSeek 的思考档位并非同一模型能力，档位按适配器的明确映射发送。

OpenAI 的加密远程状态、专属缓存搜索、Realtime/图片生成模型、ChatGPT Apps/Marketplace 与云任务账户等不包含在本地 runtime 等价范围内。Exa 不支持的缓存、位置偏好或图片搜索后端语义会返回明确兼容性错误。普通本地后台命令、队列和子智能体并不因此被替换或禁用。

## 诊断、升级与清理

先使用以下命令区分版本、配置和实际模型连接：

```sh
codex-deepseek --version
codex-deepseek runtime status
codex-deepseek runtime doctor
codex-deepseek doctor
```

`--help` / `--version` 不需要 key；`runtime status` 查看本发行包的绑定和目录，`runtime doctor` 做真实提供方验证，最后一条 `doctor` 是原生 Codex 的诊断命令。不要公开贴出含私人路径、项目正文或 token 的原始日志。显式开启 `CODEX_DEEPSEEK_TRACE_DIR` 会记录敏感请求上下文，只用于受控排查。

| 现象 | 处理 |
| --- | --- |
| 找不到命令 | 检查 `.local/bin` 是否在当前终端 PATH，或直接使用不可变版本入口。 |
| 缺少 key / Windows 凭据库不可用 | 运行 `runtime setup-key`；SSH/CI 用安全进程环境。检查是否有旧的 `OPENROUTER_API_KEY` 覆盖系统存储。 |
| 原生产物或缓存摘要不符 | 保留异常文件，重新取得匹配版本的源码和归档，交给安装器验证；不要关闭摘要检查。 |
| Linux 缺少动态库或 venv | 安装与系统匹配的 glibc/OpenSSL 3/libcap 运行库及 Python venv 支持；当前验证目标是 Ubuntu 24.04 GNU。 |
| 模型未宣告 Code Mode 元数据的提醒 | 原生提醒不会据此禁用工具，纯文本完成也不能证明工具运行。不要为消警强填 catalog 的 tool_mode，否则可能覆盖 profile 的 Direct 选择。[条件、选择顺序及真工具验证方法](docs/code-mode-metadata-warning.zh-CN.md)。 |
| `Reconnecting... undeclared tool` | 核对本次实际声明的工具集合与调用路径；目录、历史或 Code Mode 内的工具名不代表顶层已开放。保持工具白名单，参见[受控原生重试记录](reports/protocol/undeclared-tool-retry-20261025.json)。该记录覆盖一次未知调用前后已执行工具的保留，不承诺模型再次发出新操作时的业务幂等。 |
| 修改网络/凭据环境后提示已有运行者 | 结束该 home 的工作与 daemon，或换一个独立 home；避免新旧环境混在同一进程中。 |
| 子任务 DNS 失败 / `curl` 退出 6 / HTTP 000 | 先核对实际子任务网络策略；宿主能解析不等于沙箱能联网，也不能据此断言远端宕机。见[诊断指引](skills/cds/references/diagnostics-and-verification.md#shell-network-errors-do-not-prove-a-remote-outage)。 |

已发布版本的升级先在新 root 和独立 home 中验证，再切换入口；确认该 home 的任务、子进程和 daemon 都已结束后才做同 home 变更。单独停止 daemon 不等于所有工作已退出。

验证新版本时使用[隔离候选流程](docs/release-0.6.0-candidate.zh-CN.md)，验收前不切换稳定入口。新的 Windows sandbox 资源按 home 身份独立管理，旧 `.sandbox`、`.sandbox-secrets`、`.sandbox-bin` 不能直接复制到新 home；也没有自动完成旧 home 迁移的命令。

原生命令用于管理选定 home 的 daemon：

```sh
codex-deepseek app-server daemon stop
codex-deepseek app-server daemon update --from-cli --yes
```

使用本项目的安装器升级；通用上游更新路径会被拒绝，以免移除提供方隔离。隔离候选的回退是结束候选自己的工作后，继续使用保留的稳定入口与原 home。未来同 home 升级需要单独备份与恢复步骤，并保留 home 根目录身份；不承诺状态格式可任意降级。图片账本与未知消费回执应保留到远端清理得到确认。

卸载时先结束所选 home 的任务、子进程与 daemon。Windows 的已安装 release 提供 `sandbox uninstall`：使用该 release 的实际入口和该 home 原有的 `CODEX_DEEPSEEK_HOME`，先查看 `sandbox uninstall --help`，再按其中权限与所有权要求执行；它清理本 home 的沙箱账户和网络规则；目录和用户数据保留，本 home 管理的 ACL 授权由原生撤销流程回收，其他权限不应被重置。不要借旧版的 shared-sandbox 命令清理其他 namespace。

程序移除与会话删除分开处理：确认用户命令入口确由这个安装管理后，再移除该入口及不再需要的安装 root / 不可变版本；会话、配置、图片账本和必要回退材料先保留或备份。macOS/Linux 没有该 Windows 沙箱卸载命令，同样只移除自己的入口和选定安装；PowerShell 的 `cds` alias 另从自己的 profile 中删除。项目没有一条通用 `runtime uninstall` 命令代做这些选择。

取消一个采样轮次不等于清空所有后台工作。后台终端和已发出的 MCP 调用遵循原生生命周期；要清理应结束其实际拥有者，不能仅看到界面停止就认定子进程全部退出。上游直接遭到强杀也存在自身清理边界，报告保留这些事实。

确认没有活跃会话、daemon 或构建进程后，可清理本项目的构建缓存、重复下载和临时测试环境。保留正在使用的版本、必要回退包、用户会话和关键验收回执；不要批量删除 `~/.codex`、其他 WSL 发行版或其他项目。更完整的运行与验收步骤见 [稳定方案](docs/STABLE_PLAN.zh-CN.md)。

## 源码与许可证

项目源码采用 Apache-2.0。各平台实际使用的原生补丁顺序与身份由 [runtime-overlays.json](native/runtime-overlays.json) 记录；本版在既有 Windows 隔离修复后追加关键读取路径的 DENY 就绪检查，Unix 仍保留 0.6.2 的目标 recipe 与原构建回执；构建和打包入口是 `build_native.py` / `package_native.py`。源码审计对照固定上游清单与补丁重放；外部 Cargo 依赖版本保持锁定。不同编译环境可能产生不同二进制摘要，不宣称跨机器构建天然逐字节一致。

包内保留 Codex、ripgrep、Rust/Python 依赖的许可证与归属；Linux bubblewrap 同时提供原始许可证、对应源代码和构建出处，Windows 启动器保留 distlib 许可证。详见 [第三方说明](THIRD_PARTY_NOTICES.md) 和 [LICENSE](LICENSE)。[上游补充材料](upstream/README.md) 是可审查的补丁与复现说明，不代表已被 OpenAI 或 OpenRouter 接受。
