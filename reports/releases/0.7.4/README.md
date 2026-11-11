# CDS 0.7.4 协议输出边界维护候选

0.7.4 保留 P35/P41 原生运行时及固定模型、显式 provider 路由，只修复纯协议适配。原生组件继续负责工具、权限、审批、线程和恢复；没有新增 agent loop、调度器或加密实现。本说明随候选源码准备；只有最终提交在对应平台的 CI、正常安装和实际回归关闭后，才由该平台的 Release 说明开放交付。未开放的平台继续使用已发布版本。

## 修复及依据

- 固定 CLI fe136 与 App P167 c407 的 serde 会忽略无法解码的 output item，随后仍可能接受 response.completed。适配层现在在最终 done/terminal snapshot 前校验已知 message/reasoning 字段类型，拒绝 malformed annotations/logprobs、缺失 reasoning.summary 等，保留合法 nullable 字段、annotation 数组内的 opaque 内容和额外字段。
- 显式 in_progress/incomplete/failed 的 function、custom 和 client discovery 最终项不再进入原生工具提交；缺省/null status 仍可用。added、delta、arguments.done 继续是 preview，不因为暂时不完整就要求最终形状。
- response.incomplete 是非提交终态：原始 reason、response ID、usage 和截断工具参数作为诊断原样保留，不发新 done、不强制把截断 discovery JSON 解析为完整参数，也不将未提交搜索项写入 replay。先前已 done 项若再次出现须一致；不因不完整快照省略它而重复执行。P167 的 RawValue 仅用于失败 trace；P35/P41 不具备该 partial_output 字段，不借此声称其新能力。
- hosted web search 的已知 action 字段按原生 nullable string/string-array 校验；未知字符串 action 仍走原生 Other，额外字段原样保留。非法 final 在验证前不会写入或覆盖搜索 replay SQLite；合法 preview 的原有存储行为不变。
- DeepSeek 明文协作所需的缺省/null/空数组 encrypted_function_args 仍映射为原生 plaintext marker。显式非空或非法加密元数据在最终提交前报错，不能静默覆盖为明文；错误不回显其内容，也不尝试解密。

三个当前 native manifest 仅将逻辑 distribution_version 改为 0.7.4；原生归档 URL、SHA、文件清单、recipe、工具链与原生版本不变。此候选不需要重新编译 Rust。

## 已验证的范围与保留的失败

独立 Windows Session 2 普通权限环境使用原缓存 P41 HTTP transport，先保留失败，再修复并复测。纯协议形状/stream/history/断连/offline 组为352通过；初次最终 status 补充后直接相关组为269通过；补齐搜索 action、真实 SQLite 失败不写入和 encrypted collaboration 后，该组合为339通过；继续修复 incomplete 终态的兼容回归及截断 discovery 诊断后，最终组合为373通过、0跳过、0失败。各组绑定各自源哈希，不能把它们相加当成不同能力数量。测试仅用公开 synthetic fixture 和实际本地 loopback HTTP，不是上游模型或云服务实测。

原发布7ba的权威 final 形状负例为44失败；未完成工具提交负例18失败；搜索 action/cache 负例26失败；协作加密元数据负例24失败。新终态 guard 将未提交 incomplete 当成最终工具的兼容回归，先记录33失败/1通过，再按原生错误语义修复。先前 QA 的过严 added 假设、隔离导入路径遗漏和未绑定 native HTTP helper 的中止运行均保留。一个原成功 synthetic reasoning fixture 缺原生必需 summary，补为 summary:[]，原断言和 reasoning 内容不变；没有修改真实会话或补造原始工具结果。

最终 0.7.4 提交的 Linux/macOS 官方 CI、Windows 正常独立安装及相应实际回归仍须按最终身份完成；未在本页宣称已完成。旧32图/压缩/冷恢复证据保留原平台、原源码、原安装和路由条件，不自动成为本次新身份实测。CADSG/Codex App 需另行更新精确协议依赖和构建，并独立验收，CLI 结果不替代 App 结果。

[安装与同 home 升级、回滚](../../../README.md) · [既有稳定方案与历史证据](../../../docs/STABLE_PLAN.zh-CN.md) · [0.7.3 原始维护记录](../0.7.3/README.md)
