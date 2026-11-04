# CDS 0.6.10：Linux 长历史恢复与显式 peer 路由

**Linux 本地资格已完成，公开交付由实际发行回执确认。** Linux 独立发布，不等待 Windows/macOS/App；最终 tag/commit、asset ID 与下载/安装验证由公开交付回执给出，本文不预先声明未来 URL 已验证。

## 本次做了什么

CDS 仍运行原生 Codex CLI `0.164.0-alpha.7` / `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`，模型固定 `deepseek/deepseek-v4.1-flash-1017`。

容量失败走既有原生恢复/压缩入口。对已确认历史中的单个大图片输出、并行工具输出和大 UTF-8 文本，压缩器可分段读取可追溯视图；真实工具结果和当前未确认输入不被改写。新摘要必须承接全部来源，最后才安装一次。失败探测没有实际缩小 native 请求时停止，不用无限试探伪造成功。

冷恢复的本地 reset 保留旧 assistant gap 和当前 suffix；需要后续 fresh summary 时，只能凭同一次原生 ACK/checkpoint CAS 生成、同 turn 消费一次的证明继续，不重开 epoch/fence，不拿当前输入当已确认历史。取消与原生 writer 的持久化顺序有实际 token/强制 abort/写入屏障证据。没有另建 agent loop、审批器、工具调度或子智能体系统。

纯 Python 0.6.10 增加逐 home 显式 `peer_hosted_provider`，可选 `morph/fp8` 或 `primeintellect`。字段缺省时保持原 schema 1、默认请求和规范化 digest；修改沿用 gateway 锁、revision CAS、active/lease 守卫，不增加隐式 fallback。普通缺省仍官方 DeepSeek，peer 选择不改变模型、搜索、协作、reasoning、权限和最终 Schema 校验。

## 取得的实际证据

| 范围 | 已完成的事实 | 不能据此外推 |
| --- | --- | --- |
| native 容量机制 | 同一封存程序新 3 项与原 64 项两组通过；不是一批 67。 | 不是生产 CLI 已覆盖所有工具/故障。 |
| 正常 Linux P35 CLI | opt3 正常构建、完整 native 包、普通安装；新 CLI 原 14 项全通过；严格 old P27/orphan/CP6 两次冷恢复、固定 6 图预算覆盖全部 14 图、current/fence/once/EOF。 | 不扩大到其他平台或任意历史。 |
| 公开取消场景 | 8 场景无错误/未安装摘要；其中 2 个公开 receive window、6 个 early cancel。精确 token 证明另在原生测试。 | 不称 8 个精确 native-token 时间窗。 |
| 新 Python | Linux 新正常 0.6.10 release 的 32 模块来源/SHA 前后一致，297 相关回归全过、零跳过；原 HTTP helper。原 P35 native 目录/文件和旧 release 原样保留。 | 不是借 Windows 140 当 Linux 通过，也不是重跑 Rust/64/14。 |
| 使用者长期历史 | 318,538,647 字节旧历史前缀 hash 保持；同历史出现 compacted（技术回执计 11 项）并后续 12 个普通 DeepSeek completed，路由 official_default/deepseek。 | 历史字节数不是 HTTP 请求体；不据此称所有工具或业务成果通过。 |
| 真实 96 图/Jinja | 96 张原图与汇总严格匹配、原 PNG 不变；912 项工作区与 908 项独立 heldout 均零失败/错误/跳过；6 次自然压缩、cold4/8、真实只读子线程新最终复核 turn、中断后两份独立 ledger 各一次均通过。 | 保留 R443 等原失败和修复；多次受控继续完成，不称单次连续75分钟或新建reviewer线程。 |

原始 7/7、11/3、63/1、provider 错误、测试夹具/控制器错误和观察不确定性均保留。各 cohort 不相加成全能力覆盖率。技术摘要/receipt hash 见[索引](../reports/releases/0.6.10/README.md)；不公开真实业务正文、会话标识、私人路径或凭据；原生回归夹具中的合成数据与自有 QA 标识保留用于复现。

