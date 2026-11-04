# Windows 私有父目录、Node 与 PowerShell

CDS 的 Windows P37 候选修复祖先目录的属性句柄权限。它沿用原生权限准备和工具执行，只补齐当前 namespace 已负责的 home / 私有 worktree 父目录权限。本候选的安装和发布资格以对应发行回执为准，不能把源码修复当作已交付。

## 根因与最小修复

`GetFileAttributesW` 成功不代表 `CreateFileW(FILE_READ_ATTRIBUTES)` 能打开目录句柄。Windows 实测中，后者获得的权限掩码包含 `SYNCHRONIZE`；只有 `READ_ATTRIBUTES`（0x80）的旧 ACE 会让 Node 24.22.1 的普通 `.js` 文件在逐级 `lstat` 中报 `EPERM`。公开父目录可能使 libuv 的后备路径碰巧成功，因而只测公共临时目录会漏掉问题。

原生 helper 需要的精确掩码是 `FILE_READ_ATTRIBUTES | SYNCHRONIZE`（0x100080），不继承给子项。它不授予目录枚举、文件数据读取、写入或更改所有者。已有旧属性 ACE 会按同一原生准备路径补齐，不要求用户手改 ACL、切换 full access、改用 stdin 或复制一套权限脚本。

权限判定同时要求两个位，并分别核对联网和离线用户；不能把一个用户的属性位与另一个用户的同步位拼成通过。显式拒绝继续阻止授权；原路径身份、reparse 防护、目录锁、所有者检查及不继承规则保留。对当前 namespace 不负责的第三方私有目录，不静默扩大授权范围。

对应依据：[Windows 文件访问权限](https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile)、[Node 24.22.1 Windows 文件实现](https://github.com/nodejs/node/blob/v24.22.1/deps/uv/src/win/fs.c)。真实对照使用固定同一 Node 二进制、同一 native runtime、同一受限用户及同一普通脚本入口；脚本成功还须与目录枚举、写入和真实私有旁文件仍被拒绝一起判断。

早期 PowerShell 实测只证明 0x80 能恢复当时的 cwd / Set-Location，不能外推为 Node 句柄已正常，也不能把早期记录改成新版本通过。新的回执保留原错误、升级前后权限和实际执行结果。

## 标准 Git 布局

工作区中的普通子目录仓库使用标准 `.git`，已由下游通过正常 Git 工具和同一线程冷恢复核回；这种布局不需要另建 named permission profile。

工作区顶层 `.git` 的原生保护是另一种条件。确实需要写入该保护路径时，可用固定 0.164 支持的 named permission profile 明确表达授权，再回读 `activePermissionProfile` 和实际操作结果。实验性 App-server `thread/start`、`thread/resume` 的 `permissions` 不能与 `sandbox` 同传。不要以自定义 git-dir、关闭沙箱或全盘 `safe.directory` 掩盖布局、权限或仓库所有者问题。参见[官方 Permissions](https://learn.chatgpt.com/docs/permissions)。
