# 原生能力选择与发现

需要选择 CDS 的执行入口、工具或扩展机制时读本参考。安装、凭据、权限配方和运行中控制由主 skill 路由到相应参考；不要为普通任务加载全部材料或做一次完整发布验收。

以下 `<CDS>` 指用户选定的实际 `codex-deepseek` 入口。`cds` 可能只是 shell alias；自动化应沿用已解析的可执行文件。用户要求由 CDS 完成的工作，应交给 CDS 原生执行，再核对结果，不要在外层代做后称为 CDS 成功。

## 先发现当前能力

需要哪一类功能，就读取该类帮助；已经核实的信息不必重复查。

```text
<CDS> --version
<CDS> --help
<CDS> exec --help
<CDS> review --help
<CDS> app-server --help
```

接口开发或版本差异排查时，才按当版帮助导出原生 schema，例如本参考基线支持：

```text
<CDS> app-server generate-json-schema --experimental --out <owned-output-dir>
```

有原 app-server 连接时，可按实际 schema 查询 `model/list`、`modelProvider/capabilities/read`、`experimentalFeature/list` 及相关扩展目录。模型可调用的工具，以本轮原生工具目录、工具描述和动态发现结果为准；不要从历史报告数量拼一套固定工具清单。schema 中存在方法，不等于当前 feature、平台、模型、账户或权限已允许执行。

## 按工作类型选择

| 工作 | 首选原生入口/机制 | 会影响选择的边界 |
|---|---|---|
| 交互式开发 | `<CDS>` 的 TUI | 适合持续讨论、人工审批、查看过程。快捷键和 slash commands 查当前 TUI 帮助，不假定不同版本相同。 |
| 自动化的一次任务 | `<CDS> exec`，需要机器读取时用 `--json` | 显式确定 cwd、profile 和当前授权的权限。`exec --json` 是简化事件格式；需要原始 turn ID/steering 的控制器用 app-server。 |
| 审查变更 | `<CDS> review --help` 后选择工作区、base 或 commit 范围 | 审查和修复是不同请求。未授权修复时先给有证据的缺陷；不要把模型的“没有问题”当成测试通过。 |
| 会话延续、分支、长期控制 | 原生 resume/fork、app-server thread/turn 生命周期 | 使用准确 thread ID 与原 owner。queue、active-turn steer、interrupt 的语义不同；需要运行中控制时读相应专门参考。 |
| 项目规则与计划 | 当前 cwd 的原生项目指令、计划工具/协作模式 | 保留适用的 AGENTS.md 与用户边界；按任务需要选择计划模式，不把计划或状态更新当成执行结果，也不为方便而覆盖全局指令。 |
| 文件阅读与修改 | 本轮原生文件/命令/patch 工具；客户端确需文件操作时查 `fs/*` schema | 使用真实 cwd 和明确路径。检查实际 diff/文件效果；没有 `cds read-file` 之类帮助项时不要编造 CLI 子命令。 |
| Shell、构建、测试、长命令 | 本轮原生命令工具及其 continuation；客户端需要无模型命令时查 `command/exec` | 保留 stdout、stderr、退出码、process/session ID。返回“仍运行”时按该工具的等待接口继续收取，不把启动当完成。 |
| PTY 与显式进程会话 | 原生 PTY；`command/exec/*` 或实验性的 `process/*` | 两组 API 的 ID 与生命周期不同。`command/exec` 采用 server sandbox；`process/spawn` 是显式的沙箱外进程接口，不能拿来自动绕过任务限制。resize、stdin、terminate 必须作用于匹配的会话。 |
| Code Mode | 当前模型上下文实际暴露的原生 Code Mode 工具和说明 | 它执行工具编排，不是普通 shell。遵循当版执行/等待/输出规则，保留真实工具返回值；不要强制改模式以掩盖不支持或绕过审批。 |
| 并行子智能体 | 原生协作工具、fork/消息/等待/回收机制 | 按可分离任务分工，传清输入、产物和权限；结果要回读与整合。不要拿无关 App 聊天、另一个裸 CLI 或手写调度器冒充原生子智能体。 |
| 目标与持续任务 | 当前已支持的 goal 工具或 `thread/goal/*` | 遵循当前工具对创建、预算、暂停和完成的要求，保留用户的停止条件。不要把普通一次请求自动升级为后台持久任务。 |
| MCP 工具与资源 | `<CDS> mcp --help`；已授权连接上的原生 MCP 发现、tool/resource 调用 | 区分配置存在、server 启动、工具发现、授权和实际调用。`mcpServerStatus/list`、resource/tool API 的字段用当版 schema；目录列出工具不是调用成功。 |
| 插件与 Apps/连接器 | `<CDS> plugin --help`、原生插件 UI/目录，已有授权的连接器 | 插件是分发/配置单位，不自动赋予内含工具权限。官方标为开发中的 plugin API 不直接当稳定生产接口；安装、启用和外部调用各自按用户任务授权。 |
| Skills | 原生技能目录、`/skills`/`$skill`、适用时的 `skills/list` | 先读被选中的 SKILL.md，再按需读 refs。技能提供工作流，不授予额外权限，也不替换 native 工具；缺失时不要默默假装已使用。 |
| Hooks | 当前 hooks 配置、`hooks/list` 与触发结果 | Hook 会实际执行代码。区分“发现配置”“信任/启用”“本轮确实触发”；不要为完成任务无条件关闭 hooks 或绕过信任。 |
| 记忆、上下文压缩、历史 | 原生 memory/compaction 与 thread history | 会话持久化、上下文压缩、memory 抽取是不同机制。需要记忆能力时核对实际配置/产物/后续使用；不要手改数据库或伪造 memory 记录。日常任务无需逐次重验全部后台记忆流程。 |
| 联网检索与文档阅读 | 当前暴露的 search 与原生 MCP 文档工具 | 本项目搜索经 OpenRouter 搜索路径；网页/PDF 打开、页内查找、链接跳转由随包 MCP 文档读取器提供。用户要求 open/find/click 时执行相应动作；搜索摘要不替代打开或跟随链接。 |
| 图片、附件、结构化输出 | 当版文件/媒体工具、`inputModalities`、原生 output-schema 支持 | 工具能读取文件不等于所选模型具备全部媒体理解或生成能力。需要 JSON Schema 时保留约束与失败；不要丢字段、改成随意文本后称满足结构化输出。 |

