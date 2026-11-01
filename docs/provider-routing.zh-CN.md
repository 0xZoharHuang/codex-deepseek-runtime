# 每个独立 home 的显式提供方配置

此入口需要包含 `runtime provider` 命令的 CDS 版本。它固定使用同一个
DeepSeek 模型，只为普通请求显式选择已资格的 OpenRouter 提供方：
`deepseek` 或 `morph/fp8`。缺少配置时仍使用官方 `deepseek`，不读取新的全局
环境变量，不修改其他 home、Codex App 或 OpenRouter 账户设置。

先按现有方式选择你自己的 CDS home，再查看状态。例如 PowerShell：

```powershell
$env:CODEX_DEEPSEEK_HOME = "D:\OwnedProfiles\deepseek-work"
cds runtime provider status
```

输出中 `configured_provider` 是文件中的选择，`configured_config_sha256` 是
规范化配置快照的 SHA256；`configured_source_sha256` 是实际文件字节哈希。
`configured_revision` 是用于条件修改的版本标记，文件不存在时为 `absent`。
正在运行时另列实际已采用的 `admitted_provider` / `admitted_config_sha256`。
缺少旧版服务的证明字段会显示未知，不推测成官方提供方。

在没有原生任务、网关监督进程或服务 lease 的情况下修改：

```powershell
cds runtime provider set morph/fp8 --if-match absent
```

已有配置时，把 `absent` 换成刚查看的 `configured_revision`。也可省略
`--if-match`，命令仍在现有 `gateway.lock` 内执行读取版本比较与原子替换；
如果在等待锁期间配置发生变化，会拒绝写入。未知字段、重复 JSON 键、未知
提供方、链接或读取中的文件变化均拒绝，不以默认值掩盖错误。

配置仅位于该 home 的 `provider-state/provider-routing.json`，格式只有：

```json
{"schema":1,"ordinary_default_provider":"morph/fp8"}
```

活动 home 会拒绝修改，并且不会终止任何进程。先正常结束该 home 的原生
会话，再等待现有服务自然 idle 退役（默认约 120 秒）；不要修改超时、关闭
权限或杀掉其他任务来绕过检查。成功后下一次正常 CDS 启动，在同一把锁内
读取一次快照；监督器、重启后的 worker 以及同 home 的主任务、原生子任务、
Guardian、记忆和压缩请求共用这份已采用的快照与哈希，不按请求重读或热切换。

该选项只替换原路由最后的普通默认分支。显式串行工具仍优先 Prime；原生
严格结构化请求及需要兼容性的 peer/continuation hosted-tool 路径仍按既有
规则选择 Morph。模型、工具、auto 选择、历史、reasoning、权限及原始最终
Schema 校验不变。官方普通容器 Schema 路径保留已资格 JSON mode；非官方
普通路径保留完整 Schema 指令与原始请求的最终校验，不宣称上游解码约束相同。

每次请求仍是单一 `provider.only`，`allow_fallbacks=false` 且
`require_parameters=true`。429、502、取消或无效输出不会改用另一提供方。
普通 Morph 的回执明确记录 `operator_default/morph/fp8`；
`runtime status`、认证 health 与 doctor 显示实际已采用的提供方和快照哈希。
doctor 同时强校验真实 generation ID、固定模型及对应已资格的 billed provider，
不会把“有文本”当成提供方验证成功。账户允许列表或隐私规则不满足时保留
真实上游失败，不自动放宽。某提供方曾通过资格不代表此刻一定可用。

恢复官方提供方同样等待该 home 完全退役，再使用当前 revision：

```powershell
cds runtime provider status
cds runtime provider set deepseek --if-match <configured_revision>
```

失败的修改不会改写原配置。切换不会迁移、清空或合并原生线程及数据库，
也不会把先前官方请求失败改记成兼容提供方通过。
