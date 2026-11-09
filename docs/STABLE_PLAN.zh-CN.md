# CDS 按平台安装、升级与回滚方案

Linux x86_64 GNU、macOS Apple Silicon 和 Windows x86_64 使用 v0.7.2。Windows 使用 P41，Linux 和 macOS 沿用 P35。升级已有图片缓存前，需要先让该 home 的所有原生进程和网关自然退役，再用新入口运行 `codex-deepseek runtime image-cache-format --to 3`。需要回到 v0.6.15 时，先用新入口运行 `codex-deepseek runtime image-cache-format --to 2`，再切回旧入口。[格式迁移、回滚和失败处理](image-cache-formats.zh-CN.md) · [v0.7.0 实测回执](../reports/releases/0.7.0/README.md)

v0.6.15 的三份清单分别沿用 Windows v0.6.14 的 P39 和 Linux、macOS v0.6.13 的原生包。请核对 [v0.6.15 分平台验收记录](../reports/releases/0.6.15/README.md)。需要回滚时，Windows 固定 v0.6.14，Linux、macOS 固定 v0.6.13。Windows 的受管用户如果需要升级既有 home，先按[原生维护指南](windows-managed-maintenance.md)确认目标账号和目录，再用该版原生工具执行一次维护。保留原安装、会话与回滚入口。

P39 的变化集中在 Windows 沙箱的目录元数据与权限维护。它复用原生工具、沙箱、会话及子智能体机制。Unix 原生组件、DeepSeek 模型路由和协议行为沿用各自已发布的固定组合。CADSG 的桌面 App 有独立安装和验收记录。

## 安装大文件时断线

一次安装遇到可恢复的网络中断时最多尝试三次。续传从第一个尚未收到的字节开始。如果服务器忽略续传请求并返回完整文件，安装器会从头写入。终端会显示已接收字节数。下载完成后仍要校验归档 SHA-256 和逐文件清单。权限或限流错误、完整文件摘要不符会立即失败。退出后重新启动安装会使用新的临时文件。若网络频繁中断，可先用支持续传的下载工具取得平台清单中的固定归档，再按 README 的 `--native-archive` 方法安装。

## 原生机制与模型的分工

```text
用户 / 外层 agent
       |
       v
CDS 独立入口与 home
       |
       v
原生 Codex CLI
  +-- 工具、审批、沙箱、会话、压缩、Skills/MCP、子智能体
  |
  +-- Responses/事件与历史适配 -- 原生 HTTP -- OpenRouter -- 固定 DeepSeek
```

不另写 agent loop、工具调度器、审批器或子智能体系统。历史容量恢复只给原生压缩器提供可验证的历史视图；当前未确认输入与真实工具结果保持，完整来源被承接后才提交。模型输出质量、模态与 OpenAI 云端账户权益不属于协议适配能保证的能力。

## 先取得匹配资产，再正常安装

使用独立 Python 3.11+。Linux 实测为 Ubuntu 24.04 GNU/WSL，保留 glibc、OpenSSL 3、libcap 与捆绑 bwrap 条件。正常安装不编译 Rust，也不用 Codex App 内置 Python。

Windows x64 的 v0.6.14 已从公开 tag 和默认归档 URL 在独立 Windows runner 上完成无代理安装、文件核验和原生启动。目标 Windows 10 机器的首次直连下载在 900 秒内仅收到部分字节，同机改用独立出口后完成。连接较慢时，也可先下载同一发行页的归档，再用该 tag 的 `python install.py --native-archive "<归档路径>"` 省去重复下载。安装器仍核验完整 SHA-256。已有任务继续使用原入口，新 root 可配合 `--no-activate` 独立安装。[公开下载回执](../reports/releases/0.6.14/publication-receipt.json)

从本平台 tag、平台 manifest、原生归档和 SHA-256 组成匹配集合。Linux v0.6.10 已有独立的公开下载和安装回执；Windows v0.6.13 的公开 URL、asset ID 与下载后安装结果由其发布回执确认，本地已有归档并不等于 URL 已验证。v0.6.13 提供三平台正常默认 manifest，明确引用既有不可变归档。

