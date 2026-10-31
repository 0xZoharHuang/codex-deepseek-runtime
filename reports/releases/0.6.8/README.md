# CDS 0.6.8：本地 runtime 资格与交付候选

**可审候选，尚未发布。** 当前正式版仍为v0.6.7。三份新guarded native已真实构建、保留原producer/recipe/receipt；URL是未来发布位置。当前Windows完整软件回归已补齐；必要的10054 process-local cleanup backport资格、最终源码/包/secret扫描和公开资产回读尚须由owner关闭。CAG/Codex App独立验收，不能借本报告通过。

## 实现边界

固定原生Codex `0.164.0-alpha.7` / `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`，模型 `deepseek/deepseek-v4.1-flash-1017`。工具、审批、沙箱、会话、重试、压缩、队列、目标、线程/子智能体仍交给原生runtime；不新建agent loop、调度器或审批器。

严格最终schema+auto+有效工具的已识别组合，保留工具/历史/ID/auto，显式适配上游输出格式并用原始请求的StructuredOutput校验最终JSON。官方object/array根使用已验证JSON语法模式，其他已识别路由/根使用完整schema指令；不强迫tool、不做synthetic final、不失败后换提供方。**不承诺上游完整schema受约束解码**，无效最终JSON不能发布成功完成。

Guardian原生最小guard尊重 `supports_websockets=false`，保留HTTP评分/lease/取消与支持WebSocket的池行为；没有禁用Guardian或改full access。Front trace是请求观察；许可证采集恢复原始完整notice，永久资产复用原tar而不重写其身份。

## 实际证据与沿用条件

机器索引见 [qualification-index.json](qualification-index.json)。索引保留原证据SHA与范围；私有项目内容、系统身份、凭据不随公开摘要上传。摘要不是重新实测。

| 范围 | 结果与限制 |
| --- | --- |
| Guardian Rust | 实际source-bound 10项；只在native recipe/source/二进制精确相同下沿用，不把文档提交叫新编译。 |
| Windows native | 安装版49da普通权限22项native与2来源guard；850使用同一`cc3aa33a…`CLI，未假称重新跑22。 |
| Linux/macOS current850 | 各22native+249controller零failure/error/skip；各offline2501：Linux2457通过44skip，Mac2456通过45skip。skip按实际平台逐项保留。 |
| Windows current850 | 本候选完整collection2582=software2560+原native22分区；实际安装850的software2556通过、4项原source条件skip，零failure/error/deselection。每次lazy import守护、29产品及5042原prefix文件撤销QA前置后全相同，旧production不变。原native22明确component carry；不是全部177 fresh RPC。 |
| Jinja真实工程 | DeepSeek主→子→孙、真实web/审批、Stop/warm/cold同UUID；独立908 public+22 private=930全过。初始6fail902pass和旧观察器失败保留。已知任务、非盲测/SWE分数；930只证明工程结果。120保护输入、4237依赖与Git状态独立核验。 |
| schema | 真工具读取未知数据后同turn返回严格JSON，含无phase最终项；另30项安装版正负边界全过，不称30项全负控。 |
| Guardian/front | 新850两窗口5个完成POST：4个DeepSeek-only声明+1显式Morph Guardian；0模型窗口Upgrade，2 GET Upgrade→405正控。复杂任务另9个Morph评分通过。不代表物理socket/frame总数，不伪称generation-provider attestation。 |
| 177本地目录 | 固定CDS0.164目录177；同版本漏索引3项getAuthStatus(false,false)、getConversationSummary、gitDiffToRemote已补原生语义/副作用验证。不是三平台各177次fresh调用；CAG0.165的179不是CDS遗漏。 |

## 保留失败与条件

- Windows旧131矩阵为128正向+3项native RestrictedToken command write/terminate/resize平台边界，有原生对照；不虚构额外正向。
- V2实际目录为六个该版handler，没有 `close_agent`；V1显式close单独按版本契约，不能制造V2缺口或声称调用过。
- 未声明的模型tool曾被明确拒绝且未执行；schema/工程观察器曾因phase、转义命令或冻结fixture元数据断言失败，原失败保留，修观察器不重放业务改历史。
- 七个历史CPython10054栈与实际stdlib shutdown分支精确匹配；确定性证据证明该分支跳过close/detach/state。历史每条缺timestamp/FD/receipt，不可称benign或证明实际泄漏，真实3RST有界case未复现。owner已批准最小process-local guarded cleanup backport，**仍待实施/资格并绑定新产品；本页850结果不证明该fix已过**。不改共享Python或换Selector loop。
- web覆盖OpenRouter Exa搜索及codex_web MCP open/find/click/PDF读取，不是OpenAI私有托管 `web.run` 全函数复刻；Exa cached/index/location/image后端语义明确unsupported，不静默丢参数或列passed。
- 私有金融/体育云数据、云账户权益、模型质量/缺失模态单列；不由换模型取得，也不把本地后台命令/队列/目标因此排除。其他模块不因叫“云”便自动豁免。

## 资产、安装与回滚

活动`native/runtime*.json`为标准artifact manifest，ready只指本地资产契约；未来v0.6.8 URL当前不保证存在。三原tar在完整库存/hash/模式/recipe检查前后SHA/大小/身份未变；Windows未重包Unix原tar。原receipt和完整许可证随各包保留。

原0.6.7三manifest逐字节归档于`native/rollback/0.6.7/`，原recipe为`native/runtime-overlays-0.6.7-acc16483.json`；旧资产须匹配旧tag，不能冒充新guarded资产。[旧许可证补充](../0.6.7/attribution-correction.json)仍保留。

首次Windowsfull的2550pass/6fail/4skip原样保留：5项子pytest缺QA框架、1项junction夹具用UTF8解GBK stdout。仅临时为未激活QA prefix加入单行外部框架路径，修夹具bytes捕获，6原失败targeted通过后完整复验；最终CAS撤销该单文件，原5042文件完全一致。没有改产品、资格器、权限、断言或skip。

按[0.6.8操作方案](../../../docs/release-0.6.8.zh-CN.md)先新root/no-activate验证；正常安装不编译Rust。owner完成独立CDS gates、发布原字节资产并下载校验后再决定tag/main；不先公开再把未验范围写成全过。
