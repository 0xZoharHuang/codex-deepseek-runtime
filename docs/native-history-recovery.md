# 固定 v0.6.0 的原生历史恢复

本文记录 Windows x64 上的合成实测：CDS v0.6.0，安装源码身份 2d43ed50050d0a687f55，原生 Codex 0.164.0-alpha.7 / fe136d4e70ef2b5bbed40a2d22b3aabf267796d3。使用的是公开发布包读回安装中的原生 CLI，SHA256 为 739617253b01787c644cb72c704fee290e53b49ba32d6e7ae6d86691744c0e64。

[精简结果与原始证据摘要](../reports/releases/0.6.0/native-history-recovery.json)单独保存本次结果。它补充固定版本的恢复边界，不扩大原有三平台验收范围；本次未在 macOS 或 Linux 重跑，也没有迁移任何真实业务会话。

## 为什么新 home 能读到 rollout，却不能 fork

独立 CODEX_DEEPSEEK_HOME 同时隔离会话文件和原生 ThreadStore 索引。对于 historyMode 为 paginated 的线程，给 thread/fork 或 thread/resume 一个可读的绝对 rollout 路径，并不会把原线程导入新 home 的 store。

固定原生实现先按路径读取元数据，再按其中的原线程 ID 查询当前 store，校验分页历史的当前路径。缺少索引时，会返回：

~~~text
-32600: no rollout found for thread id <source-thread-id>
~~~

此时还没有走到 beforeTurnId 的截断处理。同一个分页源在全新 home 中，不带截断点的 path-only fork 和 path resume 也会失败。非空 path 确实优先于请求中的 threadId；但它不免除源元数据 ID 在当前 store 中的索引要求。实测中，在持有索引的 home 里，即使传入另一个合法格式的 threadId，正确 path 仍定位到原源线程并成功 fork。

这来自保持上游原字节的 [read_stored_thread_for_resume](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/app-server/src/request_processors/thread_processor.rs#L4886) 与[分页 fork 分支](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/app-server/src/request_processors/thread_processor.rs#L5306)。本次核对的四个关键源码文件均与固定官方归档逐字节相同，没有为恢复重写 runtime 或修改数据库。

## 推荐流程：在原索引中 fork，只继续新子线程

这里的“原 home”是目标 CDS 线程已有的专用 home，不是另一份 Codex App 或订阅配置。新工程目录可以通过 cwd 单独指定；新工程目录与全新会话 store 是两个不同的隔离范围。

使用原安装、原 home 的原生 app-server 连接，启用实验 API，并正常发送 initialized 通知。以下是 JSON-RPC 请求形状，路径和 ID 应替换为实际值：

~~~json
{
  "id": 1,
  "method": "initialize",
  "params": {
    "clientInfo": {"name": "native-history-recovery", "version": "1"},
    "capabilities": {"experimentalApi": true}
  }
}
~~~

~~~json
{"method": "initialized"}
~~~

从原生 thread/turns/list 获取已持久化的真实 turn ID。beforeTurnId 是**排除边界**：该 turn 及之后的所有 turns 都不进入新子线程；它不是包含边界，也不是消息 ID 或文本位置。

~~~json
{
  "id": 2,
  "method": "thread/fork",
  "params": {
    "threadId": "<original-thread-id>",
    "beforeTurnId": "<first-turn-to-exclude>",
    "cwd": "<absolute-new-owned-workspace>",
    "excludeTurns": true,
    "deferGoalContinuation": true
  }
}
~~~

权限覆盖不是恢复的必要条件，正常使用保留原线程的权限与审批。本次合成测试显式使用 workspace-write / never，只生成静态文本历史，没有请求执行工具；这不是建议用户改为 never 的配置。

excludeTurns 仅省略响应中的 turns，不会清空 fork 的持久化历史。用返回的新线程 ID 查询 thread/turns/list，分页核对保留的前缀，并核对 forkedFromId 和新 rollout 路径。deferGoalContinuation 推迟原生目标的自动续跑；它不意味着以后显式启动新 turn 不会产生模型请求。

本次原线程有三轮真实原生 canonical turns。排除第二轮后，新子线程只保留第一轮，API 和子线程自己的 SessionMeta 都确认父子关系。确认前缀正确后，只对返回的新子线程继续工作。不要为了注册索引而 resume 原源路径，也不要对原线程执行 revert。

fork 会在原 home 的数据库中正常增加新子线程、历史与元数据；**整个原 home 并非只读**。本次保全断言针对原父线程的 rollout 原字节，不能扩写为数据库、目录或所有文件完全不变。也不要直接复制或手工修改 SQLite/history 来补索引。

## 先全量 fork，再回退新子线程

如果先生成了完整历史的新子线程，固定版本的原生接口是 thread/revert，不是 thread/rollback：

~~~json
{
  "id": 3,
  "method": "thread/revert",
  "params": {
    "threadId": "<new-child-thread-id>",
    "beforeTurnId": "<first-turn-to-exclude>"
  }
}
~~~

本次对新分页子线程实测成功：线程 ID 保持不变，原生创建新的 rollout 代路径，thread/turns/list 只剩第一轮；原父 rollout 保持原 SHA。thread/revert 的响应 turns 本来就是空列表，应通过 thread/turns/list 重新读取，不能据此误判历史丢失。该接口修改持久化会话历史，不会回退工程中的文件修改。

调用 thread/rollback 的原始 unknown variant 错误保留在私有回执中，没有将它计为成功或静默换接口。接口定义见固定版本的 [ThreadRevertParams / ThreadRevertResponse](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/app-server-protocol/src/protocol/v2/thread.rs#L1288)。

## legacy 的不同边界

原生 thread/start.historyMode 可以显式选择 legacy；本次在 SQLite 仍正常启用时实际创建了 legacy 与 paginated 两种线程。legacy 源在新 home 中的 path + beforeTurnId fork、path-only fork 都成功，且 fork 操作本身未改变源 rollout。

但是，对 legacy 源执行 path resume 会实际改写源 rollout。即使没有真实模型采样，也不能把这个步骤称为“只读注册索引”。本次故意验证的可写源全部是可丢弃的合成会话，原始失败和前后 SHA 均保留。

选择 legacy 只影响新建线程的原生历史契约，不会追溯转换已有分页线程。本文不修改默认 historyMode、不关闭 SQLite，也不承诺把现有分页线程迁移到全新 home。不要为绕过错误静默共享 sqlite_home：CDS 的隔离配置会绑定其专用 store，这会改变隔离范围。

## 本次实际验证范围

- 三个本任务原生 app-server 都自然退出 0，没有强制结束共享进程。
- 六个初始回复由本地静态 Responses fixture 生成，供原生 runtime 创建 canonical turn IDs；没有 OpenRouter、DeepSeek 或其他真实模型推理，也没有凭据读取。
- 恢复阶段只调用原生 fork、revert 和读回接口，没有启动新模型 turn；未手写或编辑数据库、rollout/history。
- 全新 home 的分页失败、旧接口名失败、legacy resume 改源都作为原始观察保留。
- 推荐的 indexed-home fork / child-only revert 流程直到进程退出后，原分页父 rollout SHA256 始终为 3349952cb6cb391dffa85cf9161a273193e60af0c73060185302b964bc9be009。
- 这证明固定 Windows 组合下的合成原生恢复机制与边界，不代表真实业务会话已恢复、工程文件已回退，或全新 home 的分页迁移已经获得支持。
