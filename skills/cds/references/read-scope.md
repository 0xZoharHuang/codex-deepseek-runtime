# 用户明确限定读取范围时

仅在用户明确说“只读这些目录／文件”“不要读取旧证据、设备原始材料或其他会话”等情况下使用本参考。普通 coding 任务继续使用已授权的正常权限；文件名中出现 private、设备或研究等词，并不会自动触发这套流程。

## 写入边界不等于读取白名单

legacy workspace-write 主要限制写入，并不自动限制只能读取 workspace。提示词里的禁止事项、rg 的 glob／ignore、约定不递归搜索，都是任务指令或搜索选择，不能代替原生文件系统策略。执行命令仍要显式指向获准路径；不要先扫整个 home，再靠结果过滤。

需要可执行的读取边界时，使用所选 native 版本支持的 named permissions profile，并用受控的正、负样例确认 OS 实际允许／拒绝。不要把读取拒绝改成 full access，或偷偷由控制器、另一个工具、MCP 服务绕过。

## 在独立 profile 中表达范围

以下是固定 fe136 / Codex 0.164.0-alpha.7 的 macOS 示例。所有路径都是占位符；先替换成自己新建、只含虚构 marker 的测试目录。用户真正的禁读材料不能充当试读样本。

把配置合并到选定 CDS home 的 config.toml；default_permissions 是顶层键，不能误放进前一个 TOML table。不要改正在工作的其他 profile 或全局技能。

~~~toml
default_permissions = "read_scope"

[permissions.read_scope.filesystem]
":minimal" = "read"
"/absolute/public" = "read"
"/absolute/single-set/allowed.json" = "read"
"/absolute/task" = "write"
"/absolute/private" = "deny"
"/absolute/device-evidence" = "deny"
"/absolute/other-session" = "deny"

[permissions.read_scope.network]
enabled = false
~~~

- 目录 read 授予该目录树的读取；单个文件应使用 literal 文件路径，不要为了读一个文件放开父目录。
- literal 文件应位于未被其他规则放开的目录。若父目录已经 read／write，单文件规则并不会收回父目录的权限。
- write 也意味着该写入区域可读。使用新的任务输出目录，避免把混有禁读材料的整个目录授予 write。
- 核对实际路径与 symlink 的目标，保留明确 deny；不要仅检查显示路径或 glob。
- 示例禁止工具网络。已有网络授权应按对应原生 network 配置处理，文件权限失败不是扩张网络权限的理由。

CDS home、任务目录和 OS HOME 是不同边界。用 CODEX_DEEPSEEK_HOME 选择新的独立状态，不改 OS HOME，不把它指向 Codex App 或含有其他任务历史的目录。

## exec 的选择方式与 sandbox 自验不同

在这个固定版本，-P 是 sandbox 自验的 permission-profile 参数，exec --help 没有对应 -P。执行任务时让 config.toml 的 default_permissions 生效，或使用受支持的 -c 覆盖；不要给 exec 编造 -P，也不要再传 -s workspace-write 把 named profile 覆盖掉。检查实际安装的 help 与有效配置，而不是只看配置文件存在。

下面是用户明确要求“仅用本地资料、工具不联网”时的 POSIX 示例，提示正文从 stdin 提供。模型仍通过已授权的 DeepSeek API 运行；如果用户禁止包括模型 API 在内的一切联网，就不能启动这个模型任务。普通读取范围任务若允许合格 MCP，不需要照搬此处的禁用项：

~~~sh
CDS_PROFILE_HOME='/absolute/owned-cds-home'
CDS_EXE='/absolute/verified/codex-deepseek'

CODEX_DEEPSEEK_HOME="$CDS_PROFILE_HOME" "$CDS_EXE" \
  -c 'default_permissions="read_scope"' \
  -c 'mcp_servers.codex_web.enabled=false' \
  exec --json -C '/absolute/task' -
~~~

CDS bootstrap 会生成 codex_web MCP。设置空的 mcp_servers={} 是配置 merge，不会清空已有服务器；对这类明确要求本地／严格读取的任务，显式禁用对应 server，并通过 config/read 核对它实际 enabled=false，同时核对所有其他暴露渠道。关闭这一项不等于关闭所有 MCP 或外部工具，也不是普通 CDS 的默认策略。

config/read 可能补充 schema 的可选默认 null。与输入 TOML 对照时只按已知 schema 规范化这些默认值，仍逐项比较实际 profile、路径、read／write／deny、网络与 MCP enabled 状态；不能删掉实际差异来制造相等，也不能把 false、空数组或不理解的字段当作 null 忽略。

