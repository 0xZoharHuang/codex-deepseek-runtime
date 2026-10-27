# Codex CLI + DeepSeek 的稳定运行方案

## 0.6.6 集成候选：尚未发布

0.6.6合并两项协议修复和 Windows 原生适配简化。工具的完成项是提交边界：final-only custom input 尾部必须完整交付，function/custom 的预览都不能提前触发执行；已提交调用的改写仍失败。预览违约只保留最多16条哈希化诊断，不写原始代码或用户文本。

Windows 五文件补丁去掉 metadata journal、raw NTFS 和额外精确撤销路径，保留当前授权路径的 FileID／防重解析、有效 DENY、本机 SAM 与非继承属性授予。native producer 是37787ecf1882521b168ec8cc16d5dcd5894b2572，完整原生源码为9c33e0369dd955f2715d52fbdd3d19265d6ff49a29bf77ad9d777685b308a6ff，Windows recipe为410a7343667baecf2a8de222e32a09a858eaa626706ba9b65222fc5135dc1ef2。这些原生产物不改签为后续 Python／文档集成提交重新编译。

当前只关闭了源码／组件与限定 Windows native lifecycle 范围：完整8项 private layout 按正常生产来源组装；新 helper 正常执行，真实文件允许／拒绝、临时 namespace／WFP 删除和幂等卸载有证据。独立 D 盘工作区真正 cold 命令3.074秒完成时后台授权仍存活；native cwd junction 的物理目录／FileID已核对。原 AppData 夹具45秒预算失败保持失败；相关继承域约83／101秒等待是记录中的合法协调，不通过改 runtime timeout／ACL策略隐藏。原生卸载保留 home 和普通 filesystem ACE，最终只按已知路径／FileID／退休 SID 清理自有 QA 材料，不新建 ACL 恢复引擎。

完整 CDS launcher 的 Low 离线安装与协议回归、真实 DeepSeek 复杂任务、最终版本／公开交付仍待后续验收。Python 候选是0.6.6，Windows候选原生资产是0.6.6；Linux/macOS原生0.6.2字节与原 producer 回执不变。不得把已构建、helper可运行或旧版报告写成全部 runtime 已通过，也不承诺 Defender 原因或未来免报。

