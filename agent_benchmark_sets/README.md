# Agent 测评集

`agent_benchmark_sets/` 保存 Autonomous Flight Agent 的测评任务、初始飞行器状态、
故障注入条件、预期结果与 Dev / Validation / Frozen Test 划分。

它不是 PX4 的 Mission 文件目录，也不是用户在线提交给 Agent 的任务输入目录。PX4
Mission、ROS 2 Topic 与仿真运行 Artifact 不得写入此处。

- `dev/`：开发与回归期间可修改的 Agent 测评任务。
- `validation/`：里程碑级检查任务，不用于最终对外指标。
- `frozen_test/`：M9 后冻结的最终测评任务，不得用于调参或 Debug。
- `schemas/`：测评任务和数据划分 Manifest 的校验模型。
