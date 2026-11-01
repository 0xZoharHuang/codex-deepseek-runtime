# CDS 0.6.9：存储兼容与显式提供方配置

本版固定候选的本地 runtime 资格已经完成；公共交付与日常切换仍待实际回执确认。使用 Codex CLI `0.164.0-alpha.7` 的原生工具、审批、沙箱、会话和协作，固定模型为 `deepseek/deepseek-v4.1-flash-1017`。本说明不是 tag、main、远端资产或 Codex App 已通过的声明。

## 修复与配置

原生 SQLx 按 SQL 字节计算 checksum。同一 SQL 的 LF 与 CRLF 不同，旧源码构建包与官方 Windows 包曾在两个方向真实初始化失败。P27 只在共同迁移入口选择逐版本已识别的精确形式，然后仍由 SQLx 执行原迁移。

新建 Windows 存储使用官方 CRLF；既有一致 LF / CRLF 保持自身形式，空辅助库继承已确认的主库。未知 checksum、未完成迁移仍失败；不手改历史 checksum，不换数据库引擎。上游已有 legacy 38→39 的版本/描述修复保留。已识别混合账本可按版本处理，但不承诺未打补丁旧程序能够读取任意混合形式。

新增逐 home 的普通提供方选择，见[配置说明](provider-routing.zh-CN.md)。缺省仍为官方 `deepseek`，可显式选择已资格的 `morph/fp8`；固定模型和已有 serial／strict／peer 路由优先级不变。配置写入与服务使用同一锁和已验证快照，活动 home 拒绝修改。每个请求仍限定单一 provider、关闭 fallback、要求参数支持；没有自动故障切换或通过伪造 serial/schema 选择另一条路由。

## 本版实际验收

产品 Source ID 为 `8e26c81c987b7502ac96`，31 个产品模块和 113 项安装身份输入已逐项核对。三平台 P27 原包来源、recipe、编译器、原 receipt、完整清单分别审计；Windows 还完成六阶段真实新旧二进制互读，保护每个 profile 已初始化的五库账本、schema 和 typed 用户数据。未创建的 history 不算互读；原生状态测试另覆盖六类存储。

| 验收宿主 | 当前完整集合 | 边界 |
| --- | --- | --- |
| Windows 10 x64 | native22＋软件 2703 正向／4 合法跳过＝2729 | 安装版软件为原全套与精确夹具修复复验的合成，原 15 个失败保留；不是全套均在 Limited 运行。 |
| GitHub-hosted Linux x64 | native22＋软件 2652 正向／55 合法跳过＝2729 | 新 Python 资格使用原已资格 P27 字节，没有重新编 Rust。 |
| GitHub-hosted macOS arm64 | native22＋软件 2651 正向／56 合法跳过＝2729 | 没有在维护者本机 Mac 构建或运行产品。 |

原 State22／Guardian10 与 filtered 项、平台专用 skip、编译警告和短命进程 uncertain 记录单列，不加入当前通过总数；296 项 CI 控制器检查有一项与主集合重复，也不追加覆盖。具体身份、计数和源承接见[资格索引](../reports/releases/0.6.9/README.md)。

官方提供方曾使真实 nonce 任务及相同小请求的普通/native 对照均失败为 502；原失败和 30 条回执保持失败。正常显式选择 Morph 后，实际计费 Doctor、工具读取、Guardian、原完整 schema、同原 UUID 自然退役后冷恢复均通过。模型、history 和权限没有为过关被替换；不是失败后自动换 provider。

另一个实际已知工程任务完成三源文件修复、主/子/孙协作、MCP 官方文档导航、Stop/同 UUID 继续，以及独立 930 节点零失败验收。69 个同模型/Morph 请求中 68 个完成、1 个计划取消。子任务曾违背文字“只读”要求并撤回修改；其 OS 权限本来为 workspace-write，不能称只读沙箱越权。工程结果不等于盲测模型能力或 App 验收。

## 安装、升级与回滚

实际 v0.6.9 tag 和本平台公开资产经发布者确认后，使用相同版本源码的标准 `install.py` 入口；普通安装不编译 Rust。升级先在独立 root 以 `--no-activate` 验证，确认本 home 的任务、daemon 和 lease 正常退役，再正常切换入口。不要结束未知归属进程或全局结束 codex.exe，不操作其他用户或 Codex App 的订阅、配置和会话。

旧 0.6.8 一致 LF 数据由新版保留 LF，旧程序重开有独立真实记录；新版新建 Windows CRLF 可由匹配官方包读取，但不承诺旧 LF 构建包读取它。程序回滚与数据回滚分别判断，不能手改迁移 checksum，或把新 SQLite 回灌旧 home。

截止本说明的资格版本，日常入口升级、远端 tag/main/资产完整读回，以及发布后永久资产 CI 仍待交付回执。永久下载必须保存原 tar/receipt/资格证据；临时 Actions 输入不会被当成长期稳定渠道，发布后的正式 pins 只取真实资产元数据，不造占位 ID。

## 范围

本版承接固定原生 runtime 的机制，并对存储和提供方变更分别留有来源、影响验收与原失败。原有搜索、Skills/Hooks、MCP、恢复和诊断仍按实际组件证据及[使用方案](STABLE_PLAN.zh-CN.md)操作；不声称所有接口在每个平台都重新测过。OpenAI 云端账户权益、金融体育私有数据、模型固有模态与质量单列；独立 CADSG / Codex App 的 P7 和 GUI 验收没有被本发行说明代替。
