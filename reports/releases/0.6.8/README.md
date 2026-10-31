# CDS 0.6.8 本地 runtime 资格

CDS保留固定Codex原生工具、权限、会话与协作机制，用协议/传输适配连接固定DeepSeek模型。下载、tag、最终源码和公开资产的权威是[本版release页及回执](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.8)，不是预填URL或报告内嵌的未来自提交SHA。

## 实现与版本

- 原生基线为`0.164.0-alpha.7` / `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`；模型`deepseek/deepseek-v4.1-flash-1017`。
- strict最终schema+auto+有效工具的已识别组合保留原始工具/历史/ID/auto，明示输出格式映射，再以原请求StructuredOutput校验最终JSON。不强迫tool、不造synthetic final、不失败后换模型/提供方；不承诺上游完整schema受约束解码。
- 原生Guardian最小guard尊重supports_websockets=false，保留原HTTP评分/lease/取消及支持WebSocket的池；未禁Guardian或改full access。
- Windows已识别CPython3.13.3关闭方法采用process-local shutdown-finally兼容修复，清理close/detach/state但保留原异常。未改共享Python或事件循环策略；未知实现明确NOT_APPLIED，不盲改。
- 没有另建agent loop、工具调度器、审批器或子智能体系统。三平台native资产沿原构建身份，后续CI/data-only与文档变更按组件逐字节沿用，不重复Rust或同一业务任务。

## 可独立核对的证据

[qualification-index.json](qualification-index.json)列原始证据SHA及边界；私有项目内容、系统身份和凭据不随摘要公开。摘要不是新运行。

| 证据 | 实际范围 |
| --- | --- |
| 最终Windows软件 | ac3受控源码完整collection2601=software2579+native22；software2575通过/4原平台skip，零failure/error/deselection，JUnit/collection/分区Counter精确。 |
| Windows fresh native及来源 | 新正常no-activate安装产品30；普通权限22native、2origin guard通过。与ac3及最终docs/data的差异按产品30/native/lock/installer/catalog精确字节绑定，不伪称文档后重跑。 |
| Linux | ac3 GitHub run41214753061：249controller及22native零failure/error/skip；software2579中2524通过/55实际平台skip，安装源30与0.6.8实际核验。 |
| macOS ARM64 | ac3 GitHub run41214756560：249controller及22native零failure/error/skip；software2579中2523通过/56实际平台skip。没有在用户Mac主机测试。 |
| Rust Guardian | 实际source-bound10项；仅在native/source/recipe/二进制不变下carry。不是文档/data变更后再次Rust编译。 |
| Windows真实工程 | DeepSeek主→子→孙、web查证、实际审批、Stop/warm/cold同UUID；独立908 public+22 private=930全过。已知任务，非盲测/SWE分数；120保护输入、4237依赖与Git状态独立核验，controller未代模型修业务。 |
| schema | 真工具读取提示/schema未知数据后同turn合规JSON，含无phase最终项；另30项安装版正负边界全过，不称30项全为负控。 |
| HTTP/Guardian | 已验850两窗口共5个完成POST：4个DeepSeek-only声明+1显式Morph Guardian，0模型窗口Upgrade，2 GET Upgrade→405正控；复杂任务另9次Morph评分。不是物理socket/frame总数，也未伪称generation-provider attestation。 |
| 177本地目录 | CDS0.164目录177；同版本漏索引getAuthStatus(false,false)、getConversationSummary、gitDiffToRemote已补原生语义/副作用验证。不是每平台177次fresh RPC；CAG0.165的179不算CDS遗漏。 |

当前Windows来源guard5156次检查、临时QA框架路径单文件CAS撤销后新prefix5045文件逐byte恢复，旧00b5042及日常入口未改。最终仅docs/data的源码身份变化，由新的wheel/normalSXS/origin/入口及外部release handoff补绑定，不将旧prefix改名成新安装。

## 失败、边界及未作出的承诺

- 初始Jinja 6fail/902pass、未声明模型tool被拒且未执行、观察器phase/转义/冻结元数据失败都保留；后续930只证明工程结果，机制另有事件证据。
- 首Windows软件2550pass6fail4skip保留；五个子pytest缺框架和junction夹具GBK文本解码，修QA前置/bytes捕获后6targeted和完整回归独立通过，未改产品/断言/权限/skip。原065 full的FIN观察失败也保留：transport已清理，aiohttp异步done callback未结束；修同2秒等待条件而保全部assert后最终回归通过。
- 七个历史10054栈精确匹配跳过清理的stdlib分支；修复的是分支清理。历史每条缺timestamp/FD/receipt，不能重定性benign、具体Stop归因或实测泄漏；异常仍可见。真实3RST有界case未复现不能被叫证明原日志消失。
- Windows旧131矩阵128正向及3项native RestrictedToken command write/terminate/resize平台边界保原生对照，不造额外正向。
- V2实际六个handler无close_agent；V1显式close按版本契约。不能虚构V2 API/通过。
- web覆盖OpenRouter Exa搜索与codex_web MCP open/find/click/PDF；不是私有托管web.run全函数复刻。Exa cached/index/location/image等不支持语义明确报错，未丢字段或列passed。
- 私有金融/体育云数据、账户/云权益、模型质量及缺失模态单列；不由换模型取得，也不能把本地命令、队列、目标因此排除。其他模块不因叫“云”自动豁免。
- CAG/Codex App独立；本报告不替其GUI、闭源工作流或业务通过。各解释器/OS/CPU未自动扩展资格。

## 交付与回滚

活动native/runtime*.json使用原字节三tar及完整receipt、license和recipe。ready只指artifact契约，下载必须以release页实际提供内容和SHA回读；不在Windows重包Unix已资格原tar。

原0.6.7三manifest逐byte存native/rollback/0.6.7，原recipe acc16483保留；旧资产配旧tag及[许可证补充](../0.6.7/attribution-correction.json)，不冒充新guarded资产。正常安装、same-home恢复、版本切换、rollback和owned清理见[0.6.8方案](../../../docs/release-0.6.8.zh-CN.md)及[稳定方案](../../../docs/STABLE_PLAN.zh-CN.md)。最终源码由tag/外部qualification回执解析，本文不制造自身SHA固定点。
