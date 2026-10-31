# Front HTTP trace（候选功能）

默认行为保持不变。显式设置 `CODEX_DEEPSEEK_TRACE_DIR` 后，现有网关的 aiohttp AppRunner 会在每个 HTTP 请求收尾时写一个 `front-http-<随机 UUID>.json`，包括路由直接返回的 404/405、认证拒绝和 Responses POST/SSE；没有添加代理或更换运行时。服务已有的 trace 配置更新方式也适用。长连接建立后开启或关闭 trace 同样有效，不需要调整全局或 `aiohttp.access` 日志级别。

每条记录只包含 `version`、Unix 秒时间 `completed_at`、`duration_seconds`、HTTP `method`、不含 query 的 `path`、布尔 `upgrade_websocket`、HTTP `status`。不会记录其他请求头、认证、请求体或响应体；`upgrade_websocket` 只表示收到的 Upgrade 字段是否包含 websocket，并不表示服务接受了升级。记录使用既有私有目录和原子写入方法。路径本身仍可能包含私人项目名称。

**这并不使整个 trace 目录成为可公开材料。** 同一模式已有的 `*-request.json` 等文件仍包含敏感请求上下文。完成受控排查后关闭 trace，按项目既有证据保留和清理策略处理，不能把原始目录直接公开。

验证前端请求覆盖时，必须先在同一运行实例和观测窗口发出已知 `GET /v1/responses` + `Upgrade: websocket` 正控，确认收到 405，并在 front 记录中看到 GET/405/true；再匹配实际完成的 POST/SSE、主请求及辅助请求。需要保留窗口起止、原始文件清单和请求/回执对应情况。只有文件为空，不能据此声称没有 WebSocket 请求。收尾前尚未完成的请求、未解析成 HTTP 的 TCP 连接以及套接字帧不属于此完成请求日志的计数范围。

Front 记录写失败时，网关发出不含异常正文、路径或请求数据的 `Front HTTP trace incomplete (<异常类型>); request outcome unchanged` 警告，不更改原本 HTTP/SSE 响应。出现该警告、缺失正控或有未核对的请求时，观测窗口必须标为不完整，不能把漏记当作 0。原有 Responses 请求 trace 的错误处理没有改变。

实现一直让 aiohttp 保留计时，以支持运行中启用 trace 的既有 keepalive 连接；trace 关闭时不写 front 文件，仍仅在原 `aiohttp.access` INFO 级别本来启用时使用其既有输出。前端统计的适用范围独立于原生模型能力、上游 HTTP 传输和运行时 WebSocket 能力设置。