[预定的 v0.6.6 release 地址](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.6)尚未发布。候选使用正常安装器的显式匹配 manifest／archive／wheelhouse、新 root及 CODEX_HOME，按[README候选步骤](../README.md#安装并运行)验证；不覆盖正在工作的日常 CDS，不读取或改动 Codex App 的登录与状态。以下旧版本材料保留原始身份与范围。

## 0.6.5 历史方案

0.6.5固定本地runtime功能资格与公开交付已通过，产品source为072be79ac2b773d2684c；已正式发布为GitHub Latest，tag固定01de977bc7d1d2474d7b8dd2697b80959e29d42b，222609996字节匿名下载摘要一致，见[发布回执](../reports/releases/0.6.5/publication-record.json)。Windows标准用户目录的SYSTEM/Administrators所有权现由原生元数据专用谓词正确维护；不改变目录所有者、调度器、审批器或工具执行器。

当前Windows installed完整集合2295：2291通过、4项明确平台不适用、0漏选；普通账户native21全通过且是完整集合子集。真实profile完成Full refresh/冷重入、受限读取、foreign-owner拒绝/精确恢复、critical RX DENY/恢复以及先核effective roots的inside写入/outside拒绝。一轮真实模型任务完成CodeMode/原生本地ToolSearch/MCP digest/未知nonce和哈希，12个请求均独立确认served DeepSeek；3次PowerShell和3次JSON解析失败保留，本轮没有wait调用，不伪造一次全绿。

Linux、macOS当前源码的hosted完整分区分别2255/40skip、2254/41skip，总数均2295，另有173 controller。两个Unix native仍是原字节0.6.2；新的Windows归档b3438fec…/3022文件与完整prepared源码和patch链一起交付。首次CI旧pin失败保留，CI-only修正没有改变101个产品输入。详见[0.6.5材料](../reports/releases/0.6.5/README.md)。

本地runtime不等于OpenAI云服务权益。原生ToolSearch是本地工具发现，联网Web Search使用搜索后台，二者不混写。旧版本复杂任务、原始失败和上游边界仍按原身份保留。日常默认入口的切换与安装资格分开，未知归属旧控制台及其home继续保护；CADSG的App GUI仍独立验收。

## 0.6.4 历史方案

v0.6.4 已正式发布并读回为 GitHub Latest；tag 固定到69b9271247277fb9d2ecb4816706736863a11860，后续报告提交不改变产品 source3ae。见[发布回执](../reports/releases/0.6.4/publication-record.json)。

0.6.4 保留原生任务循环、工具、审批、沙箱、会话和协作机制，仅修正 Windows 关键读取路径把 ALLOW 当作充分就绪证据的缺口。实际目标账户或已知正常组存在有效读取/执行 DENY 时，原生设置流程现在明确失败并保留 DACL；系统权限没有被绕过，也没有以放宽权限通过验收。

当前 Windows 完整回归为 2286 通过、4 项平台跳过；真实专用账户完成允许读取、DENY 拒绝、ACL 恢复后再读取，以及先核对原生有效根再执行的工作区允许/拒绝。新 CLI 的一次真实 DeepSeek turn 经原生工具读取随机文件，三个模型请求均由独立 generation 记录确认；首次 Bash 拒绝和同 turn 改用 PowerShell 的恢复保留。范围、失败与逐项证据见[0.6.4 报告](../reports/releases/0.6.4/README.md)。

Linux/macOS 使用原字节 0.6.2 原生包，当前 0.6.4 Python 安装分别通过 2229/40skip、2228/41skip，每个平台另有 21 原生传输和 171 控制器检查。Windows 原生包版本为 0.6.4；不全局替换 Unix 资产，也不把旧复杂任务重新签成新执行。日常入口是否激活与产品资格分开；运行中的未知归属旧控制台保持原状。以下旧版本章节保留其原始身份。


0.6.4 的公开归档已通过匿名完整下载与摘要核对；这是发布通道验证，不是 Mac runtime 实测。当前 Windows 主机的默认下载 URL 连续失败，官方资产 API 返回200但未下完整，原记录保留；实际用户目录通过文档支持的同摘要离线归档正常安装成功。用独立 home 和绝对入口运行，不覆盖未知归属的旧控制台。[公开交付与本机安装记录](../reports/releases/0.6.4/public-delivery-record.json)

## 0.6.3 历史方案

0.6.3 只补上响应中明确模型／提供方身份矛盾的拒绝检查，不改变请求路由、原生工具调度或重试。缺失字段保持未知，已验证的同义模型别名和提供方显示名／slug 可以互换。即使另一个提供方在总体允许列表中，也不能替代这一次明确选择的路由；已经交付的工具事件不会被后续错误抹去。

本轮 Windows 完整回归为2281通过、4项平台跳过；Linux和macOS当前已安装适配层分别2224/40、2223/41，各另有21项原生传输与171项控制器检查。Windows正常安装还实际完成官方DeepSeek doctor与保存会话请求、受限文件效果和冷恢复。范围、原始反例和[CI原件读回](../reports/releases/0.6.3/unix-installed-regression.json)分别保存，不把测试数量称作所有输入的穷尽证明。

所有原生包仍为已发布的0.6.2资产和原recipe，不重编Rust。Windows完整资格绑定source09e4；最终README说明产生source22e550，99项输入仅README不同，29运行时／2991原生文件及catalog和lock同字节，已正常安装并回读版本与帮助。[文档派生回执](../reports/releases/0.6.3/windows-readme-derivative.json)没有改签旧实跑身份。

日常升级仍按[README](../README.md)使用对应正式release；任务运行中保留旧入口，先以独立root/home验证。最终验收入口是可单独使用的不可变安装，是否切换全局cds是另一个维护动作。Codex App的独立CADSG项目继续单独验收。

以下保留0.6.3及更早版本的历史冻结结论；当前0.6.4见[本版报告](../reports/releases/0.6.4/README.md)。历史身份、数量和失败不改签为新版本结果，操作须匹配实际入口和home。

## 当前 0.6.2 的资格与交付状态

D69严格Windows资格已封存：2,150项offline通过/4项平台不适用，30个有效合并场景、128项正向RPC与3项原生边界、456份Schema无差异。原29通过/1项TUI失败及后续终端补测保留，`single_run_all_passed=false`。Jinja的runtime14项和业务均通过；HTTPcore的runtime14项通过，业务失败、原始超时和provider错误保留。

CF9 `cf9f19eaf67b0fdd8151`已正常新安装；实际runtime29文件和native2991文件与D69相同，产品输入只变化manifest的LF输出及换行。CF9另有doctor/保存线程共2次真实请求、同home受限原生Python写入/外拒、EOF退出与冷恢复，以及进程自然退出证明；当前完整offline为2,229通过/4项平台不适用。见[组件证明](../reports/releases/0.6.2/component-equivalence.json)、[安装健康](../reports/releases/0.6.2/candidate-installation-summary.json)及[QA升级谱系](../reports/releases/0.6.2/qa-upgrade-lineage-summary.json)。D69历史证据保留原producer身份。

v0.6.2 已正式发布为 [stable GitHub Release](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.2)（Release423636524，draft=false/prerelease=false，latest已读回）。产品tag保持 `4f24af36f62c538ba35817ad48aa49d4308220b5`；后续QA提交 `d325437588e7fbde418aa52f008740ea96fab0fd` 未改CF9产品99或native资产。main已快进到已验证的QA提交并经GitHub读回确认，见[发布回执](../reports/releases/0.6.2/publication-record.json)；发行成功不代表已激活日常默认`cds`。

实际公开资产 [CI40233851528](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40233851528) 已通过：Linux/macOS各171 controller、21 native，adapter分别2,172/40skip和2,171/41skip，0模型/重编译，下载身份和进程hygiene均通过。正常Windows公开归档[安装读回](../reports/releases/0.6.2/public-install-readback.json)也已通过，未传native归档/manifest覆盖参数；依赖仍使用哈希锁定wheelhouse，未切换默认入口。旧2179/4、2181/4、首次collection失败和首次公开资产Linux20/1诊断race均保留；48个新增纯反例及真实受限command正例见[QA审计](../reports/releases/0.6.2/public-asset-observer-audit.json)。旧D69资格及下文0.6.0历史结果各保留原身份。

## 0.6.0历史方案

本方案描述 0.6.0 的固定架构、安装隔离和运维方法。**固定组合的三平台 runtime 与公开原生资产路径的托管 CI 已通过。** 可部署状态与逐平台证据以 [0.6.0 验收记录](release-0.6.0-acceptance.zh-CN.md)及对应 GitHub release 为准；本文件的操作说明不代替验收结论。

CDS 保留 Codex CLI 的原生任务循环、工具、审批、沙箱、会话和子智能体，只在安装隔离、模型协议、传输及确有必要的平台边界适配。模型质量、模型未具备的模态能力、OpenAI 账户权益和专属云端服务单独列明。

## 固定组合与证据

- 适配层产品源码：2d43ed50050d0a687f55，95 个产品文件。
- 原生 Codex：0.164.0-alpha.7，提交 fe136d4e70ef2b5bbed40a2d22b3aabf267796d3。
- 模型：deepseek/deepseek-v4.1-flash-1017；模型与提供方均明确固定，不使用可自动变化的 latest。
- 平台：Windows x64、macOS ARM64、Linux x64 GNU；每个平台分别验证其原生支持范围。Windows 10 不声明 Windows 11 专有能力。

原生基线本身是上游预发布版本，项目的发布说明必须保留这一事实。固定版本、可回退安装和完整验收提供的是明确组合的使用依据，不能把上游 alpha 改称上游稳定版。

每份报告绑定产品源码、原生包、实际验收脚本和原始输出。三平台最终各收集 1807 项离线测试：Windows 1804 通过/3 项平台不适用，macOS 1775/32，Linux 1776/31。Windows 的 128 项正向 RPC 与 3 项原生配置边界、Mac/Linux 各 129 项正向 RPC 均已从原始请求和实际效果重建。四种 OAuth、网页 open/find/click、协议恢复与原生协作等当前实跑及原始失败分别记录；不从 v0.5.0 继承通过。复杂开发任务在 Windows 执行，Unix 明确复用未变共同组件的 runtime 证据，不冒称本地工程任务成功。[最终托管 CI](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40087081457)也已通过公开原生资产下载、15 项真实传输与完整集合分区校验；Windows 公开版本的[显式归档安装读回](../reports/releases/0.6.0/public-install-readback.json)已通过，原直连 GitHub 网络失败与脚本文件数错误均保留；该方式不改变全机网络或共享入口。

此前版本的结果保留在 [v0.5.0 历史方案](https://github.com/0xZoharHuang/codex-deepseek-runtime/blob/dd832a9fa45ab12ed87cf98942cb2d1c8c34d237/docs/STABLE_PLAN.zh-CN.md)。旧版本的接口数量、平台报告或安装清单不能自动成为 0.6.0 的通过证明。

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

Windows 使用独立 Python 执行同一个安装器，入口是 `codex-deepseek.exe`。默认安装目录为 `%LOCALAPPDATA%/codex-deepseek`；macOS/Linux 默认为 `~/.local/share/codex-deepseek`。并行验证 0.6.0 时，需使用此前不存在的新 root、新 home 和匹配当前 recipe 的显式 native manifest/归档；详见[候选安装步骤](release-0.6.0-candidate.zh-CN.md)。`--no-activate` 仍初始化 root 内的 home；运行通用不可变入口时也须显式设置该 home。

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

Linux 额外包含原生构建的 `codex-resources/bwrap`。其最终字节先被哈希，再将摘要嵌入 CLI；验收同时检查强制使用捆绑资源、实际文件权限限制和篡改拒绝。当前 GNU 构建及实测环境是 Ubuntu 24.04。全部 5 个 runtime ELF 的动态依赖与符号已核对：CLI 要求 glibc 2.39，HTTP helper/bwrap 要求 2.38；整体部署须满足 glibc 2.39，并有 OpenSSL 3、libcap2、libgcc-s1 及标准 libc/libm 与 x86-64 loader。包不是 musl 静态包；更旧 glibc 的发行版不能直接使用。完整 soname 与包版本见[实际 ELF 库清单](../reports/releases/0.6.0/x86_64-unknown-linux-gnu/linux-runtime-libraries-final.json)。

Windows 包含原生沙箱 setup、runner、service 和诊断辅助程序。进程使用内核创建时间识别，避免误杀被复用的 PID；文件锁使用系统文件锁，私有目录使用受保护 ACL。Ctrl-C/Ctrl-Break 保留 Windows 原生控制台广播语义，不翻译成另一种信号。

网关工作进程崩溃后，可以在同一监听端口恢复服务；未知的在途模型结果仍标为未知，不重新执行工具。采样轮次中断与后台命令、MCP 请求退出是不同事件，按同版本原生行为验收。关闭拥有者后的清理，另外用实际进程、连接和文件效果证明。固定 Linux 上游的插件 Git 后台下载在 daemon 停止后仍可能继续，同版完整官方包已复现；操作系统进程检查与准确拥有者清理另行记录，不能把 daemon stop 的成功当作整棵下载进程已经退出。

## 完成的判据

0.6.0 按以下机制验收。每项结论必须绑定当前源码与实际包，或给出可核查的未变组件复用依据；新增行为需要实际运行证据：

1. 完整原生源码清单与补丁重放，确认保护范围中的会话、工具处理器、调度和沙箱实现保持上游内容。
2. 同版本官方二进制与本项目二进制的全部 456 份协议 Schema 比较。
3. 174 项客户端方法的明确适用范围；Windows 本地范围为 131 项，其中 128 项要求正向状态或副作用；3 项命令控制接口属于同版官方和候选在真实受限命令上的相同平台边界，必须各自有实际请求/响应证明。macOS/Linux 按其各自范围验证，不能用 Windows 的成功代替。账户、后端和平台边界分别保留实际证据。
4. 标准场景、实际收集到的全部离线测试、逐方法的状态或副作用，以及对应的真实请求/响应配对。
5. 真终端输入输出、窗口尺寸、取消、正常退出、同一会话冷恢复；普通管道输出不替代终端能力。
6. 文件、系统钥匙串、自动和加密凭据模式中的实际 OAuth、冷启动、跨 profile 隔离与原生清理。
7. Jinja 与 HTTPcore 的复杂真实开发工作流：实际工具调用与文件效果、成功和失败输出的完整传回、原生审查、源码修改后的中断续跑，以及独立测试确实加载工作区代码的证据。保留全部业务测试结果，包括隐藏测试、三轮 TCP 压力测试和真实 TLS 取消；这些测试验证的是被修改的第三方项目，不能与 Codex 自身的中断、网络策略或沙箱验收混为一谈。
8. 被验收父轮次中的实际网页打开、同一引用的正向查找、跟随真实链接到另一文档，绑定明确的 thread、turn 和原始调用。其他轮次、审查者的浏览或搜索摘要不能代替。

验收结论分开记录 runtime 机制与模型任务产出。第三方项目的业务测试失败、模型未完成修复或控制器达到时间预算，都保留原始失败状态，不能改签为任务成功。runtime 机制必须另从原始请求、响应、文件效果、会话和进程状态逐项核验；不能仅删除失败条件或统计工具调用次数后宣布通过。控制器主动中断也必须有对应的原生中断回执和后续恢复证据，不能把未知退出归类为正常中断。

完整通过的开发任务证明正常完成路径；包含真实失败与恢复的开发任务证明错误传递和恢复路径。两者共同补充逐接口与平台验收。模型生成代码的正确率不属于 runtime 等价承诺，任何尚未验证的 runtime 能力仍是发布阻断项。Windows 沙箱、凭据、审批和进程清理的实际结果分别记录在最终平台报告中。

v0.5.0 曾明确采用宿主 full-access 复杂任务与同身份隔离 Windows 客体沙箱证据的组合。那是当时记录的验收条件，不是 0.6.0 绕过沙箱的操作步骤。新的候选必须按自己的权限与安装方案取得证据；readiness 或 setup 成功仍不能替代实际允许、拒绝与恢复结果。

## 委派与运行中控制

子 CLI 的权限以实际启动参数为准，不能从父任务的 full-access 状态推断。`queue` 进入后续 turn 队列；运行中追加输入使用原 app-server 的 `turn/steer`，明确停止则使用 interrupt 并核对后台进程。原版 REA/Ghidra 的已验证启动方式及 exec resume 用法见[委派和本地工具指南](delegation-and-local-tools.zh-CN.md)。

供外层 agent 和 CDS 自身按需使用的 [`$cds` 操作技能](cds-skill.zh-CN.md) 沿用官方 SKILL.md 格式；它只提供操作知识和只读安装检查，不增加工具调度或会话生命周期实现。技能和文档的更新不重写已发布 v0.5.0 的 tag 或平台证据。

## 更新、回退和清理

升级使用新不可变目录。当前 profile 仍有原生任务、子进程或 daemon 时，不替换它的提供方服务。0.6.0 首次验证使用新 root 和新 home，稳定入口保留；候选回退就是结束候选自己的工作并继续使用稳定入口。未来同 home 迁移需单独备份和恢复，保留根目录身份，不复制或清理旧共享 sandbox 资源。具体步骤见[候选说明](release-0.6.0-candidate.zh-CN.md)。

图片传输启用后，应在移除候选环境前核对 owned file 的清理与未知状态。删除本地账本不是远端清理；未解决的回执属于应保留材料。

保留已验证的运行版本及必要回退材料。构建缓存、重复下载和临时虚拟机在停止其确切拥有者后清理；失败证据先压缩、逐文件读回校验，再删除展开副本。项目源码、用户会话、关键回执和 Codex App 数据不属于批量清理对象。实际释放空间以文件系统读数为准。
