# 在 Windows 沙箱里运行 asyncio 测试

Windows 版 CPython 3.11.9 创建异步事件循环时，会用本机 TCP 回环连接建立内部的 `socketpair()`。在禁止回环的 elevated 沙箱中，一个没有业务网络请求的测试也可能卡住。正式 P39 上的两个 pytest 进程都进入了 Jinja 的异步用例；实际栈依次经过 `asyncio.run`、`_make_self_pipe`、`socketpair` 和 `accept`。在独立沙箱中运行带时间边界的最短探针，得到了相同的阻塞栈。[CPython 3.11.9 的 socket 实现](https://github.com/python/cpython/blob/v3.11.9/Lib/socket.py)

如果项目需要本地连接，可为它选择一个明确允许主机回环的受管网络配置。下面是 P39 实测的配置形式；将端口换成该专用 CDS home 可用的本机端口，并通过正常的原生维护入口应用设置。项目应使用已受信的配置，按原生规则选择具名权限配置。

```toml
default_permissions = "cds_local_only"
approval_policy = "on-request"

[windows]
sandbox = "elevated"

[permissions.cds_local_only]
extends = ":workspace"

[permissions.cds_local_only.network]
enabled = true

[features.network_proxy]
enabled = true
proxy_url = "http://127.0.0.1:43117"
enable_socks5 = false
allow_upstream_proxy = false
allow_local_binding = true
mode = "limited"

[features.network_proxy.domains]
localhost = "allow"
"example.com" = "deny"
```

`allow_local_binding` 允许访问主机回环上的服务，其范围大于单个 Python 进程的自连接。这个配置显式启用受管代理，通过许可列表约束代理请求。Windows 的离线沙箱账户和防火墙设置按 CDS home 的命名空间维护。切换设置前，应结束该 home 内还在使用上一套权限的任务。

在实测的受管配置中，沙箱使用原离线身份，`socketpair()` 成功收发数据；工作区可写，私有 oracle 读取被拒绝。主机可以连接的外部 IP 在沙箱中直连返回 WinError 10013。通过代理发送真实 HTTP GET 时，明确拒绝的域名返回 403 `blocked-by-denylist`，未列入许可列表的域名返回 403 `blocked-by-allowlist`。选择自己的配置时，也应通过真实进程的网络身份、回环收发、外部直连、代理域名和文件权限检查确认生效。

恢复原配置后，通过同一原生维护入口重建该命名空间的设置，并核对回环拒绝和预期的文件权限。CLI 的这组结果对应本版固定的 Windows runtime；桌面 App 使用自己的运行时和独立验收。

[受控配置的网络与文件权限回执](../reports/releases/0.6.14/windows-loopback-qualification.json)