```sh
# 从相同版本源码目录执行；验证目录应此前不存在。
CDS_CHECK_ROOT="$HOME/.local/share/codex-deepseek-0.6.10-check"
python3 install.py --root "$CDS_CHECK_ROOT" --no-activate   --native-manifest /verified/runtime-x86_64-unknown-linux-gnu.json   --native-archive /verified/codex-deepseek-native-0.6.10-x86_64-unknown-linux-gnu.tar.gz
```

安装器继续核对 target、recipe、工具链与完整清单。选择安装输出中记录的新不可变入口，显式指定自己的 `CODEX_DEEPSEEK_HOME`。`--no-activate` 不切现有命令入口，但会按正常安装规则建立该 root 的默认 home；不把它误当不会写任何目录。

`--version` 无需 key；`runtime status` 会读正常凭据、打开 Store 并进入 provider 生命周期；`runtime doctor` 实际请求模型并计费。key 只走系统凭据、隐藏输入或受控进程环境，不进 argv、配置、仓库或日志。

## 同 home 延续历史

1. 保留原 home 根目录身份、UUID、rollout、图片账本和工程成果；备份不能改变正在使用的路径身份。不复制 Windows sandbox 秘密/账户资源到另一个 home。
2. 先正常结束目标 home 的任务、子进程与 daemon，并确认 PID 加 birth 的实际所有者。未知 owner/lease 不能被当成已退出，单独停 daemon 不代表全部工作结束。
3. 使用图片缓存的旧 home，先在所有 native/provider 退役后用新入口执行 `runtime image-cache-format --to 3`，确认返回码 0、`complete=true`；这是不读取 key、不启动 provider 的本地维护。然后使用新 release 的绝对入口和原 `CODEX_DEEPSEEK_HOME` 执行 `runtime status`。正常 gateway 锁/启动逻辑可以迁移已终态旧 descriptor 的 environment、native_binary、code_id；保留迁移前后回读。它不发送模型推理，但会启动本地 provider。不要手删 descriptor、改 PID、手改 checkpoint/数据库或绕过 guard。
4. 如果需要更改 provider，等这次 status 客户端及 provider 正常 idle 退役后再 CAS；默认 idle 约 120 秒，不靠改 timeout 通过。不要反复调用 `runtime status` 来等它闲置。
5. 使用同一新入口、同一 home 执行原生 `resume <UUID>`，由实际普通完成、工具效果和工程检查确认继续成功；状态输出和 doctor/probe 都不代替真实 reviewer。

## 已验证的长图片组合、显式 peer 与回滚

普通提供方的既有选择保持；缺省仍为官方 `deepseek`。新的 `peer_hosted_provider` 是 schema 1 可选字段，缺省不存在时旧 payload、格式及规范化 digest 保持。peer/continuation hosted-tool 分支可明确选择 `primeintellect`；单请求仍 `provider.only`、`allow_fallbacks=false`、`require_parameters=true`，不因错误自动换端点。

真实 96 图任务最后采用普通 `morph/fp8` 与 peer `morph/fp8` 的同一准入快照。普通默认官方 `deepseek` 仍保留；这是用户逐 home 的显式选择，不是全局改默认或自动 fallback。对长图片任务使用下面已经验收的组合：

```sh
# CDS_ENTRY/CDS_HOME/CDS_CHECKS 先设为已核对的入口、原home和自己的记录目录。
# 仅在此 home 的任务、客户端、provider与lease完全退役后操作。
CODEX_DEEPSEEK_HOME="$CDS_HOME" "$CDS_ENTRY" runtime provider status > "$CDS_CHECKS/provider-before.json"
CDS_REVISION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["configured_revision"])' "$CDS_CHECKS/provider-before.json")"
CODEX_DEEPSEEK_HOME="$CDS_HOME" "$CDS_ENTRY" runtime provider set morph/fp8 --if-match "$CDS_REVISION"
CODEX_DEEPSEEK_HOME="$CDS_HOME" "$CDS_ENTRY" runtime provider status > "$CDS_CHECKS/provider-before-peer.json"
CDS_REVISION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["configured_revision"])' "$CDS_CHECKS/provider-before-peer.json")"
CODEX_DEEPSEEK_HOME="$CDS_HOME" "$CDS_ENTRY" runtime provider set-peer-hosted default --if-match "$CDS_REVISION"
```

`default` 删除可选 peer 字段，采用原缺省 Morph 路由。每次修改后都重新读取 revision，不重复使用旧 revision。Prime 的受限文本 peer/parent payload 及 generation 已有证据，但真实 22 图续接失败为 400；不要据文本 probe 把所有多图组合切到 Prime。

