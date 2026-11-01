# CDS 0.6.9 资格与交付索引

**本版固定候选的本地 runtime 资格已完成，远端交付、日常切换和永久资产 CI 仍待逐项确认。** 本报告不是稳定版本已发布的声明，也不代替独立 CADSG / Codex App 验收。实际 tag、main 和资产信息由发布者完成远端读回后补入索引。

本轮产品身份为 `8e26c81c987b7502ac96`：31 个运行模块、113 项安装器身份输入。安装与模型验收所用产品源码为 `f4808831605f0232bf0dc5decbbbbdf02d47f151`；夹具后继 `d6bc7d6d…`、Unix 验收源码 `3e9712e…` 与文档整理前的 `ff4d9ed…` 保持这些产品输入逐项相同。文档、测试夹具和 CI 后继不能被写成重新安装或重新编译的证据。

## 本版完成的改变

- P27 在上游 Codex CLI `0.164.0-alpha.7` / `fe136d4e…` 的共同迁移入口兼容同一 SQL 的精确 LF / CRLF 形式。仍使用原 SQLx、工具执行、审批、沙箱、会话和原生协作，不另建 runtime。
- 新建 Windows 存储使用官方 Windows 形式；一致 LF / CRLF 的旧存储保持自身形式，空辅助库继承已确认的主库形式。未知 checksum、未完成迁移仍拒绝；不改写历史 checksum。上游已有的 legacy 38→39 版本/描述修复保留。
- 新增逐 home 的显式普通提供方配置，缺省仍为官方 `deepseek`，可明确选择已资格的 `morph/fp8`。固定模型不变，原串行、严格输出和 peer/续接特殊路由优先级不变；每个请求仍单一 `provider.only`、`allow_fallbacks=false`、`require_parameters=true`。没有失败后的自动换路由。

## 三平台原生与安装版证据

P27 三平台原生产物均来自已记录的 `a5853bf6…` producer 源码、固定 27 份 overlay 和精确原包。Windows 原生包与原、新 CLI 完成六阶段真实互读：每个 profile 实际初始化的五个数据库，其账本、schema 和 typed 用户数据保持一致；未创建的 history 没有冒充互读通过。Rust 状态测试另覆盖六类存储及记忆版本边界。

| 当前验收集合 | 实际结果 | 计数边界 |
| --- | --- | --- |
| Windows 安装版软件 | 2703 正向＋4 个精确平台专用跳过＝2707 | 原完整运行 2688 正向／15 失败／4 跳过保留；同一安装件精确 15 项复验通过后合成，不叫一次全新全绿运行。 |
| Windows 原生 CLI / HTTP helper | 22／22，无失败或跳过 | 19 个自然 exit0，3 个预期负控 exit1；与软件集合严格不相交。 |
| Linux 新 Python 复用 CI | native22＋软件 2652 正向／55 个合法跳过＝2729 | head `3e9712e…`，run `41334558626`；安装模块与源码逐项对应。 |
| macOS 新 Python 复用 CI | native22＋软件 2651 正向／56 个合法跳过＝2729 | head `3e9712e…`，run `41334558965`；运行于 GitHub-hosted macOS，不是维护者本机。 |

每个平台的完整集合均为 **2729＝软件 2707＋native22**。Windows origin2 与主集合重叠，不额外计数；三文件 51 项复验也不额外计覆盖。Unix 每个平台的 296 项 CI 控制器检查含一个主软件集合重复节点，未加到上述总数。

新 Unix CI 没有重新编译 Rust，而是核对同一组 P27 原包、原 receipt、完整源码/锁/驱动和 recipe 后，承接原 State22、Guardian10 证据。State 的未执行 filtered 项分别为 Linux 224、macOS 222，Guardian 各 117；不计通过。原 Pool10 的名字与结果不独立证明“显式 false provider 配置下的取消”，该组件范围保留。

Windows 软件全套使用满足符号链接等夹具前置条件的 High 执行上下文；native22 和 origin2 的驱动为实际 Limited/session3。原预检没有保存所有已退出软件进程的 token，不能追溯伪称逐个读取。已观测短命进程的不确定记录仍保留：Windows native 5 项，Linux native 4／软件 13 项，macOS 软件 16 项。它们不是已证明的泄漏；已记录 remaining / unknown-live 为空也不等于任意短命进程全部可观测。

