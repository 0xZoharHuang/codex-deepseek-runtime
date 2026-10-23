# 0.6.0 候选：安装、图片传输与 Windows 隔离

**0.6.0 尚未发布。** 现有 v0.5.0 稳定安装请从对应 tag 取得源码；本分支的安装条件和版本状态见 [README](../README.md)。本页说明候选已有的源码行为、如何隔离验证以及发布前还需要哪些证据；没有可供引用的 0.6.0 release 下载或完整三平台验收。

## 这次改变了什么

- 可选的无损图片传输：原生历史保留原图，只有实际 HTTP 请求按字节预算改为私有文件引用与内联图片的混合形式。
- HTTP 413 保留为请求字节错误。没有 Retry-After 的这类错误不再盲目重试；有明确重试建议的路径及其他原生重试继续由 native runtime 处理。它不被伪装成 token 溢出，也不替代原生压缩。
- Windows sandbox 的账户、网络对象和所有权按 home 身份区分；新补丁另检查实际 WFP readiness，并修复受保护 home 下 skills、插件资源及两版 memories 的只读访问。最终允许、拒绝和恢复效果仍需在对应新包上验收。
- 构建、打包和安装使用平台对应的 native recipe。新源码拒绝旧版或不同 recipe 的 `ready` manifest；原有不可变安装继续使用自己的文件。

