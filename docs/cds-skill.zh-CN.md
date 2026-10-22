# CDS 操作技能

[`skills/cds`](../skills/cds/SKILL.md) 是按官方 `skill-creator` 规范编写的操作技能，主要帮助调用 CDS 的 agent 选择入口、控制任务和核验结果。用户直接运行 CDS 时，无需安装此技能才能使用命令、文件、MCP 或子智能体等原生能力。

```text
用户直接使用                         外层 Codex / 其他 agent
      |                                      |
      v                                $cds 操作技能
 CDS CLI / TUI                               |
      |                             exec 或 app-server
      |                                      |
      +---------------+----------------------+
                      v
              原生 Codex runtime
              + DeepSeek 协议适配
                      |
          原生工具 / 权限 / 会话 / 协作
```

在 CDS 内调用 `$cds` 时，它使用当前会话，不再递归启动另一层 CDS。外层 agent 启动的独立 CDS worker 有自己的进程和会话；它不会自动成为父 Codex 的 `spawn_agent` 子智能体。CDS 内的原生子智能体仍使用原生协作机制。技能说明这两种生命周期，并不实现新的控制器、调度器或 agent loop。

## 一个技能，按需读六份参考

主入口保持简短，通过任务选择启动与权限、会话控制、原生工具、安装与平台、排障验收、版本与来源。它参考了固定 native 源码内置的 `skill-creator` 格式，以及官方 [Build skills](https://learn.chatgpt.com/docs/build-skills) 和 [App Server](https://learn.chatgpt.com/docs/app-server#api-overview) 文档。

技能包含一个可选的只读 `inspect_cds.py`：核对本地安装元数据与入口哈希，拒绝不明的同名程序。显式要求时才运行已关联入口的 `--help` 或 `--version`。它不读取凭据、不调用模型，也不把元数据一致性称为发布签名或连接测试。

## 安装到使用方的技能目录

这次技能随仓库分支发布，**已发布的 v0.5.0 tag 和归档保持不变**。从含有 `skills/cds` 的本仓库工作区执行下列命令；命令只复制技能，不安装或升级 CDS runtime。

需要同时供普通 Codex 和 CDS 使用时，官方支持的用户目录 `~/.agents/skills/cds` 最方便。Windows 对应用户主目录下的 `.agents\skills\cds`。两者共享的是指令文件，配置、凭据、会话和运行进程仍分别保存。若只供某个独立 profile 使用，则复制到其 `CODEX_HOME/skills/cds`；不要为了装技能把 CDS 的 home 指到 Codex App。

macOS / Linux，在仓库根目录运行：

```sh
python3 -c 'from pathlib import Path; import shutil; target=Path.home()/".agents/skills/cds"; target.parent.mkdir(parents=True,exist_ok=True); shutil.copytree("skills/cds",target,ignore=shutil.ignore_patterns("__pycache__","*.pyc")); print(target)'
```

Windows PowerShell，在仓库根目录运行：

```powershell
@'
from pathlib import Path
import shutil
target = Path.home()/'.agents/skills/cds'
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree('skills/cds', target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
print(target)
'@ | python -
```

目标已存在时命令会停止，避免覆盖用户改过的技能。更新时先核对原目录来源及差异，再替换对应版本；不要把同名副本重复放到多个发现目录。其他支持 SKILL.md 的 agent 使用其自身规定的发现路径，不能保证所有产品识别同一目录。

安装后检查当前 runtime 的技能列表。必要时开始新会话；已运行的任务不一定立刻刷新。app-server 客户端可调用 `skills/list`，指定 cwd 和 `forceReload:true`，检查 `cds` 的路径、启用状态及解析错误。只有列表可见还不等于技能已在模型轮次中使用。

## 调用示例

外层 Codex：

> 使用 $cds，让 Windows 上已安装的 CDS 在指定工程内修复这个问题。保留它的会话，检查实际改动和测试结果。

直接在 CDS 内：

> 使用 $cds，检查当前工具为什么无法写入缓存；沿用当前会话和已授权的权限范围。

普通功能开发直接提出需求即可，无需每轮调用操作技能。运行中需要可靠追加要求时，外层控制器持有原 app-server 的连接和 turn ID；`queue` 是后续轮次的持久队列，不能当作即时 steer 或 stop。具体用法见[委派与本地工具指南](delegation-and-local-tools.zh-CN.md)。

## 验证和维护

[`verify_cds_skill.py`](../verify_cds_skill.py) 在新目录中检查原生技能发现。`--model` 另执行两段真实模型工作：CLI 使用技能完成 CSV 程序，再由 app-server 恢复同一个 thread，通过原生 skill input 追加功能；独立检查实际输出和冲突处理。它使用 API 额度，沿用已经授权的凭据机制，不在命令行放入 key。

```text
python verify_cds_skill.py --entry <实际CDS可执行文件> --output <尚不存在的测试目录>
python verify_cds_skill.py --entry <实际CDS可执行文件> --output <另一个新目录> --model
```

测试使用独立 CDS home，保留原始事件和失败，并停止自己的 native/provider 进程；完成后由调用方按需要保留结果、清理测试目录。它不是整个 runtime 的跨平台资格验收，也不能用一次业务成功证明未来所有模型任务正确。技能结构检查、前向决策测试、无模型原生发现、真实模型任务分别记录。

本次 Windows SSH 实测保留了一项环境限制：PowerShell 在原生 unelevated workspace 沙箱下出现 `0xC0000142`；同机同二进制绕过 CDS 直接调用原生 runtime，结果相同。CMD 在相同权限下正常。真实模型任务最终完成，不表示所有中间命令都成功；技能提供按实际 shell 处理引号与选择兼容工具的指引，不把放宽权限当成默认修复。

[2026-10-22 验证记录](../reports/skills/cds-20261022.json)分别列出 13 个前向场景、18 项 helper 单测、三平台原生发现，以及 Windows 的实际模型任务、失败和对照结果。最终共享技能的 9 个文件在三台环境中逐项哈希一致。
