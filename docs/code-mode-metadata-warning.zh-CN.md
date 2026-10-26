# Code Mode 元数据提醒与实际执行

如果看到模型元数据未宣告 Code Mode 支持的原生提醒，不能据此认定工具被禁用，也不能用一轮纯文本回答证明 Code Mode 已执行。

固定的 Codex 0.164.0-alpha.7 在启用了 CodeMode 或 CodeModeOnly、catalog 的 tool_mode 缺省且模型使用了实际 catalog 条目时发出这个 WarningEvent。检查只发送提醒，不关闭工具。CDS 的 DeepSeek catalog 当前省略 tool_mode，因此提醒可能出现；本版没有隐藏或消除它。

实际模式选择有自己的顺序：catalog 中明确设置的 tool_mode 优先，其后才按 profile 的 CodeModeOnly、CodeMode、Direct 选择。不要为了消除提醒，把共享 catalog 改成 tool_mode=code_mode_only；这会覆盖希望使用 Direct 的 profile。把它改成 Direct 同样可能不再提醒，但并不能证明 Code Mode 能力得到宣告。

Code Mode host 不可用是另一条独立错误路径。CodeModeOnly 不会因为 host 不可用就自动退成 Direct。ToolSearch 还需要当前策略允许、存在带搜索信息的 deferred tools；supports_search_tool=true 不是每一轮都必然出现 ToolSearch 的保证。

判断一条具体任务是否真正走通，应保留完整证据链：请求确实暴露 exec/wait 及对应工具定义，提供方返回真实 custom_tool_call，原生 host 执行，嵌套工具返回实际结果，最后 turn 完成。例如让控制者先创建一个未出现在 prompt 中的随机 nonce 文件，再由模型调用嵌套只读 shell 读取；最终结果必须与文件匹配。退出码为零、纯文本中声称调用成功、只看 feature 配置，都不能替代这条链。ToolSearch 需要另看实际发现调用及之后被发现工具的使用。

源码依据是固定上游的 [提醒条件](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/core/src/session/code_mode_warning.rs) 和 [工具模式选择](https://github.com/openai/codex/blob/fe136d4e70ef2b5bbed40a2d22b3aabf267796d3/codex-rs/core/src/tools/mod.rs)。各版本的实际运行资格以 release report 为准；本说明解释机制，不替代运行证据。
