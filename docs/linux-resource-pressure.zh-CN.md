# Linux／WSL 资源压力下的状态检查超时

本次捕获的 40 秒故障已在记录范围内完成处置与验收：真实堆栈停在标准库导入阶段，guest 资源证据与同负载复测支持容量不足这一解释。仅将专用 Windows 用户的 WSL2 内存上限从 6 GB 调到 10 GB，CDS/native、冻结输入、模型、并发和原 40 秒判定标准保持。原始失败与观测缺口保留；这不是任意负载永不超时或报告内容质量通过的保证。

完整数值与口径见[验收记录](../reports/diagnostics/linux-import-timeout-20261102.json)，原始 919 B [阻塞堆栈](../reports/diagnostics/linux-import-timeout-20261102.stack.txt)按原字节公开。

## 先分清超时发生在哪一层

本次检查由独立 Python `-I -B` 子进程执行，依次观察 provider descriptor、进程身份、原生运行状态及 service lease。父进程的原有截止时间是 **40 秒**。生产场景为同一个原生 app-server 内的三个并行团队；真实文件、网页、图片和本地压缩均有活动。

原负载的新一次运行持续约 **34 分钟**后再次触发该截止时间。检查器在 15 秒时留下的堆栈停在 Python 3.12 标准库 `pathlib.py:17`，正在导入内建 `errno`；日志只有 `before_imports`，还没有进入任何 CDS 状态函数。这能把本次捕获的阻塞定位到**导入阶段**，不能据此认定 `errno`、SQLite 或某个锁自身有缺陷。

停止负载后，在同一次 guest 启动中取得以下快照：

| 观察项 | 读数 | 能支持的判断 |
| --- | ---: | --- |
| Linux `MemTotal` | 6,070,864 kB | guest 实际可用总内存约为既有 6 GB 配置；Windows 空闲 RAM 不会自动越过该限制 |
| memory PSI `full avg300` | 21.22% | 前一段时间存在明显内存停顿 |
| IO PSI `full avg300` | 32.75% | 前一段时间存在明显 IO 停顿 |
| CPU PSI `some avg300` | 0.17% | 此快照未显示同等程度的 CPU 调度压力 |
| 相关 OOM 记录 | 未发现 | 不能据此排除回收、换页或其他资源压力 |

这些是**负载停止后读取的 `avg300` 指数平滑趋势，不是精确五分钟区间平均，也不是故障瞬间峰值**。三个团队正在 PPTX／LibreOffice 转换路径中，是需要关联采样的候选高峰；尚未证明三次转换精确重叠，也没有证明 LibreOffice 单独造成了导入停顿。这些旧读数与后续保持原负载的单变量复测一起支持 guest 容量不足的处置路线；后续确实记录到至少两个转换核心进程重叠，但没有把 LibreOffice 本身认定为软件缺陷。

