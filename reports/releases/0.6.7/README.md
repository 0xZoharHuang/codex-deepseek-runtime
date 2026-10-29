# 0.6.7 验收范围与保留失败

本轮修复两处具体问题：严格 JSON Schema 最终输出前，允许原生客户端工具搜索的合法中间调用；以及压缩历史冷恢复后，容量检查点因默认 metadata 表示不同而无法匹配的问题。最终 Schema 校验仍然保留。工具、审批、线程和压缩仍由原生 Codex 执行。

## 固定范围

- Codex CLI 0.164.0-alpha.7，源提交 fe136d4e70ef2b5bbed40a2d22b3aabf267796d3。
- 当前代码验收源 ce701f7f9c4f2dd333db53c4e1a1779db646f9e4；最终发布还会加入本报告和实际产物 manifest。不能把新增文档/产物清单误写成当时的原测试源。
- 模型固定 deepseek/deepseek-v4.1-flash-1017；模型质量、模型不支持的模态及 OpenAI 私有云服务/账户权益单列。
- 本轮没有本机 Mac 测试。macOS 和 Linux 构建、运行发生在 GitHub 官方临时 runner；Windows 开发和安装验收在独立目录完成，没有改变防护设置或正在运行的旧 CDS。

## 已完成的验证

| 层次 | 结果 | 边界 |
| --- | --- | --- |
| Windows 新原生 CLI，普通用户 token | 22/22 原生传输/恢复用例通过，零跳过 | 包含三个真实 CLI 进程、连续两次冷恢复/413/原生压缩/后续完成；不使用模型 |
| Windows 当前安装版完整离线套件 | 2388 唯一用例，2384 通过、4 个既定系统不适用项、零失败 | 使用已有管理员测试 token 构造 symlink 和 BA/SYSTEM owner 等对抗 fixture；不等同普通用户 runtime 执行 |
| macOS arm64 GitHub runner | 22/22 原生用例；2366 个 adapter 用例中 2321 通过、45 个系统不适用 | [原运行](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40730907886)；无提供方凭据/真实模型 |
| Linux x64 GNU GitHub runner | 22/22 原生用例；2366 个 adapter 用例中 2322 通过、44 个系统不适用 | 同一固定运行；当前 GNU 包不是 musl 静态包 |
| 三平台源码与包 | 完整源码清单、补丁重放、外部 Cargo 依赖不变、所有实际包文件哈希通过 | Windows 上只读核验 Unix 包，不执行 Unix 二进制 |
| 真实 DeepSeek 当前安装版冷续跑 | 顶层工具搜索与默认 Code Mode 两条路径通过 | 同一旧UUID、两个冷进程；旧nonce只来自保存历史，MCP每阶段一次，最终严格JSON；原始失败单列 |

Windows 普通 token 首轮全套出现 15 个 fixture 构造失败：11 个 Win1314 符号链接权限错误和4个 Win1307 owner 赋值错误；另3项因同类权限被既有测试跳过。没有放松断言、加入新 skip 或改变系统设置。随后管理员 fixture 运行的同2388个ID中，这18项均实际执行通过，原失败保留。4个最终 Windows 不适用项为两项 POSIX shell/shebang、原生 Unix symlink extraction 和 Linux daemon identity。

私有外层控制器还有一次额外失败：把仅适用 Unix CI 的 Windows-only skip 目录加到了 Windows 预期。实际 pytest 和公开严格结果 reader 当时已通过；此处用原 reader 独立回读，同一原始 XML 和 origin 记录验证，没有重写原结果。27个产品模块、4774次 import origin 检查均来自安装产物。

## 容量恢复的改动

这不是另写一个 agent loop。已有扩展在原生 runtime 内持久化容量检查点、epoch/fence 和恢复证据，然后调用原有 compactor；新补丁只修正 metadata 的哈希投影及固定旧格式验证。

新 v3 将缺省 metadata 与序列化往返得到的 typed Default 归一。非默认和 opaque metadata、内容、工具调用 ID、顺序与媒体仍严格比较。仅接受新 v3 或两个固定 v2 形式；不枚举任意 metadata 组合、不编辑历史/检查点、不以最终任务成功掩盖中途失败。

旧 v2 runtime 不能读取新 v3 检查点；降级后容量恢复可能明确失败，历史保留。不要手改 checkpoint 来制造兼容。新常规完成会记录自己的正确 epoch。

Windows 只重新编译 CLI；现有5个 helper 的原始 producer/哈希留在 build receipt 的 reused_components，不把它们冒称为新源码重新编译，也不承诺未来防病毒判定。Linux/macOS 本轮是完整新 native 构建。

## 真实任务证据的使用