实际大图片请求有 66 个唯一成功的主账本/图片账本/native 完成关联，其中 62 个普通请求、4 个压缩请求；关联按 request ID 去重，不把两个账本重复相加。5 个选定真实 generation 的 GET 元数据核对为固定 DeepSeek / Morph 与准入快照；这是抽样身份核对，不称全部请求逐次发票审计。173 个实际工具调用的回读审计没有发现读取 gold、controller 或 credential 目标。

旧 EOF 中断只证明关闭连接，原事务的单次 append 仍保留。另一个独立新事务中，控制器发出真实 `turn/interrupt` 后、cold08 的正常 EOF 关闭之前，观察到该 Linux worker 退出，第二份 ledger 也恰好一次。这是该次实测结果，不代表中断 turn 会终止所有可恢复后台终端；后台终端的列出、按原生 processId 终止与统一清理是独立的原生接口。`turn/completed` 的 `interrupted` 状态本身不是全部子进程退出的证明。原超时标准未改，最终复核是在原生既有只读子线程发起新 turn；没有用探针或旧复核替代。

已知完成请求的图片保护 pin 为 0，未知上传资产为 0；3 个消费未知请求的 36 个 pin 仍受保护。有界正常 reconcile 没将它们伪装成已知或强删。最终资格明确保留这项云端不确定性。

## 安装和同 home 升级

正常安装不编译 Rust。只使用本版匹配源码与 Linux manifest/归档，保留原 SHA/receipt/许可证；[稳定方案](STABLE_PLAN.zh-CN.md)给出 `--native-manifest`、`--native-archive`、`--no-activate` 标准入口。

已有任务保持原 home/UUID/工程。目标 home 的 task、daemon、worker 与 lease 正常退役后，由新不可变入口 `runtime status` 通过既有锁与启动逻辑迁移 dead descriptor，再读回 environment/native_binary/code_id。status 会读取正常凭据并启动本地 provider，不是完全只读；它不发送模型推理。不要手删 descriptor、数据库、lease 或绕过 owner guard。

真实长图片任务验证的是普通 Morph + peer Morph。等待 home 自然退役，按[稳定方案](STABLE_PLAN.zh-CN.md)分别用新 revision 执行普通 Morph 选择和 peer default：

```sh
CODEX_DEEPSEEK_HOME="$CDS_HOME" "$CDS_ENTRY" runtime provider set morph/fp8 --if-match "$CDS_REVISION"
# 重新 provider status 读取新 revision 后：
CODEX_DEEPSEEK_HOME="$CDS_HOME" "$CDS_ENTRY" runtime provider set-peer-hosted default --if-match "$CDS_REVISION"
```

下一次正常启动采用这份快照。Prime 仅保留已验证的受限文本 peer 兼容范围；真实22图续接400保留，不作通用多图兼容声明。恢复 peer 默认使用新入口的 `set-peer-hosted default` 和刚读回的 revision；该步骤应在旧源码回滚前、home 完全退役时完成。没有以删除字段文件或忽略 guard 代替正常管理命令。

## 平台与权益边界

0.6.10 本次仅准备 Linux x86_64 GNU。Windows/macOS 的 v0.6.9 tag/入口和原资格保留；新 tag 不携带冒称 ready 的旧平台 manifest。Windows 候选构建/GUI、macOS 新资格和独立 CADSG 另行闭环。

原生本地 runtime 机制是目标；模型质量、模型固有模态及 OpenAI 云端账户/私有金融体育等数据服务不由本包创造。搜索、网页 open/find/click 与能力限制按已有组件证据和明确错误处理；不通过禁用搜索、Guardian、记忆或协作来缩小范围。

公开 URL 的实际下载结果、asset ID、tag commit 与发布后安装回执由外部交付记录确认。私有 native archive 已验证，不代表拟定的 GitHub URL 已下载成功；正式晋升后按实际记录补齐索引，不移动旧 tag 或把旧 CI/retained pin 改名当新资格。