Linux PSI 的 `some` 表示至少部分任务因资源停顿，`full` 表示所有非空闲任务同时停顿；`avg10/60/300` 是不同时间窗的趋势，`total` 是累计停顿微秒数。它描述停顿时间，不是内存使用率。参见 [Linux 内核 PSI 文档](https://docs.kernel.org/accounting/psi.html)。

## 不要把状态函数的局部 timeout 当成总时限

冻结 CDS 0.6.9 中，`native_active()` 使用 SQLite `timeout=5`，执行三个 SELECT、取完结果并关闭连接，随后检查记录的进程身份。五秒是数据库忙锁等待配置，**不是 Python 启动、导入、查询计算、取回结果和关闭连接的总截止时间**。本次堆栈尚未进入这条路径，不能靠改 SQLite timeout 或加索引解释、修复本次故障。

另有两个操作边界容易混淆：

- `lease_held()` 先以追加模式打开 lease 文件，再尝试非阻塞文件锁；它可以创建文件，不能称为严格纯读。非阻塞参数只作用于锁请求。
- `runtime status` 会进入 Store 和 provider 生命周期流程；`runtime provider status` 也会取得 service 配置锁，再调用原生状态检查。不要将它们替换进活跃期探针，宣称得到无副作用且有硬截止时间的健康接口。HTTP health 只验证本地 provider 响应，不能证明原生任务已经完成。

## 用低影响证据区分导入、函数和管道

保留原检查器及其 SHA256。在外层增加少量观察即可，不修改冻结 CDS、判断逻辑、轮询频率或 40 秒截止时间：

1. 以单调时钟记录导入前后、各函数进入／返回、最终 JSON 写完的位置；及时 flush。不要记录 descriptor 内容、token 或环境变量全集。
2. 使用 Python 的定时堆栈机制，将输出写入检查器自己的私有普通文件，和业务 stdout／stderr 管道分开。记录采样时间及检查器 PID／出生身份，保留原始日志。
3. 父进程同时记录子进程是否已经退出、实际超时发生在等待进程还是等待管道 EOF。不能只凭 `communicate()` 超时就认定函数还在执行。
4. 在同一 guest 启动中低频采集 `/proc/pressure/{memory,io,cpu}`、`/proc/meminfo`、相关 cgroup 计数，以及自有检查器的 CPU、IO、调度和进程状态。对转换进程记录实际起止和 RSS，避免只从任务名称推断高峰。
5. 超时保留为 `unknown`，不得改成 `inactive`，也不得仅凭它强制替换服务或清理未知属主。已确认身份的原 owner 仍按既定失败流程正常取消、收尾，并回读确认；不能把未知状态当作安全清理许可。采样器不请求模型，不持续保存完整命令行、文档正文或每帧大转储。

短时间空载成功只能证明空载控制可运行。重启后的成功、换一轮简单任务或未覆盖原失败窗口的成功，都不能独立证明修复。

私有证据可采用相对路径和精简索引：

```json
{
  "probe": {
    "source_sha256": "<冻结检查器指纹>",
    "deadline_seconds": 40,
    "phases": [{"name": "before_imports", "monotonic_seconds": 0.0}]
  },
  "stack": {"relative_path": "evidence/import-block.log", "sha256": "<原始日志指纹>"},
  "resources": {
    "same_guest_boot": true,
    "sample_relation": "post_stop_not_peak",
    "memory_full_avg300": 21.22,
    "io_full_avg300": 32.75
  },
  "result": "unknown_pending_single_variable_retest"
}
```

公开索引不包含实际 boot UUID、机器 SID、业务 thread UUID、凭据或完整私有路径。本次原始导入日志 SHA256 为 `a0ad9239dab96eab62df3f7b3e6f032226985585a291ef3b1331f270c1ab7754`；资源读取回执 SHA256 为 `1f29ded76d964c5b6e834bfd3f8d3ed3e47b9a705db7cdc74e484594533b7889`。原件私有保留，指纹不代替原件或结论。

## WSL 配置必须先确认作用域

Microsoft 的 `.wslconfig` 位于实际 Windows 用户的 `%UserProfile%`，其中内存、处理器和 swap 设置作用于该配置所管理的 WSL 2 虚拟机及其发行版；它**不是某一个 distro 的独立内存配额**。`/etc/wsl.conf` 的逐发行版设置不能替代这个作用域。配置生效需要 WSL 停止再启动；`wsl --shutdown` 会停止所有运行中的发行版，不能作为无害的配置刷新使用。参见 [Microsoft WSL 高级配置](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)。

本次处理只由原负载 owner 执行：将其 Windows 账户的既有 guest 内存配置 **6 GB 调至 10 GB**，CPU 保持 4、swap 保持 1 GB。配置已通过 guest 实际 `MemTotal=10,187,344 KiB`、CPU=4、swap=1 GiB 回读，原配置字节和权限保留；后续同负载复测结果见关闭记录。这个上限是本次配置下的处理值，不是 8 GB／10 GB 的通用最低配置。

变更前应确认实际 Windows 账户、已运行发行版、自有任务及其他消费者；只有确定不会中断其他工作后才安排停止。保存原配置的字节、权限、SHA256 和“原文件是否存在”，只改内存项，保留无关设置。不改变其他账户的 WSL 配置、系统 pagefile、防护、代理或 CDS 发行。

重新启动后读取 Linux `MemTotal`，核对 CPU 数、swap、实际 CDS/native/模型/provider 和配置身份。只看到配置文本写成 10 GB 不算已经生效。回滚时也先确认没有其他消费者；将本项目修改恢复到核验过的原值，若文件原本不存在则按登记范围恢复原状，保留后来新增的无关设置，再正常停止／启动并回读实际资源。不要用整份旧文件覆盖后来工作。

## 复测与关闭条件

资源变更后的复测仍保持三个团队、同一个 app-server、每根至多两个原生助手、同一代码／输入／模型路由／权限和原 40 秒检查标准。需要覆盖至少原约 34 分钟的失败窗口，并实际经历对应 PPTX／LibreOffice 转换高峰、网页、图片及本地压缩；继续保存 PSI、转换起止、检查耗时及原生线程关系。

不能降低并发、换成小图片、延长 timeout、用自动重试掩盖失败，或将最后成功覆盖此前失败。若没有出现原高峰，结果只记为覆盖不足；若仍超时，保留新的阶段和堆栈，继续针对实际层定位。最终结果至少应列明原始失败、唯一变更、实际资源、负载覆盖、实际记录的检查时长及原截止时间是否触发、错误／未知数、任务与会话保全，以及回滚入口。函数内部 phase 时长不能冒充父进程完整往返时长。还要保留宿主高峰期 RAM 和 commit 余量；guest 上限不是预留容量，其他进程 PID 未变也不能单独证明它们未受资源压力影响。

## 本次关闭结果

在原三个团队、同一个 app-server 的实际制作负载下，45 个资源样本跨越 **2640.92 秒单调时间／2721.917271 秒墙钟时间**，均超过原约 34 分钟失败窗口；全程保持同一生产 guest 启动和 service。故障前两根 data 回合已完成，实际活跃的是三个 business 回合；复测保留其进度，没有重跑已结束的研究来凑并发。

| 验收项 | 实测结果与范围 |
| --- | --- |
| 原检查 | 50 份已完成子检查日志均有全部函数 enter／exit 与 result marker，0 栈／异常；原 40 秒 guard 和返回码校验未变，窗口内没有再次触发该超时 |
| 子进程 phase | 0.069870～0.115985 秒；未逐次记录父进程完整往返时间，不报告不存在的 RTT 最大值／分位数 |
| 真实负载峰值 | 至少两个 LibreOffice 核心转换进程重叠；`MemTotal−MemAvailable` 估计为 6,865,640 KiB，约 6.55 GiB，超过旧 guest 实际 6,070,864 KiB |
| RSS | 两个转换进程的该次 RSS 为 3,504,612／2,381,200 KiB；含共享页，不相加当成唯一物理工作集 |
| guest 压力 | 同 boot 的 memory full 累计增量 459,626 μs；avg300 最大 0.07%，IO full avg300 最大 3.97%；最大 swap 使用 124 KiB，不写成全程零交换 |
| 宿主观测 | 修正后的独立记录含 45 个样本；该记录区间最小可用物理内存约 5.16 GiB，Windows `FreeVirtualMemory` 约 5.21 GiB。最初约十分钟的宿主采样缺失，没有补造 |
| 保全 | 四份 preparation 共 5104 条文件清单核对、315 条源树项核验，原有五个非空 role UUID 保留；新 reviewer 是正常工作进展，不称五根始终同时活跃 |
| 配置与机制 | 配置意图匹配，正常生成的 MCP 项另处理；没有宣称整份配置逐字节不变，没有修改 runtime／工具调度／审批机制 |

近期 PSI 是趋势，不能把旧停止后的 avg300 当作旧故障瞬间峰值，也不能跨两个 boot 直接相减累计计数。10 GB 是这个具体配置和负载的已验证处理值，不是 CDS 的通用最低要求。

## 后续磁盘保护事件单独保留

以上窗口之后，原任务继续至约 59 分钟时，C 盘空间触发原有 `CapacityUnavailable(runtime_free_below_reserve)` 保护。它不是同一 40 秒故障复发，也不是报告质量验收成功。同期宿主系统管理的分页文件曾增长至约 18.53 GB，随后回到约 5.77 GB，C 盘可用量恢复约 21.43 GB；一个大内存宿主进程的归属未确认，没有将它归罪于 CDS 或其他线程，也没有终止未知进程。

没有修改分页文件策略、安全设置或原容量保护线，也没有把空间自然恢复计成本项目清理收益。提高 guest 内存上限前，还须预算宿主 RAM／commit、系统管理分页文件可能占用的磁盘空间和项目原保留线；不能只看当时的空闲 RAM。Microsoft 说明系统管理的分页文件会根据 commit 压力自动增长，见[官方分页文件说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/introduction-to-the-page-file)。

**本次捕获的资源限制与 40 秒超时处置已验收关闭。** 后续业务续接、最终报告及内容质量仍由原任务 owner 按既定流程判断；没有用运行时窗口替代这些业务验收。
