# 委派权限、本地工具与运行中控制

适用于本项目固定的 Codex `0.164.0-alpha.7`。委派控制器应明确保存进程身份、工作目录、CDS home、工具链和权限选择；父任务当前拥有的权限不会自动覆盖新 CLI 的参数。

本次问题的两个独立原因已经复现：

- 父任务允许 full access，但子 CDS 显式使用 `-s workspace-write`。Swift 缓存写入及原版 REA 的 Unix socket 都受子任务自己的原生沙箱限制。
- `queue` 调用的是 `thread/queue/add`。`Queued` 仅确认进入持久队列，当前 turn 继续原来的工作符合该命令的语义。

修正的是启动配置和调用方法。agent loop、沙箱、queue、steering 及 REA transport 保持原实现。

## 已验证的原版 REA 启动方式

对于用户已经授权的可信本地开发工具任务，可明确选择 `danger-full-access`，并保持原来的 OS `HOME`。使用独立工作目录与短、无点前缀目录段的临时目录。`CODEX_DEEPSEEK_HOME` 是 CDS 会话目录，与 OS `HOME` 不同。

控制器的参数示例：

```text
[actual_cds_executable,
 "-a", "never", "-s", "danger-full-access",
 "-m", "deepseek/deepseek-v4.1-flash-1017",
 "exec", "--skip-git-repo-check", "--json", "-C", owned_work_dir, "-"]
```

提示通过 stdin 输入。只给这一进程设置任务需要的环境；不要改用户全局配置：

| 项目 | 本次验证使用的配置 |
| --- | --- |
| Node | 绝对路径，版本 24.19.0；REA 6.4 的 engines 为 `^22.19.0 \|\| ^24.11.0 \|\| >=26.0.0` |
| REA | 原版 6.4.0，commit `58e91e14bd5659dd57acbb91e56b5fc6a6963c80`，入口 `scripts/rea.mjs` |
| Ghidra | 12.1.4，通过 `GHIDRA_INSTALL_DIR` 指定原安装目录 |
| JDK | 通过 `JAVA_HOME` 指定已有兼容 JDK |
| Java heap 配置 | `GHIDRA_HEADLESS_MAXMEM=512M` |
| REA 启动预算 | `REA_GHIDRA_STARTUP_TIMEOUT_MS=180000` |
| 临时目录 | 由控制器创建的私有 `TMPDIR`，不改变 `HOME` |
| 模型凭据 | 使用已获授权的进程环境或既有运行时存储；值不进 argv、提示词、报告或日志 |

REA 命令由 **CDS 的原生工具执行**，例如：

```text
[absolute_supported_node, original_rea_entry,
 "function", owned_binary, exact_symbol_or_address,
 "--provider", "ghidra", "--format", "json"]
```

本次真实验证由 DeepSeek 驱动 CDS 创建一个小型 C 程序、编译并调用一次原版 REA。Ghidra 返回的目标 SHA 与实际文件一致，恢复出 32 字节函数体、8 条指令和 `x * 7 + 11`；原始 REA 的 1223 个所选源码/运行时文件前后哈希一致。没有用外部 host 代跑 REA，没有替换 Unix socket 为 TCP/file bridge，没有关闭验证。

实际测试使用已有 macOS CDS 安装（adapter source `98b24d5adbcf145b99ee`），原生版本仍为 `0.164.0-alpha.7`；它验证这次观察到的启动路径，不冒充一次新的全平台发布验收。测试中的 model effort 为 medium，其他研究任务可保留自己的已支持 effort。

## 保留 workspace-write 时

`-a never` 不会替失败的沙箱操作请求提权。遇到确定的权限错误，应停止重复启动，向控制器报告原始错误，再按授权调整调用配置。

两个权限面要分别处理：

1. 文件写权限：REA 6.4 的 `DarwinProcessRunTokenReader` 会删除继承的 `CLANG_MODULE_CACHE_PATH`，使用 Swift 默认缓存。只设置该变量不能保证重定向成功；不应通过改 `HOME` 来掩盖问题。受限任务需要给实际缓存路径明确的原生文件权限。
2. 本地 IPC：文件目录可写不等于允许 Unix socket。固定原生版本通过选中的命名 permission profile 的 `network.unix_sockets` 表达目录授权；只有正确加载的原生网络策略才生效。不能把其它版本的旧键名直接照搬，也不能用开放 TCP 或关闭 REA 校验替代 Unix socket 权限。

本次另外用极小、无模型的原生 `command/exec` 测试验证了精确目录授权；它与上面的 REA 全链路验收分开记录。受限配置的完整 REA 工作流没有在本次重跑，因此不要把该 OS 探针当成完整 REA profile 已验收。

## Queued、steer 与 interrupt

```text
queue
  -> thread/queue/add
  -> 持久队列
  -> 可调度的 idle 状态下启动新 turn

turn/steer
  -> 原 app-server 的当前 active turn
  -> 接受输入并在原生可抢占边界交给模型

turn/interrupt
  -> 请求取消当前 turn
  -> 等待原生 interrupted 终态
  -> 另外核对已启动的后台进程
```

