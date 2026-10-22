# 来源、技能格式与兼容性

在解释版本差异、补充命令、更新 CDS 或维护本 skill 时使用本参考。日常执行只读取与当前问题相关的来源。

## 先确定是哪一层

| 层级 | 决定什么 | 查哪里 |
|---|---|---|
| 用户当前请求与授权 | 做什么、允许作用于哪些对象、是否允许新增安装/外部操作 | 当前任务及已经确认的约束；skill 不扩大它们。 |
| 实际安装的 CDS / native CLI | 可用子命令、参数位置、原生协议版本 | 用户选定入口的 `--version`、相关 `--help`；需要时查看该安装的公开版本/manifest 信息。 |
| 当前运行的 owner / profile | loaded thread、feature、权限、已发现的工具与扩展 | 原连接的有效配置、状态和工具目录。源码工作区 HEAD 或同名 CLI 不等于当前运行实例。 |
| CDS 发布约定 | provider 适配、固定模型、隔离、平台资源、支持/验收范围 | 本项目 README、`docs/STABLE_PLAN.zh-CN.md`、发布 manifest 和平台报告。 |
| 上游通用行为 | API 生命周期、技能格式、配置概念 | 官方文档；涉及固定版本细节，再查对应 native commit 的源码/schema。 |

新旧资料不一致时，先标明版本与执行入口。不要为了匹配较新的网页文档而编造旧二进制没有的参数，也不要因为旧版本没有某功能便宣称所有 Codex 版本都没有。

## 维护时的已知基线

本参考编写时（2026-10-22）核对的项目基线为 adapter **v0.5.0**，原生 **Codex `0.164.0-alpha.7`**，upstream commit **`fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`**；模型请求名固定为 **`deepseek/deepseek-v4.1-flash-1017`**。这些是可追踪的历史基线，不是“永远最新”。实际入口可能仍指向另一个已安装版本。

普通模型请求优先官方 DeepSeek 路由，部分已验证的协议条件使用显式兼容的同模型路由；以对应发布的 catalog、实现和实际回执为准。不要把更换模型、提供方、平台或原生产物当作纯参数修复，也不要把 API 连接成功等同于获得 OpenAI 模型能力或云端权益。

本项目的“native runtime”是保留原生 agent loop、工具调度、会话、审批、沙箱与协作机制；模型回答质量、第三方服务行为和账户权益单独判断。原生 `alpha` 身份及发布报告的范围应保留，不能用一次局部成功扩展到未测试系统或未来版本。

## 官方与项目入口

- [官方 Codex App Server API](https://learn.chatgpt.com/docs/app-server#api-overview)：thread/turn、进程与终端、MCP、技能、hooks、配置等接口的行为入口。实验或开发中的方法先核对当前版本，不能由“出现在页面上”推出生产可用。
- [官方 Build skills](https://learn.chatgpt.com/docs/build-skills)：SKILL.md、渐进加载、显式/隐式触发、支持文件与本地发现路径。
- [CDS 项目与安装说明](https://github.com/0xZoharHuang/codex-deepseek-runtime)：使用与实际版本匹配的 tag/commit；不要只读仓库首页最新内容来解释旧安装。
- [稳定方案](https://github.com/0xZoharHuang/codex-deepseek-runtime/blob/codex/runtime-release/docs/STABLE_PLAN.zh-CN.md)：用于理解证据范围和组件谱系。检查某次发布时应切换到对应 tag/commit，而非把此分支链接当不可变记录。
- 固定原生源码中的 `codex-rs/cli/src/main.rs`、`codex-rs/exec/src/cli.rs` 和 `codex-rs/app-server-protocol/src/protocol/`：命令结构和协议注册的精确依据。通常先用原生 help/schema；只有文档缺口、版本冲突或排障需要时才深入源码。

## 参考内置 skills 的结构，不照抄它们的任务限制

固定 native 源码在 `codex-rs/skills/src/assets/samples/` 自带 `skill-creator`、`skill-installer`、`review-agent`、`openai-docs` 等技能。编写本 skill 时已核对内置 `skill-creator/SKILL.md` 与当前宿主提供的 creator 内容一致。

沿用其格式与渐进披露方式：

```text
cds/
  SKILL.md                 name/description + 核心决策与按需路由
  agents/openai.yaml       需要时配置展示信息/调用策略
  references/             只有特定工作才需要的细节
  scripts/                确有可靠重复执行需求时才添加
```

SKILL.md frontmatter 必须有 `name` 和 `description`。description 应使“使用 codex-deepseek/CDS”准确触发，不吸收所有泛软件、研究或医疗语境下的同名缩写。主入口保留用户意图、必要判断和参考路由，不复制完整 native 手册；本参考也不把某次接口总数冻结成永久能力清单。

`review-agent` 的只读规则属于被委派的代码审查工作，不能传播为所有 CDS 任务的限制。`skill-installer` 的安装流程也不表示使用一个现有 skill 时可以顺便安装插件、迁移账户或修改运行环境。个人某次偏好、某个具体工具的修复配方，不应变成 CDS 的全局规则。

保持默认的隐式技能选择；只有用户明确要求显式调用时，才设置 `allow_implicit_invocation: false`。技能被选中与执行具体操作的授权是两回事。

采用当前 creator 提供的初始化/校验工具，维护 UI metadata 时再读其 `openai_yaml` 参考。结构校验只能证明 frontmatter/命名等基本条件，不能证明路由正确；复杂 skill 应用有代表性的请求验证是否选对入口、保留授权和识别真实结果，而非只测试标题文字。

## Skill 发现路径的版本区别

当前官方文档以 repository 的 `.agents/skills` 和用户 `$HOME/.agents/skills` 等位置说明发现机制。固定 native 的 `ext/skills/src/host_roots.rs` 仍保留 `$CODEX_HOME/skills` 的兼容读取，并在 user layer 加入 `$HOME/.agents/skills`；内置 installer 的默认目录因此可能与最新文档示例不同。

遵守用户指定位置和当前 creator/installer 的实际约定，安装后用所选 runtime 的技能目录确认可见。不要为“修复”路径差异复制同名 skill 到多个位置，也不要改写生成的 `.system` 内置技能。独立 CDS home、共享用户 skills 与 Codex App 状态是不同层次；共享技能目录并不意味着要共享会话、凭据或 daemon。

## 更新与复核的界限

- 只有用户请求更新或当前任务确需解决已确认的版本问题时，才进入升级流程。不要把普通执行任务自动变成上游升级、重装或整套资格审计。
- 使用 CDS 对应发布的源码、native archive、依赖锁、catalog 和校验规则。不要直接替换受完整性保护的安装文件，或用一个任意最新版 `codex` 二进制拼入旧 CDS 安装。
- 改变 native/adapter/provider/platform 的行为时，检查受影响机制；扩大验收的程度与实际变化相称。纯文档或换行变化也应精确记录差异，不能把旧执行 source_id 改写成新执行。
- 历史通过、原始失败、单项补测、当前安装验证和真实任务效果分别保留。一个报错的包装脚本可能包着已经完成的工具操作；先核对所在层，避免重复执行。反过来，也不能用 provider 成功或 schema 存在替代实际效果。
- 对尚未证实的能力明确保留不确定性，优先用原生 help/schema、只读状态或小型受控检查解决。需要真实模型或外部系统时遵守当前任务的授权、费用和数据范围；不因 skill 本身而获得额外授权。

日常能力选择见 [native-tools.md](native-tools.md)。
