# 只读取本轮获准资料

用户若明确限制“只读这些文件／目录，不读旧证据、设备原始材料或其他会话”，应把读取范围表达为所选 native 支持的 named permissions profile。workspace-write 限制写入，并不等于读取白名单；提示词或 rg 排除模式也不能替代 OS 拒绝。

[读取范围参考](../skills/cds/references/read-scope.md) 提供固定版本的 TOML、exec 启动方式、虚构正负 fixture 的 cat／随包 rg 自验，以及历史、MCP 和平台边界。仅在这种明确约束下使用；普通 coding 不需要改成这套权限。

操作上保留三个区别：

1. **指令与权限。** 命令显式指向获准路径，同时用原生规则限制读取。别先宽搜再过滤；单个文件可读不代表同目录 sibling 可读。
2. **旧上下文与新读取。** 权限收紧不能撤回已经进入模型的内容。新 worker 使用独立 profile 和新任务，不恢复／fork 含禁读资料的旧历史。
3. **本机工具与其他渠道。** cat／rg 的 OS 自验不代表 MCP、远程服务或已有上下文一并受限；按实际渠道核对权限，不能借别的工具绕过用户约束。明确要求本地／工具离线时，还要处理 bootstrap 生成的 codex_web：空 MCP table 是 merge，并非清空；按参考中的 server.enabled=false 设置并读回实际状态，不能普遍禁用普通任务的 MCP。

固定 macOS native 的虚构 fixture 已验证正负读取、literal 文件 sibling 与 symlink 边界；这不代替 Linux／Windows 的实际支持与验收。特别是 Windows 10 elevated 后端对 minimal-read 的原生拒绝，不应被“修”为 :root read 来扩大用户允许范围。

需要 Python 等本地工具时，还要显式允许可信运行时和它实际需要的外部动态依赖；解释器目录可读不等于所有依赖都可读。参考中的 macOS 实测同时核对了真实后端、stderr 与越界拒绝，说明 Homebrew alias 可见范围及可选 Perl 版本目录权限；不能只看 exit=0 或放开整个 Homebrew 来处理。

本页不安装技能、不修改全局配置，也不引入新的 sandbox 或 runtime。CDS 使用入口见[操作技能](cds-skill.zh-CN.md)；一般权限和网络配置见[启动与权限参考](../skills/cds/references/launch-and-permissions.md)。
