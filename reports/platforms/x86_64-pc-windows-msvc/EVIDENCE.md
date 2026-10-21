# Windows runtime 验收证据

`runtime-qualification.json` 是完整组合结论，实测源码身份保持 `3a59a782f017693be2b0`。发布源码仅改 README 和 Windows manifest 的换行符，见 `release-source-lineage.json` 和仓库 `reports/release-source-lineage.json`。

- 标准 30 项由原 29 项通过与独立 Guardian 补测组成，原失败未改写。
- 131 项本地 RPC 均有原始成功请求/响应配对，并另有副作用和生命周期断言。
- 当前 Windows 完整离线测试 666 项通过，1 项为 Linux 平台专用；无权限原因跳过。
- Windows 10 宿主执行标准门与真实项目；新 Windows Server 2025 客体以同一新原生产物完成两种沙箱模式。
- 两个复杂项目保留实际受测的 94c 身份；当前变更分支由新回归覆盖。并未声称这些任务刚在 3a 重新执行。
- `user-delivery.json` 记录实际 Windows 用户的 `cds`、官方 DeepSeek 路由和默认权限文件效果。

`evidence/` 是经核查的报告、回执与索引副本。只对私人路径和账号标签做展示替换；原始状态、失败语义、source ID 和哈希均保留。`original-to-public-sha256.json` 逐文件记录原始与公开副本 SHA。索引中的完整原始模型历史、凭据库和私密 profile 不公开，原件在任务所有者处保留。

仓库根目录的 `run_acceptance.py`、`verify_*.py` 和 `tests/` 用于新的独立验收。`audit/` 中的只读脚本用于复核这次保留的原始实验布局，不将经脱敏的展示副本当成原始执行数据。