`Queued` 不是投递到当前 turn 的回执，也不是模型已遵循指令的证明。重复 queue 会积累未来输入，不能用它停止正在进行的动作。

固定版本的 queue watcher 对已加载 thread 检查持久队列，Running 不被 queue 抢占，Interrupted 也不会仅因中断就自动启动排队任务。已经排队的指令可能在后续显式恢复后被消费。控制器应按自己保存的 queuedSubmissionId 核对过期项，不能无差别清空其他来源的队列。

## 现有 exec --json：中断后恢复

`exec` 自己持有 in-process app-server，不接受根 `--remote` 来接管别人的 active turn。`exec --json` 的 `thread.started` 有 thread_id，但 `turn.started` 不给 active turnId；它也不输出 app-server 原样的 interrupted `turn/completed` 对象。

对于已经这样启动的任务：

1. 原控制器用持有的进程句柄或核对过创建时间的 PID，向原 exec 发送一次 Ctrl-C/SIGINT。
2. 等待原进程退出。此版本主动中断可以返回 exit 1；不能只看非零退出码便推断工具失败。
3. 核对已经留下的后台工具，避免旧 REA 仍在执行时再启动第二份。
4. 保持原 `CODEX_DEEPSEEK_HOME` 和工作目录，用准确 UUID 恢复，并把最新指令作为新输入：

```text
[actual_cds_executable,
 "-a", "never", "-s", "danger-full-access", "-C", original_cwd,
 "exec", "--json", "resume", exact_thread_uuid, "-"]
```

以上 argv 已经过固定原生 parser 验证。`-a/-s/-C` 不要放到 `resume` 后；`--json` 是 exec global 参数。`-a` 是根选项，headless exec 在该版本本身默认 Never，不是 resume 的独立审批开关。

并发任务不要使用 `--last`。不要往 exec 的 stdin 写 JSON-RPC，并假定它是 app-server 控制连接。本次诊断没有恢复或操作原研究任务。

## 长期控制器：从开始持有 app-server 连接

需要运行中 steering 时，让控制器从创建任务起持有 `codex-deepseek app-server` 的原连接，或原来使用的共享 daemon 连接。相同 home 的持久记录不等于相同的内存 owner；另起一个 app-server 不能据此接管旧 exec。

完成 `initialize` / `initialized` 后，保存 `turn/start` 返回的 `turn.id`，或原始 `turn/started` 事件中的 ID。需要实验 API 时声明 `capabilities.experimentalApi=true`。

追加当前轮输入：

```json
{"jsonrpc":"2.0","id":21,"method":"turn/steer","params":{"threadId":"<thread-id>","expectedTurnId":"<active-turn-id>","input":[{"type":"text","text":"<latest instruction>"}]}}
```

核对响应的 turnId。错误的 expectedTurnId 会被拒绝；接受输入后还应观察实际后续行为，不能把接受回执等同于完成执行。

明确取消：

```json
{"jsonrpc":"2.0","id":22,"method":"turn/interrupt","params":{"threadId":"<thread-id>","turnId":"<active-turn-id>"}}
```

等待同一 turn 的 `turn/completed`，确认 `status="interrupted"`。已运行的后台终端使用该 owner 的 `thread/backgroundTerminals/list`、精确 `terminate`，或授权范围内的 `clean` 单独处理。

固定源码中 `features.instant_interrupt` 是 under-development、默认 true，具体 profile 可以覆写。它作用于 active-turn 输入的采样抢占，不会把 queue 变成 steer，也不会保证杀死已有工具进程。此版本没有顶层 `cds steer` CLI，也没有注册 `thread/input/append`。

## 验证范围

[本次脱敏验证记录](../reports/integrations/rea-delegation-20261022.json)保留实际版本、正负控、原始证据 SHA 和验收边界。

- 真实 DeepSeek → CDS → 原版 REA/Ghidra：1 次 REA 调用、7 个完成的模型请求、原始函数结果与文件效果相符。
- 原生 queue/steer 受控正负控：3 次本地固定响应请求，验证 queue 仅在新 turn 消费、steer 同 turn 进入下一请求、错误 turn ID 和未注册 API 被拒绝；没有外部模型调用。
- CLI 参数：3 个正确位置与 3 个错误位置的 parser 检查。
- 对已有研究任务的具体 queue 消费时间、当前 feature override 和全部后台进程状态，本诊断没有越界推断。

依据：[官方 app-server API](https://learn.chatgpt.com/docs/app-server#api-overview)，以及固定上游提交中的 `ext/queue/src/service.rs`、`exec/src/lib.rs`、`exec/src/exec_events.rs`、`core/src/session/input_queue.rs`、`sandboxing/src/seatbelt.rs`。本仓库的 CDS wrapper 原样转发这些管理命令，没有把 queue 改写为 steer。
