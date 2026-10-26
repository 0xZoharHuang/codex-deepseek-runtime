# 0.6.5 固定本地 runtime 资格

功能资格已通过。正式公开发行、匿名下载与日常入口激活是另外的交付状态，以本目录后续发布回执为准；本文不把草稿 release 当作已经公开。

本版修复 Windows 标准用户目录下的沙箱准备失败。原元数据维护只接受实例用户作为目录所有者，遗漏了合法的 SYSTEM-owned profile 和 Administrators-owned 设置目录。新谓词仅在这段元数据维护中接受实例用户、SYSTEM 和 BUILTIN Administrators；不改目录所有者，不修改任务循环、工具执行、审批、线程或子智能体业务。目录 FileID、防重解析、操作系统 WRITE_DAC、显式 DENY、非继承 FILE_READ_ATTRIBUTES 与精确撤销 journal 保留。

固定身份为 Python distribution 0.6.5、产品 source072be79ac2b773d2684c（101 项产品输入、29 个 Python 文件）、原生 Codex 0.164.0-alpha.7 / fe136d4e70ef2b5bbed40a2d22b3aabf267796d3。模型固定 deepseek/deepseek-v4.1-flash-1017。Windows 新归档 b3438fec5f7e5979cac479d3db8e26938291a54e4ed46e0b9673734ffd981a73，共3022文件，包含完整prepared源码与选中patch链。Unix继续使用原字节的0.6.2资产。上游本身是alpha，不改称上游稳定版。

| 当前运行 | 结果 | 明确范围 |
| --- | --- | --- |
| Windows完整installed suite | 2295收集，2291通过、4平台不适用、0漏选 | 一次完整执行，独立collection/JUnit逐项闭合；High仅offline合同 |
| Windows独立native transport | 21/21 | 普通非提权执行；21项全属完整suite，不额外加进独立用例数 |
| Windows标准profile | Full refresh、冷重入、实际非管理员受限读取通过 | 同一个曾复现0.6.4失败的home，保留原始反例 |
| 权限负例与恢复 | 普通foreign-owner拒绝/精确恢复、critical RX DENY/恢复、inside写入/outside拒绝 | 先检查真实effective roots，再检查文件字节与身份 |
| 当前模型CodeMode任务 | 11次custom exec、1次原生BM25 ToolSearch、1次MCP digest；未知nonce/hash匹配 | 一个受控任务，12个真实请求全经独立generation GET确认DeepSeek；仅显式启用ToolSearch |
| native uninstall | 5条metadata ACE精确撤销，实例2账号/1组删除，进程自然退出 | 保留home和普通filesystem权限是上游明确合同，不虚称所有ACL恢复 |
| Linux hosted | 2295=2255通过+40平台skip；native21、controller173均通过 | 当前0.6.5 Python与原0.6.2 native；完整分区0漏选 |
| macOS hosted | 2295=2254通过+41平台skip；native21、controller173均通过 | 临时GitHub runner，未操作用户Mac App、登录、钥匙串或CDS |

原始失败和限制必须保留：

- 0.6.4真实profile失败已再次复现；此前C盘根目录的自有home与profile安装/help不能替代这个拓扑。
- 新模型任务有3次PowerShell失败和3次CodeMode JSON解析错误，同一turn内恢复；single_run_all_green=false。没有发生新的wait调用；D69真实wait是原身份的历史证据。
- 模型shell的实际账号由已有OS事件与命令/时间/PID交叉绑定；对已退出PID没有直接查询IsTokenRestricted。read-only/never、没有escalation参数、真实CLM拒绝以及同源码的实际token probe分别记录。
- 首次hosted run40312383584因旧的显式current-recipe pin在下载前失败。修正只更新CI current pin并新增两个实际静态pin回归；原Unix资产、producer和recipe投影均未改变。成功run为[40314383545](https://github.com/0xZoharHuang/codex-deepseek-runtime/actions/runs/40314383545)，commit097013bb7eb74769cfcc369eecc76efb8d8be1bd。
- 原编译结束观察中VCTIP暂留、身份ABI/版本层误断言、QA计划路径分隔符、ordinary RX与metadata混淆及workspace输出变量遮蔽均保留。修正针对可证明的控制器输入/观察问题，没有放宽产品断言、重签旧执行或重跑已完成副作用。
- 普通read ACL按原生uninstall合同保留；测试实例遗留的已删除SID Allow将在项目最终Windows卫生清理时另行精确移除，不改原生默认语义，不碰活跃/未知namespace。

[Windows完整suite与native](windows-offline-native.json)、[profile权限链](windows-profile-permissions.json)、[当前模型任务](windows-model-code-mode-toolsearch.json)、[总runtime摘要](windows-runtime-summary.json)、[native卸载](windows-native-uninstall.json)、[Unix独立原件回读](unix-current-installed-qualification.json)分别保留各自scope。所有scope均不能用测试数量当成所有输入的穷尽证明。

更广的本地机制、接口与复杂工程证据保留在[0.6.2冻结资格](../0.6.2/README.md)及[稳定方案](../../../docs/STABLE_PLAN.zh-CN.md)的D69谱系。本版只改Windows祖先元数据模块；没有将这些历史复杂任务改签为新运行。Jinja的runtime与业务曾通过，HTTPcore的runtime通过而D69业务失败、超时和provider错误保留。模型质量和OpenAI云端账户权益不属于本地runtime等价承诺。Codex App的CADSG资格独立，本报告不宣布App已通过。
