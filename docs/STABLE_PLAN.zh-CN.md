# CDS 0.6.8 稳定使用方案

本方案只管理CDS。Codex App/CAG独立验收，不迁移App订阅、配置或会话。原生Codex负责工具、权限、会话和协作，DeepSeek负责模型输出，云端数据/权益另按服务处理。

## 固定来源，再正常安装

1. 以[v0.6.8发布页](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.8)取得对应tag、平台归档和摘要；发布页/实际下载回执决定可用性，不混tag、manifest、recipe或同名旧二进制。
2. 使用独立Python3.11+与Git；正常安装不编译Rust、不使用Codex App内置Python。按[安装升级回滚方案](release-0.6.8.zh-CN.md)先新root与标准安装器`--no-activate`验证，保护当前入口和工作。
3. 选择新release绝对入口及显式`CODEX_DEEPSEEK_HOME`。`--version`与`runtime status`不等于模型连接通过；`runtime doctor`真实调用模型并计费。key仅走系统凭据/隐藏输入或安全进程环境，不进argv、仓库、日志。
4. 日常切换先核当前home真实任务、进程birth与所有者。旧descriptor的PID可能已复用，不能按数字kill。验收、任务协调后才正常安装器选择已核root。

## 保留原生机制和状态

默认workspace-write/on-request与实际审批按项目授权使用。不要为通过切full access、禁Guardian、改provider fallback或删历史。Direct/Code Mode、Skills/Hooks、MCP及子智能体按实际版本工具目录；V1/V2不盲混。

独立root使用独立home；延续任务保留home/UUID，并在该home任务及daemon真正结束后由新不可变入口执行原生resume。沙箱秘密/账户资源不直接复制新home。恢复失败保留原回执，不改checkpoint版本或把新SQLite回灌旧home。

Windows关闭兼容分支为process-local已识别方法修复，未变共享Python/loop策略。未识别实现明确NOT_APPLIED；安装器最低Python版本不代表每种解释器/OS组合获得同一资格。实际解释器与平台边界见本版索引。

## 失败、回滚和清理

区分上游模型错误、显式不支持的云后端语义、原生平台限制与适配缺陷。未知结果先查原run/thread/turn及副作用，不重放有副作用工作造全绿。

回滚保留旧tag、root/home和不可变入口，协调目标home后由旧版本正常入口选择；新会话/失败记录不倒退覆盖。旧`native/rollback/0.6.7`只保来源，旧资产配旧tag与许可证补充。

最后按实际owned目录清理下载/编译/QA缓存与多余工作树，先保原始证据/校验。纯重建pytest缓存仅在owner终态、无正引用、raw/XML/source外部保留且路径身份核验后退役；不碰模型工作区、用户工程、共享安装、Defender、账户或代理。

## 资格与历史

[0.6.8资格索引](../reports/releases/0.6.8/README.md)分列native、software、复杂任务、schema、skip及云能力；一次业务成功不等于所有模型任务正确。外部qualification/release回执绑定最终tag源码，不在文档内嵌自身未来SHA。

历史稳定方案原文附件：[旧0.6.6方案](archive/STABLE_PLAN-0.6.6-original.md.txt)。其中旧相对链接仅原始历史文字，不是当前操作入口。
