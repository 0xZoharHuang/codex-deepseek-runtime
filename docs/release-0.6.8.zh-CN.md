# CDS 0.6.8 安装、升级与回滚草案

**候选尚未发布。** 正式v0.6.8 tag/资产独立回读成功后才按该tag正常安装；当前公开稳定版仍为v0.6.7。发布前仅用owner已核本地manifest/archive，未来URL失败不换任意旧包。

## 先隔离验证

使用独立Python3.11+、Git与本平台固定资产；不用Codex App内置Python、配置或订阅。Windows原生与WSL/Linux是不同安装。正常安装无需Rust，不覆盖`codex`。

```text
python install.py --root NEW_ROOT --no-activate \
  --native-manifest MATCHING_LOCAL_MANIFEST \
  --native-archive MATCHING_ORIGINAL_TAR \
  --wheelhouse VERIFIED_LOCKED_WHEELHOUSE
```

wheelhouse可省略以按hash lock取得Python依赖；native仍核平台、recipe与完整库存。新root必须此前不存在。安装后显式设置`CODEX_DEEPSEEK_HOME=NEW_ROOT/codex-home`，使用该release的不可变绝对入口：Unix `NEW_ROOT/releases/<source-id>/venv/bin/codex-deepseek`；Windows `NEW_ROOT\releases\<source-id>\venv\Scripts\codex-deepseek.exe`。

先`--version`与`runtime status`；`runtime doctor`会真实调用提供方并计费。key只通过`runtime setup-key`隐藏输入、系统凭据或安全父进程环境提供，不进命令参数/源码/日志。

## 正常切换和延续会话

记录旧root/home、绝对入口、安装回执与归档。旧任务继续用旧不可变入口，不能结束别人的工作。新root验收后，以同一冻结源码/原参数再次正常安装、省略`--no-activate`，由标准入口切换，仍不构建Rust。

要继续旧会话，保留原home；确认该home任务、子进程和daemon全部结束后，以新不可变入口和显式原`CODEX_DEEPSEEK_HOME`执行原生resume。仅停daemon不证明后台工作全退。发生版本/网络/凭据身份冲突先诊断，不全局kill。

不得把旧`.sandbox`、`.sandbox-secrets`、`.sandbox-bin`复制进新home；权限与sandbox资源按原生home身份管理，不用full access/禁Guardian/关闭审批通过。Direct、Code Mode、MCP、Skills、Hooks与子智能体照实际版本目录使用；V2不虚构V1的close_agent。

## 回滚和清理

保留旧tag/root/home/不可变入口。先协调新home的任务与后台进程，再使用旧入口或旧tag的标准安装器选择旧root。不要将新SQLite/容量检查点覆盖旧home，不改checkpoint版本；兼容性按[容量恢复说明](capacity-replay-source.md)。

`native/rollback/0.6.7`只保存旧身份，不授权0.6.8源码安装旧recipe；旧包配v0.6.7源码及其许可证补充。失败、新会话和证据保留，不能靠删记录造全绿。

新版本稳定、旧任务终止、回滚材料与证据归档校验后再清理本项目下载/编译缓存/候选root/多余工作树，保护用户工程和会话，不动Defender、账户、全机代理或其他线程进程。

资格、原始失败、平台skip、web/云能力边界见[本版索引](../reports/releases/0.6.8/README.md)。CAG App独立验收。