0.6.6 的真实 Jinja 修复、独立审查、网页访问、权限与清理记录仍保留为各自固定版本证据。本轮只在未变化组件有明确来源比对时复用，不能把旧运行标成新安装版全绿。新的真实模型冷续跑已完成；最终发行安装回读另列，不把历史复杂任务重标成新的单场运行。

## 本轮真实模型与失败归因

安装 source ID d537072503fac0f3e59c，资格源码9d7f037023d25dbad62335fac0ffc29ed66be07b。普通 token 用新CLI冷恢复同一旧线程，旧nonce未重放到新提示。顶层client tool_search的call ID、输出、namespace MCP调用与最终Schema匹配。默认CodeModeOnly则通过原生exec的ALL_TOOLS发现并调用MCP。六个generation元数据均为 deepseek/deepseek-v4.1-flash-20261017 和Morph；严格JSON场景在发送前明确选择morph/fp8，allow_fallbacks=false，没有失败后换模型或提供方。

最初控制器漏带旧实验的一次性code_mode_only=false覆盖，恢复了既有CodeModeOnly默认。原生按设计隐藏顶层搜索，MCP仍在exec内可用。模型反复网页搜索的失败保留；实际MCP initialize/list_tools三服务器均通过，请求工具目录与源码证明了模式差异，未改产品掩盖它。随后分别验证原顶层模式与真正默认模式，Code Mode没有被禁用。

默认模式第一次JavaScript把flat工具名当namespace，原生返回TypeError且MCP未执行；模型同turn修正后调用一次并完成。它证明错误传播/恢复，不是所有步骤一次通过，single_run_all_steps_passed=false。

两个CLI均exit0。provider沿用默认120秒idle，119.778秒后正常退休；173.12秒观察结束时forced/remaining/unknown/errors均为空。home FID、旧历史前缀、403MB native与产品源码不变。

## 证据索引

以下哈希对照本地保留的原始记录；公开native归档另含自身完整build receipt、文件清单与许可证。不会公开凭据、未脱敏会话或截图。

| 原始证据 | SHA256 |
| --- | --- |
| Windows新native22项 | 6eac84cbed9c28f59b110c3f27ea09013aa331989c2e93b16c68f9587ce9a00f |
| Windows完整安装版严格回读 | 7d53bce3dcc295bc7c5d85ad0f949ca40718ea58dfe9bf23ad77466bc9a61c62 |
| 真实两路径独立语义验收 | 002fb59ff63b3a29f7e15c338d2d9529430ef2040dbc3cc6efd397572a2966bb |
| Root真实验收41引用逐字节核验 | b86a0632a58f049167e3195169e1bf225f74901c9c2bd3b774913c7df3da1deb |
| Windows完整source/package audit | b623ce07760bd14095f7a80d895ceb8fbfa8be4cdd5e39dc84044f35bedf1b73 |
| Linux完整source/package audit | 9b9b6878108023d72b4897e46ad09e0a564d91e2ac52ce498de039b59b8522a9 |
| macOS完整source/package audit | 8186fba55ee1d5070afa6ff1a072a8178833f8af9b9831c8921adfbaf784d9dd |

当前native/runtime*.json固定三平台最终归档。打包只封装已逐字节核验且实际验收过的native字节、完整receipt和许可证，没有重编译或在本机执行Unix代码。

## 最终发行安装回读

最终源码f7394e0ffd128bb5032eb20991e4dfcf2522a11f以普通用户、正常安装器、全新root及 --no-activate 安装，source ID为98e6b2d9c29cdb24a52d。venv明确使用既有独立CDS Python base，没有CADSG依赖；原base2932个文件元数据及核心二进制哈希在安装和运行前后不变，旧入口/home/活动进程未切换。

正常安装产生的wheel与两次独立staged构建逐字节一致：762352fe471c3242dca0172d015a0d3cc86927b25aaa4312b36fad0148c7bb35，167895字节。ZIP CRC、全部57条RECORD哈希、0.6.7包元数据和29模块均校验。29模块/native/catalog与完整测试及两种真实工具模式的已验安装产物相同；只有本版元数据和说明更新。

新独立home无schema的doctor只发一次请求，返回精确CONNECTED。请求固定provider.only=[deepseek]、allow_fallbacks=false，generation元数据核对相同response ID、官方DeepSeek和固定canonical模型。未把strictJSON的Morph路线冒称官方DeepSeek。

--version、原生exec --help及身份检查均exit0。保留默认120秒idle，120.270秒自然退休，观察器exit0，forced/remaining/unknown/errors均为空。安装/doctor两个Limited broker身份、session和退出均核验，专属任务已删除。

最终receipt SHA256：b75c10a3854ae12a24d188e16439716b04dfbf2f9b7f2fdd8ee52bf374914d3e；Root另逐字节重核其30项引用。这个附录只增加报告，不改变任何产品模块、README、native manifest或source ID。