## 显式 Morph 的真实任务

官方默认路由的原 nonce 任务在原生重连后仍失败为 502，未执行工具、Guardian 或最终 schema；30 条原官方 502 回执保留。相同小请求的普通 HTTPS 和 native 对照同样失败，认证、余额检查为正向，故没有将故障归为原生传输特有，也没有猜测具体服务端原因。

随后在同一专用 home、同一固定 DeepSeek 模型上，通过正常 CLI 显式 CAS 选择 `morph/fp8`。活动 owner 下反向改配置被拒，文件未变。实际工具读取 nonce 和 SHA，原生 Guardian 允许，最终对象按原始完整 outputSchema 校验。服务/原生进程自然退役后，同一原 UUID 冷恢复，从真实持久化工具历史读回值；冷请求与 schema 未注入答案，也没有重新调用工具。普通工具路由使用文本＋最终 schema 校验，不宣称提供方进行了严格受约束解码；合法的 agentMessage `phase=null` 没被观察器误判。

Doctor 的一个实际计费 generation 已另行核对为同一模型、Morph。nonce / Guardian / cold 的四条成功 HTTP 回执没有各自额外查询计费元数据，不能外推逐请求发票证明。这组受控窗口完成 POST 与 health 的观测 Upgrade 为 0；不扩大成所有时期、所有连接的断言。

## 复杂真实工程与原生机制

显式 Morph 完成了已知 Jinja 三文件修复任务：真实首轮 runner 为 exit1／6 失败、902 通过，随后一次原生 Stop、同 UUID 继续，再到实际 908 项公开测试通过。独立重建验收为 908＋22＝930 个真实节点、零失败/错误/跳过；2790 个 setup/call/teardown 结果不是额外 2790 个测试。120 个保护输入及全部元数据、4237 个依赖文件保持；控制器没有代改业务源码。这是有监督的已知回归任务，不是盲测、SWE-bench 得分或模型质量等价证明。

主→子→孙的独立原生 UUID、启动/完成和两次 wait 有原始事件。V2 没有 close_agent，不捏造关闭调用。子任务的“只读”是父任务文字要求；其原生权限仍为 workspace-write。子任务曾改动三个允许路径，后精确撤回自己的 diff。这是保留的模型指令违背，不是 OS 只读沙箱被绕过，也不被最终成功抹掉。

官方 Jinja 文档经 MCP reader 实际 open 12 次、find 成功 15 次（14 次有匹配、1 次合法零匹配）、click 4 次；另有一次空 ref find 失败保留。引用与实际 open/click 来源对应；截断的可见链接列表不被当成完整缓存目录。本项不是 OpenAI 私有搜索索引或所有 web.run 数据服务的代验。

共 69 个请求均保留固定模型、显式 Morph、无 fallback 和 require_parameters：68 个 HTTP200 完成，1 个由计划 Stop 取消。完成访问日志覆盖 68 个 POST；取消的半截前端请求不在 completed access log 的覆盖内。回执 context key 均归父任务，不能据此捏造每个子任务独立回执或每次单独计费核对。

## 原件、观察器修正与剩余交付

原准备失败、首次配置预检失败、模型失败命令、空 ref、子任务越文字约束、短命进程不确定性均保留。私有摘要曾被最后报告读数覆盖，基线改以首个真实 typed runner 的时间、exitCode 和输出选择；选择器负控拒绝“只有旧失败文字的成功命令”和报告文件读取。私有 holdout 的旧 classname 假设也已按冻结源函数/参数身份与实际 collection/JUnit 严格对应修正；没有放宽断言或修改原输出。

当前还需完成：实际 tag/main/公共资产及远端逐字节读回、正常日常入口升级与状态保护、真实资产 pins 的永久化默认切换，以及两平台实际走永久下载路径的 native22／完整软件 CI。临时 Actions 输入不是稳定长期下载渠道；永久化代码准备及软件检查不代替实际发布后验收。

[qualification-index.json](qualification-index.json) 给出冻结证据 SHA 和机器可读计数。原始请求、会话、模型内容及凭据保持私有。云端账户权益、模型固有模态/质量、金融体育私有数据和独立 CADSG App 按既定边界单列，均不因本报告而被标成通过。
