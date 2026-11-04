# Windows 私有工程父目录与 PowerShell 工作目录

当集成方把工程放在自建的私有目录树，例如 `work/<worker>/<task>`，终端的目录参数正确并不代表 PowerShell 能初始化该目录。Win32/Python 可以进入目标目录，而 PowerShell 仍可能退回盘符根目录，或在 `Set-Location` 报拒绝访问：它还会查询祖先目录的属性。

先分别核对原生 session/turn/tool 的 cwd、实际沙箱用户、Python cwd、PowerShell 初始 cwd 和相对文件读写。不要把这种差异归因于模型漏传 workdir，也不要切换 full access、改变整个工程树继承、读取凭据或替换 shell 来掩盖问题。

已在真实消费方同一 CDS 入口、同一 home、同一受限用户下验证：目标工程已有正常原生访问能力，但一个私有父目录缺少沙箱组的 `FILE_READ_ATTRIBUTES`。仅为该父目录增加不继承的 `READ_ATTRIBUTES`（0x80）后，PowerShell 初始位置、显式 Set-Location 和相对标记读取均恢复；8 个实际存在的私有测试文件仍拒绝访问，其他目录描述符、原有 owner 和 DACL 控制位保持不变。该结论只覆盖该目录布局和实际账号，不延伸到任意账户或所有嵌套目录。

这是集成方自建目录树的初始化条件。固定上游的工作区授权不承诺替第三方修改所有私有祖先目录；CDS 不会因此自动扩大这些目录的权限。若集成方确实拥有并授权维护这些父目录，应在既有的工程初始化流程内，依据当前 home 的已验证原生 namespace 身份提供最小的、不继承的属性访问，并对每一级私有父目录进行实际验证。不要硬编码本报告里的账号、SID 或 group 名，不要把诊断脚本复制成另一套沙箱或权限系统。

修改前后保存实际目录对象身份与安全描述符；拒绝 reparse 重定向、所有者不匹配以及并发变化。已存在的显式拒绝不能被覆盖。验收必须同时包含正常 cwd/相对读写和真实存在的私有邻接文件仍被拒绝；如果修复无效，按原描述符精确回滚。多用户、嵌套 worker/context、在线账号和业务构建仍由采用该布局的集成方分别验收。

普通个人工程没有这一私有目录布局时，不需要额外创建账号或维护 ACL。

## Node 与 Git 的两项区别

PowerShell 的 `GetFileAttributes` 路径成功，不足以证明 Node 的逐级 `lstat` 成功。Node 24.22.1 的 Windows 路径解析可能需要实际 `CreateFileW(FILE_READ_ATTRIBUTES)` 句柄；访问失败后的 libuv 后备路径还会尝试父目录列表。应检查确切 API、访问掩码、组状态和显式拒绝，而不是直接扩大祖先树的 read 权限，或把改用 stdin 算作正常脚本文件执行通过。源码见 [Node fs.js](https://github.com/nodejs/node/blob/v24.22.1/lib/fs.js#L2616) 与 [libuv Windows fs](https://github.com/nodejs/node/blob/v24.22.1/deps/uv/src/win/fs.c#L2042)。

标准 `.git` 在默认 workspace-write 中受保护属于原生行为。固定 0.164 源码支持 named permission profiles：继承 `:workspace` 后，为 `permissions.<name>.filesystem.":workspace_roots"` 下的 `.git` 明确设置 `write`，可表达授权的 Git 写入，同时保持受限执行身份。其他私有 deny 和临时目录范围仍需保留。旧 `sandbox_mode`/`sandbox_workspace_write` 与新 profile 不叠加；App-server 的实验性 `thread/start`、`thread/resume` 使用 `permissions` 选择名称，不能同时传 `sandbox`。应回读 `activePermissionProfile` 并实际验证普通 `.git` 操作和私有 canary；不能只看配置文本。参见[官方 Permissions](https://learn.chatgpt.com/docs/permissions)。