这里的 API 名是发现入口，不是可无条件调用的固定接口清单。参数、实验开关与当前连接能力以原生 schema 和实际响应为准。[官方 app-server API](https://learn.chatgpt.com/docs/app-server#api-overview)

## 几个容易混淆的执行面

- 模型发起的命令/文件工具遵循该 thread 的原生权限。客户端的 `thread/shellCommand` 明确在沙箱外执行，且不继承 thread sandbox；不要把它或 `process/spawn` 当作受限工具失败后的自动替代。
- Code Mode cell、命令的 session、显式 process、thread 和 turn 是不同标识。中断采样不会自动证明所有后台进程都停止；需要清理时使用实际 owner 的原生接口，并限定于任务拥有的对象。
- 网页文档读取器不是完整的登录态浏览器或桌面 UI 自动化。需要页面交互、登录态或客户端渲染时，确认另有已授权的对应工具，不把文本 `click` 当成任意浏览器鼠标操作。
- 原生 CLI 子命令存在，并不使 OpenAI 云任务、账户服务、专属模型功能或第三方服务授权自动可用。先区分 runtime、provider/model、账户、平台和权限问题，再选择解决路径；不要偷偷切换到另一套产品完成后仍标为 CDS。

## 用与任务相称的证据判断完成

把 **模型/provider 回执、原生工具结果、外层脚本退出、用户要求的实际效果** 分开看。对一般代码任务，实际 diff、相关测试和目标产物通常比额外堆统计更有用；涉及会话控制或外部操作时，再核对对应 thread/turn/item/操作标识。

如果工具已完成且目标产物存在，外层包装脚本却在后续记账/格式化时出错，先核对已有结果，再修复或重试必要的一层。不要只为了获得外层 exit 0 而重复一个已经完成、可能有副作用的操作。反之，provider 成功或模型说“已完成”，也不能替代工具和业务效果。

版本、来源与更新判断见 [sources-and-compatibility.md](sources-and-compatibility.md)。