`CDS_ENTRY`、`CDS_HOME`、`CDS_CHECKS` 须由操作者设置为已核对的绝对入口、原 home 和自己的记录目录。CAS 冲突或 active/lease 拒绝时保留失败，重新核对；不手工改 JSON 或停止未知进程。

回滚到不支持该可选字段的旧源码前，先在 home 完全退役后用新入口删除该字段：

```sh
CODEX_DEEPSEEK_HOME="$CDS_HOME" "$CDS_ENTRY" runtime provider status > "$CDS_CHECKS/provider-before-rollback.json"
CDS_REVISION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["configured_revision"])' "$CDS_CHECKS/provider-before-rollback.json")"
CODEX_DEEPSEEK_HOME="$CDS_HOME" "$CDS_ENTRY" runtime provider set-peer-hosted default --if-match "$CDS_REVISION"
```

`default` 恢复旧 schema 可读格式和规范化策略 hash；普通 provider 选择不被暗改。程序回退与数据回退分别判断，保留旧 tag、不可变入口、原始失败与匹配 native/许可证。不要移动已公开 tag、把新 SQLite 回灌旧 home，或承诺旧程序能读取所有新版扩展历史。

## 失败与清理

区分适配缺陷、原生平台限制、上游服务错误和模型生成问题；保留 HTTP/流失败、中间失败、副作用与最终结果。318 MB 本地历史恢复证据不表示发送了 318 MB HTTP 请求，也不表示无限上下文或业务结果全通过。96 图任务在保留原失败的多次受控继续后完成，原 phase timeout 没有延长，不称一次连续 75 分钟运行。

Linux/WSL 超时保留原时间界限，先看实际导入/函数/等待位置及 guest RAM/PSI，不跳过验证、改全局资源或降权限标准。默认 workspace-write/on-request、Guardian、Hooks、搜索及协作保持。

最后仅清理已结束且身份确定的本项目缓存/工作树/临时工具。原始证据先压缩逐文件校验，保留用户工程、会话、必要回退件和未确认的远端图片清理账本。不碰其他用户、WSL 发行版、App、Defender、账户或代理。

## 图片账本的已知与未知

本次真实任务结束后，本地 native/provider 均已退役。已知完成请求的保护 pin 已为 0、未知上传资产为 0；仍有 3 个云消费结果未知的请求，36 个 pin 按既有 fail-closed 规则保留。它们分别来自原 EOF、新事务的计划取消，以及缺 generation ID 的 502。一次有界正常 reconcile 后仍未知，没有强制清除，也不能宣称全部云文件/账本已清空。

保留这些回执与保护 pin，后续只使用正常图片状态/维护入口核对；不要手删数据库、清全部缓存或把未知消费改成已成功删除。该云端不确定性不等于本地进程仍在运行，亦不被最终任务成功抹去。

## 0.6.10 公开交付回读

Linux 0.6.10 已正式发布。公开资产下载及默认 manifest 的正常隔离安装已通过；固定源码为 `6d6ccd32c92d06fc52ecd72712140f24496c30a2`，安装 CID 为 `2752f58dbefb9114c9a8`。参见[发布回执](../reports/releases/0.6.10/publication-receipt.json)及[发行页](https://github.com/0xZoharHuang/codex-deepseek-runtime/releases/tag/v0.6.10)。Windows/macOS/CADSG 的后续验收保持独立。

Windows 上的异步 Python 工程可能需要本机回环。受管配置、实测的允许与拒绝结果见 [Windows Python 回环说明](windows-python-loopback.zh-CN.md)。


## 图片缓存格式的受控升级与回滚

图片缓存格式 3 使用单图操作锁和短共享账本事务；不会把 Files API 的等待串在所有图片的共享锁内。旧 home 升级前先自然退役并用新入口显式 `runtime image-cache-format --to 3`。回滚到格式 2 旧程序前，先退役全部新 reader，再用新入口显式 `--to 2`；它保留升级后新增的数据，不覆盖旧 DB 快照。未知或活跃 owner 阻止迁移，已证退出但消费结果未知的 pin 原样保留。中断后的 transition 不能当 ready；恢复同一次维护并完成核对后再启动。[完整机制与步骤](image-cache-formats.zh-CN.md)。