不要恢复或 fork 含有禁读内容的旧历史来启动这个新 worker。已进入模型上下文的数据不会因后来收紧权限而撤回；新 home 也不等于把旧 thread 复制进去就变干净。只交付本轮获准资料、必要目标和结果路径。

## 先做无模型 OS 自验

从已核验安装的 metadata 取得配套 native 与随包 rg 的实际路径；不要用 PATH 中不明的 codex 或另一个 rg。用同一份虚构目录树、同一 profile 和相同身份自验：

| 自验 | 应观察的实际结果 |
| --- | --- |
| cat、随包 rg 读取 public 中的 marker | 成功并匹配预设 marker |
| cat literal allowed.json | 成功 |
| 读取同目录未获准 sibling | OS 拒绝，不能因为 allowed.json 可读便放开整目录 |
| cat／rg 访问 private、device-evidence、other-session | OS 拒绝 |
| 对 public 内指向禁读 fixture 的 symlink 使用随包 rg --follow | 仍被 OS 拒绝 |
| 在 task 中写入自有结果；在未授权位置写入 | 前者成功，后者拒绝 |

显式 sandbox -P 在此版本默认不纳入 managed requirements。若实际任务受组织／托管配置约束，使用该版本支持的 --include-managed-config 并核对最终有效策略；未包含 managed requirements 的单独 profile 自验，不能被称为与 exec 相同的有效策略，也不是绕过托管限制的授权。

例如，以下只针对虚构 fixture。-P 和 -C 属于 sandbox 自验；它们不是 exec 的参数配方：

~~~sh
CDS_NATIVE='/absolute/verified/native/bin/codex'
CDS_RG='/absolute/verified/native/codex-path/rg'

CODEX_HOME="$CDS_PROFILE_HOME" "$CDS_NATIVE" sandbox \
  -P read_scope -C '/absolute/task' -- \
  /bin/cat '/absolute/public/allowed.txt'

CODEX_HOME="$CDS_PROFILE_HOME" "$CDS_NATIVE" sandbox \
  -P read_scope -C '/absolute/task' -- \
  "$CDS_RG" --no-config --files --hidden --no-ignore --follow '/absolute/private'
~~~

记录每条命令的实际退出、stderr 和文件效果。预期拒绝不应有 marker 内容或文件名列表输出；单纯“没有搜索结果”不足以证明 OS 拒绝。配置读回、无模型命令拒绝、模型实际工作分别记录；不要靠真实敏感数据泄露后才判断配置错了。

本轮维护者使用固定 macOS native 8b16ba68…，在全新虚构 fixture 中做了无模型 OS 自验，覆盖上述 cat／随包 rg、单文件 sibling 与 symlink 逃逸边界。使用方另在新 home 运行了实际 DeepSeek 会话，其首条工具 canary 验证了获准输入、任务写入及明确拒绝项，并读回 read_scope／无 legacy override；这是使用方的模型 canary 回执，不能写成本仓库又重跑了整套模型或全工具验收。此证据只覆盖该 Mac 安装与所测本地工具路径。Windows 10 的 native 739… elevated 后端曾在执行前拒绝 minimal-read profile，要求 effective :root read；不能据此声称 Mac 不支持，也不能为满足用户读取白名单而加 :root read。Linux、其他 Windows 后端或未来版本须核对自己的原生支持并自验；Mac 结果不代替它们的实测。


## 解释器和动态依赖也必须在获准范围内

如果本轮需要可信 Python 等本地工具，允许解释器版本目录还不够：动态加载器读取的外部库、运行时模块以及依赖路径中的 symlink 也受原生策略约束。先在新的合成 fixture 验证，保留 dyld、ImportError 和 stderr；不要改 loader、复制解释器、关闭 sandbox，或为了一个工具开放整个 Homebrew／System Library。

固定 macOS native 8b16ba68… 与 pyenv Python 3.13.3 的实测中，可信 Python 版本目录之外需要以下依赖。下面是该机器当时的完整 Python 附加条目，可信 Python 根为占位符，库版本是实测值；它们不是通用安装路径。先确认自己实际安装版本、逻辑路径和解析目标，再合并到已有的 filesystem table：

~~~toml
[permissions.read_scope.filesystem]
# 同表原有规则必须保留；不要重复创建这个 table。
"/absolute/trusted/pyenv/versions/3.13.3" = "read"
"/opt/homebrew/opt" = "read"
"/opt/homebrew/opt/gettext/lib/libintl.8.dylib" = "read"
"/opt/homebrew/Cellar/gettext/1.0/lib/libintl.8.dylib" = "read"
"/opt/homebrew/opt/openssl@3/lib/libcrypto.3.dylib" = "read"
"/opt/homebrew/Cellar/openssl@3/3.6.5/lib/libcrypto.3.dylib" = "read"
"/opt/homebrew/opt/libb2/lib/libb2.1.dylib" = "read"
"/opt/homebrew/Cellar/libb2/0.98.1/lib/libb2.1.dylib" = "read"
~~~

