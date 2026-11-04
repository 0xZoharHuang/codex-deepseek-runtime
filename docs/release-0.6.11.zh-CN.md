# Windows 0.6.11 独立发布组合

本文件随 Windows 0.6.11 配套源码提供。原生包、正常安装与最终真实任务是分开的验收层；当前状态和公开下载回执以[本版索引](../reports/releases/0.6.11/README.md)为准。本文不把 Linux 的通过借给 Windows，也不代验 CADSG / Codex App。

这一组合固定 Windows x64 Codex `0.164.0-alpha.7` / `fe136d4e70ef2b5bbed40a2d22b3aabf267796d3`、Rust 1.99.1 和 P36 的正常 opt3 构建。复用完整原生归档，不重打包、更换二进制或将旧包改名冒充新编译。Python 业务模块与正常 P36 安装相同，版本字面量改为 0.6.11；本版不移除现有入口、会话或 home。

```text
Windows 原生 CDS CLI
  +-- 原生工具、沙箱、审批、终端、会话与子智能体
  |
  +-- 同一 Python 协议适配 --> OpenRouter --> 固定 DeepSeek
```

## 安装与已有任务

使用本 tag 的源码与 Windows manifest、归档，正常安装不编译 Rust：

```powershell
python install.py --root "$env:LOCALAPPDATA\codex-deepseek-0.6.11-check" --no-activate --native-archive "C:\verified\codex-deepseek-native-0.6.11-x86_64-pc-windows-msvc.tar.gz"
```

安装器核对平台、recipe、工具链、SHA 和完整清单。先检查安装输出的新不可变入口，再决定是否切换用户命令；不要覆盖活动 home、sandbox 目录或来源不明的启动器。`--no-activate` 不切换现有命令，但会正常初始化新 root 的 home。

已有任务保留同 home/UUID/工程。正常结束该 home 的客户端、native、provider 与 lease 后，用新入口执行正常 `runtime status` 迁移已终态 descriptor，再回读 runtime 身份；不手删状态、忽略未知 owner 或重新执行已有副作用。status 不发送模型推理，但会读取凭据并启动本地 provider。模型调用需要另行使用正常凭据机制，不把 key 写进命令、日志或源码。

## 中断与后台终端

`turn/interrupt` 中断当前 turn，不承诺终止所有可恢复后台终端。后台 list、按所属 thread 与原生 processId 的 terminate，以及 clean 是独立原生接口；Code Mode 外层返回 aborted 也不等于内部进程全部退出。

R478 原始失败保留：其夹具要求 interrupt 后 worker 自动退出，但该要求与固定上游保留 resumable unified_exec 的契约冲突。R481 用独立新 UUID、无副作用命令实际观察 initial wait 被中断后会话仍保留，原生 list 返回准确 processId，随后原生 terminate 返回 true，持有的 OS worker 在 0.028 秒内退出，原 once ledger 未重放。这不改写 R478 的 RED，也不等于全部生命周期审计已通过：外层控制器保留其历史 uncertain 和持久 provider 清理事实。

## 平台与证据边界

Linux 继续使用已独立发布的 v0.6.10；macOS 继续使用 v0.6.9。0.6.11 源码不带旧平台 manifest，不会把旧包标作新平台资格。OpenAI 模型能力和云端账户权益仍单列。实际长会话的原图、汇总、冷恢复、压缩、协作和退出证据已核对。Jinja 业务补丁的最后一次基准仍失败，单独保留；模型生成代码的正确性不计作本地 runtime 保证。公开交付必须核对发行页的下载与正常安装回执。
