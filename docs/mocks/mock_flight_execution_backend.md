# Mock Flight Execution Backend

`MockFlightExecutionBackend` 是 Agent 层的确定性飞行器替身。它接收与真实 PX4 Adapter 相同的 `ApprovedSkillCommand`，返回 `SkillResult`，但不连接 ROS2、PX4 或 Gazebo。

## 行为

- 成功 `takeoff` 解锁、离地，并将 NED `z` 设为目标高度的负数。
- 成功 `goto` 更新到命令中的 NED 北、东、正高度目标。
- 成功 `hold` 保持状态；`rtl` 返回北、东原点；`land` 着陆并解除解锁。
- 只有成功推进 `WorldState`。拒绝、超时、无进展和取消不推进状态。

Mock 从初始 `WorldState.received_at` 起使用虚拟时钟，不等待真实时间。成功、拒绝、无进展和取消固定推进一秒；超时推进命令的 `timeout_s`。以 `execution_id -> MockExecutionOutcome` 注入 `rejected`、`timed_out` 或 `no_progress`；协作式 `cancel()` 会使在途命令返回 `CANCELLED`。

成功返回 `px4_ack=ACCEPTED`，拒绝返回 `DENIED`，其他失败不伪造 ACK。实现没有随机过程，因此相同输入必然产生相同结果，不需要 seed。

M3 的结果注入由构造函数的 `execution_id -> MockExecutionOutcome` 映射完成。评测 YAML
Fixture 与读取它们的 Runner 会在 M8 一起交付，避免保留尚未被任何代码消费的配置文件。

## 不模拟

不模拟 PX4 状态机、控制器、物理轨迹、传感器、通信时延、真实 ACK 生命周期、failsafe 或地理围栏。这些飞控事实由 PX4/ROS2/Gazebo E2E 覆盖。