这里有明确的额外可见范围：/opt/homebrew/opt 的读取会允许查看 alias 名称与链接。固定原生实现会 canonicalize read roots 并生成 subpath 规则，并没有可配置的 symlink metadata-only 许可。仅授库的物理目标时，逻辑 alias 路径仍曾被拒绝；上例补 alias 目录，不等于开放所有 alias 目标内容。维护者分别实测了逻辑与物理 gettext 库 sibling、另一 readline alias 的真实文件，以及自有 alias 指向 fake private，它们仍被拒绝。不要把这项结果概括为任意 symlink 或所有依赖都已通过。

Python 自验应覆盖实际 helper 需要的后端与算法，不能只看 import hashlib 或 exit=0。本次明确验证 _json、_struct、_hashlib、_blake2 的真实后端和全部 14 个 guaranteed hash algorithms，最终 exit=0 且 stderr 为空。早期只补 libintl／libcrypto 的一次运行虽 exit=0，仍有 Blake2 缺少 libb2 的错误；该失败保留，没有当作无降级通过。原 fake private／device-evidence／other-session、未列举 sibling 和越界 symlink 的拒绝也必须同时保持。

复杂工具还可能需要版本固定的可信 lib／data 目录，而不只是依赖文件。例如另一次完全虚构的 TShark 4.6.9 离线 pcap 自验中，file-only 规则不足以完成 dyld 的版本 alias 解析；已测方案允许原版 physical 可执行文件、该版本 lib 与 share/wireshark，以及 21 个关联库分布的 19 个固定版本 lib 目录和 opt alias namespace。它没有开放整个 Homebrew 或 /opt/homebrew/bin。通过官方 WIRESHARK_CONFIG_DIR 指向新建的自有配置目录，并用 -G folders 读回；合成 UDP 解码与自有 dmacros 实际执行成功，同时禁读 fixture、未列举 sibling、越界 symlink 和跨 alias 未授 header 均继续拒绝。这只是所测离线解析路径：SMI 默认关闭，optional extcap 的缺库错误另行保留，不能推成所有工具子功能均通过。按实际任务选择可信 runtime 边界并说明额外可见内容，不自动扩大读取范围或建立依赖绕过器。

若已有可用 Python，只计算摘要可以使用已获准 Python 的 hashlib，无需额外 Perl 权限。确实需要 /usr/bin/shasum 时，本机已测试的独立选择是允许精确 /System/Library/Perl/5.34 可信运行时目录；只放 libperl.dylib 后仍会因 strict.pm 缺权失败。这个选择额外允许读取该版本 Perl 运行时，不是只允许一个库文件，也无需整个 /System/Library。

Homebrew、Python 或系统 runtime 更新后，重新核对解析目标与依赖，再做相同正负自验。这些结果只覆盖固定 Mac 上所测的合成 helper，不证明任意第三方包、MCP、模型历史或 Windows／Linux 同样通过。Code Mode 的实验 feature／模型 metadata 提醒应另行源核；不能为了让解释器测试通过而关闭 Code Mode、强制 tool_mode 或隐藏原生警告。

## 历史、原生预加载与 MCP 各有边界

原生源码在非 full-disk-read profile 下为 AGENTS.md 加载传递 sandbox context；保留这个原生机制，不另写 loader。仍应使用合适的新 cwd／profile，核对实际生效的 profile 和会进入新任务的获准指令，避免把旧上下文重新带回。cat／rg 自验不证明模型从未见过其他历史。

MCP／插件可能由独立进程或远程服务持有文件与账户权限。命令子进程的 OS 拒绝，不会自动收紧外部服务器的授权。只有独立确认该渠道也符合本轮读取范围时才暴露给 worker；否则保留未验证边界，不能改走该渠道绕过拒绝。具体身份、网络和生命周期继续用对应参考。

## 固定版本依据

- fe136 的 [permission profile 配置测试](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/core/src/config/config_tests.rs#L1443)：default_permissions 与 filesystem／network table。
- [Seatbelt 读取策略生成](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/sandboxing/src/seatbelt.rs#L980)：full read 与显式 readable roots／unreadable exclusions 的区别。
- [AGENTS.md 加载](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/core/src/agents_md.rs#L74)：按选定 profile 传入 sandbox context。
- CLI flag 仍以对应安装的 sandbox --help、exec --help 和 config/read 为准；参见 [来源与兼容性](sources-and-compatibility.md)。

这些是原生能力的使用说明，不是 adapter 新增的 agent loop、通用隐私保证或跨平台全工具验收。