线程、命令工具、审批、沙箱执行与子智能体仍由原生 Codex 管理。模型仍固定为 `deepseek/deepseek-v4.1-flash-1017`，提供方选择沿用 [README 的路由表](../README.md#为什么有时不是官方-deepseek-路由)，并禁用 provider fallback。图片传输不改变路由优先级；一次官方 DeepSeek 图片探测不代表所有路由组合均已实测。

```text
稳定入口 + 原 home                         保留可用

新候选入口 + 新 home
    -> 匹配平台 recipe 的原生包
    -> 可选图片配置 -> 原生 HTTP -> OpenRouter
    -> 候选自己的会话、账本和 Windows sandbox 资源
```

## 安装候选时保持稳定入口可用

先取得针对当前源码和平台验收的本地 manifest 与归档。源码 recipe、build receipt、包内文件及归档哈希必须对应；旧 v0.5.0 manifest 不能给 0.6.0 授予安装资格。不要仅把旧清单里的版本或 `status` 改成新值。

选择此前不存在的新安装目录，使用独立 Python 3.11+。以下是待替换路径的 Windows 示例，须在匹配候选包具备安装条件后执行：

```powershell
$CandidateRoot = 'D:\path\to\new-cds-candidate'
python install.py --root $CandidateRoot --no-activate --native-manifest 'D:\path\to\qualified-local-manifest.json' --native-archive 'D:\path\to\qualified-local-archive.tar.gz'
```

离线依赖可另外传 `--wheelhouse <目录>`，仍按 `requirements.lock` 校验。归档必须是清单匹配的文件；安装器拒绝越界归档名、重定向或共享的缓存文件，异常缓存保留供检查。

`--no-activate` 保留用户的全局入口，但仍会初始化并保护新 root 中的 `codex-home`。不要把旧安装根传给首次候选验证。根据新安装根下 `releases/<source_id>/installation.json` 记录的具体 release 选择入口，显式指定新 home；不要原样使用下面的 `<source_id>` 占位符：

```powershell
$CandidateEntry = 'D:\path\to\new-cds-candidate\releases\<source_id>\venv\Scripts\codex-deepseek.exe'
$env:CODEX_DEEPSEEK_HOME = Join-Path $CandidateRoot 'codex-home'
& $CandidateEntry --version
& $CandidateEntry runtime status
```

若交付提供了已绑定 home 的 profile launcher，优先使用它的绝对路径。通用 release 入口不会自动覆盖旧的 `CODEX_DEEPSEEK_HOME`；候选验证期间不要用仍指向稳定版的全局 `cds` 别名。macOS/Linux 的不可变入口形式为 `releases/<source_id>/venv/bin/codex-deepseek`，同样需要匹配平台的候选包和明确 home，尚未通过的平台不能由 Windows 结果推定可用。

`runtime doctor` 会调用真实模型并核对提供方记录，产生 API 用量；它应使用已授权的正常凭据机制。凭据设置沿用 [README](../README.md#key配置与-codex-app-隔离)。安装本身、离线版本输出与一次 doctor 成功分别证明不同范围，不能互相代替。

## 配置图片传输

缺少配置时功能关闭，沿用原有 JSON 请求路径。先结束选定 home 的活跃任务和服务，再在该 home 的 `provider-state/image-transport.json` 写入仅供所有者访问的普通文件；修改配置不会热替换活跃 profile 的 worker。

```json
{
  "schema_version": 1,
  "enabled": true,
  "native_outbound_policy": "default",
  "activate_above_bytes": 16777216,
  "wire_budget_bytes": 31457280,
  "idle_ttl_seconds": 3600,
  "verify_after_seconds": 60,
  "max_file_references": 16
}
```

这里的 16 MiB 启用阈值和 30 MiB wire budget 是示例中的用户预防策略，**不是实测 provider 请求上限或 token 上限**。`native_outbound_policy` 的 `default`、`default-fallback`、`system` 是原生出站代理路由选项，名称中的 fallback 不表示允许更换模型提供方，也不是工具权限令牌。

传输选择预计节省字节最多的图片位置，最多使用 16 个文件引用；调用者已有的引用也占位置，重复图的每个引用位置分别计数。其余图片继续内联，图片原字节、顺序与 detail 不变。无法满足配置预算时显式返回 413，不缩图、不丢图、不用摘要代替。原生历史仍保留原图；图片传输自己的账本不另存原图字节。

配置关闭时使用完整的最小对象，不保留其他未生效选项：

```json
{"schema_version": 1, "enabled": false}
```

关闭功能不证明以前上传的文件已删除。应先处理下文的清理或保留尚未解决的回执。

## 查看、清理与恢复图片资源

以下命令都应在**候选自己的入口与 home** 下运行；例如 Windows 用 `& $CandidateEntry` 替代 `codex-deepseek`：

```sh
codex-deepseek runtime image-status
codex-deepseek runtime image-cleanup
codex-deepseek runtime image-cleanup --all-unpinned
codex-deepseek runtime image-reconcile-uploads
codex-deepseek runtime image-reconcile-calls
```

| 命令 | 含义 |
| --- | --- |
| `image-status` | 查看本 home 的状态；信息命令退出 0 不代表远端对象已清空，关闭时也可返回 disabled。 |
| `image-cleanup` | 按 TTL 清理当前 credential scope 中已知属于自己的、未锁定的对象。 |
| `image-cleanup --all-unpinned` | 不等待 ready 对象的 TTL；仍保留已锁定、未知上传或未知消费状态。 |
| `image-reconcile-uploads` | 在有界元数据分页中对照已有私有上传记录；唯一匹配只用于清理，不被当成已验证内容缓存。 |
| `image-reconcile-calls` | 用已有回执及必要的有界 provider 元数据核实消费终态；不重放推理请求。 |

除 status 外，命令仅在对应维护操作完成时返回 0。pending、未知结果、服务错误或超时需继续保留证据；“这次维护完成”也不一定等于所有对象已删除，应同时查看 `remaining_assets`、`foreign_scopes`、锁定状态及 `all_owned_files_deleted` 等适用字段。

启用的服务每 60 秒执行有界 TTL 维护，每次最多处理 2 个对象、最多 10 秒；遇 Files 错误停止本轮，并保留安全的 Retry-After。后台维护只处理已知 owned IDs，不扫描账户全部文件、不发模型请求。

只有已验证的 provider 终态或明确 HTTP 拒绝才能解除消费锁定。客户端取消、500、截断的流或上传结果未知，都不是安全删除的证明。DELETE 之后还要确认 GET 返回不存在；清理失败保持待处理，不会伪装成成功。

账本位于 `<home>/provider-state/image-transport/`，绑定 credential scope 与 canonical cache root。**不要复制或移动账本到另一 home 后接管其云端对象。** 锁、salt 与账本拒绝链接、junction 和硬链接；正常同根 Windows 大小写或扩展路径别名可继续使用。空的旧未绑定账本可初始化，非空未绑定账本会保留并拒绝自动采用。更换 key 不会用新 key 清理旧 scope；它会报告仍待处理的其他 scope。

图片 owner 记录采用内核进程身份，避免系统时钟校正造成仍在使用的锁被误清理。匹配当前根目录与 credential scope 的已绑定账本可从内部版本 1 升至 2；旧数值型 owner 记录保留，存活不明时不会删除。升级后旧版读者会明确拒绝该账本，不能将新锁误判为过期。该内部版本与上面的配置 schema_version=1 不同。使用旧入口回退时应回到其原 home；不要将版本 2 账本交给旧实现，也不要删账本来绕过拒绝。

删除本地 cache、安装目录或配置文件都不会替你删除远端文件。应保留账本与回执，直到所属对象已确认不存在，或明确保留未知状态以便后续处理。

## Windows 升级和回退

新 namespace 依据 canonical home、目录身份和用户 SID 派生独立资源。创建了同名但不同身份的 home，也不能假定能够继承旧 sandbox 账户。不要把旧 home 的 `.sandbox`、`.sandbox-secrets`、`.sandbox-bin`、provider-state 或会话数据库复制给候选。

本阶段优先使用新 profile。回退时只结束候选自己的任务、子进程和服务，保留候选回执，再继续使用原稳定入口与原 home。无需用旧版的 machine-wide `sandbox uninstall` 清理候选；旧命令可能操作共享机器资源。新命名空间的清理也必须使用匹配入口、明确 home 和完整所有权记录。

尚无自动原子迁移旧 home 的命令。未来若要保留同 home 的会话进行升级，需要先确认该 home 的整棵原生进程树退出，保留 home 根目录身份，记录 `.sandbox*` 的受保护备份与恢复步骤；不得改动其他 home 或旧共享账户、规则。详细运维边界见 [并行候选流程](parallel-candidate-upgrade.md) 和 [Windows namespace 说明](windows-sandbox-isolation.md)。

## 当前证据与发布前待完成项

候选已有部分 Windows native helper、真实图片请求、413/压缩、namespace 与网络实验，以及 Python/文件系统复核。它们分别绑定当时的组件和源码，不能合并成“最终 0.6.0 已通过”。原失败保留；一次真实图片请求只能证明其具体模型、提供方和请求路径。

可公开阅读的解释入口：

- [图片传输与分阶段取证](payload-capacity-source.md)。
- [初始隔离缺陷与修复边界](payload-isolation-audit.md)、[Windows namespace](windows-sandbox-isolation.md)、[四类 artifact 只读目录](windows-artifact-read-roots.md)。
- [native platform CI](../ci/README.md) 与 [workflow](../.github/workflows/native-platform-qualification.yml)。固定候选源码的[实际 CI 运行](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/39899815305)已完成；macos-15 arm64 使用 1 个构建 job，ubuntu-24.04 x64 使用 2 个。CI 的 synthetic transport 通过不代替真实模型、安装或平台权限验收；后续源码变化按[本版本验收记录](release-0.6.0-acceptance.zh-CN.md)单独核验。

发布前仍须把下列项目绑定到最终提交和产物，补入可公开、去除私人信息的 receipt；本页不预填通过结果或下载 URL：

| 待补项目 | 要记录的事实 |
| --- | --- |
| 最终源码与包身份 | 提交、版本、平台 recipe、工具链、build receipt、archive SHA-256 与完整文件清单。 |
| Windows 最终候选 | readiness、offline/例外网络、四类 artifact 首次读取与拒绝边界、冷恢复、清理，以及匹配新安装入口的实际结果。 |
| 新安装与代表性工作 | 实际安装、明确入口/home、模型及提供方记录、原生工作流效果、失败与恢复，分别说明 runtime 与业务任务结果。 |
| macOS/Linux 新产物 | 实际 CI run 与归档身份、目标系统兼容性，以及各平台尚需的安装、权限、恢复和 runtime 验收。源码 patch replay 不替代执行证据。 |
| 发布与回退材料 | 最终公开报告、release/tag 与真实归档地址、稳定入口切换和可核查回退步骤。 |

维护者的私有项目证据保存在 `.evidence/payload-capacity/`。版本冻结、独立复核、Windows build/acceptance 与 CI 准备记录是定位线索；公开发布应整理必要的脱敏摘要，而不是上传原始模型上下文、真实 home、密钥或完整私有日志。

`package_native.py` 在库存、哈希与 recipe 校验后写出的 `status: ready` 是安装清单字段。它自身不读取完整产品验收，也不发布 GitHub release；维护者必须在对应验收闭合后才把新清单晋升为公开平台入口。旧 v0.5.0 的 manifest、tag 和报告保留原身份。

## OpenRouter 402：账户余额与密钥额度

HTTP 402 是提供方支付／额度边界，不能归类为上下文溢出、内容风控或沙箱失败。账户有余额时，单独 API key 的上限仍可能耗尽；并发请求的预估费用也可能暂时占满可用预算。OpenRouter 会按输入及允许的最大输出估算费用，错误里的“requested tokens”不代表模型已经生成或实际扣费了那么多 token。[官方额度说明](https://openrouter.ai/docs/api/reference/limits)

处理时先核对专用凭据的 GET /api/v1/key 返回的 limit_remaining、limit_reset，以及错误 metadata.limit_source。需要区分账户余额时再读取 /api/v1/credits。诊断只保存必要的数值与错误分类，不把 Authorization、密钥或完整账户标识放进命令参数和日志。Codex 订阅周额度与 OpenRouter key 额度相互独立。

- openrouter_key_limit：检查这把 key 的限额及重置条件；提高上限须由密钥所有者明确授权。
- openrouter_credits：余额或单请求预估成本不足；不能通过自动切换提供方、削减历史或默默降低输出预算掩盖。
- openrouter_in_flight_budget：按响应给出的 Retry-After 等待；没有等待建议的 402 不应被描述为“稍后必定恢复”。原始状态码、响应和每次原生重试仍须保留。[官方错误与重试说明](https://openrouter.ai/docs/api/reference/errors-and-debugging)

修复外部额度条件后，可从原来的 CDS 会话继续。提高额度不会自动完成、重放或迁移此前失败的任务；先回读原生失败终态与现有文件，再明确续接。现有凭据或额度不会由安装、诊断、重试过程自动修改。
