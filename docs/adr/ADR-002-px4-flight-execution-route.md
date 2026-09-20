# ADR-002 — PX4 飞行执行的混合路线

状态：已接受

## 背景

M3 需要为 `takeoff`、`goto`、`hold`、`rtl`、`land` 选择真实 PX4 执行路线。当前
飞行器是 x500 多旋翼，业务 Contract 使用局部 NED 坐标；ROS 适配层使用固定的 PX4
v1.16.2 `px4_msgs`。选择必须同时满足本地坐标任务、PX4 原生 failsafe 与可审计 ACK。

## 候选方案

1. 所有 Skill 使用 PX4 高层 `VehicleCommand` / Mission。
2. 所有 Skill 使用 ROS 2 Offboard position setpoint。
3. 按 Skill 使用 PX4 原生自动模式与受控 Offboard 的混合路线。

## 决策

采用方案 3。

| Skill | M3 执行路线 | 原因 |
|---|---|---|
| `takeoff` | PX4 原生起飞命令 / 自动模式 | 起飞是 PX4 已有的飞行阶段；Adapter 在 Home 全局参考有效时构造 PX4 所需参数。 |
| `goto` | ROS 2 Offboard 的 NED `TrajectorySetpoint` | 业务 Contract 以局部 NED 表达目标；PX4 的 `DO_REPOSITION` 使用 WGS84 全局位置。 |
| `hold` | ROS 2 Offboard 保持当前位置 setpoint | 可准确保持当前局部位置，并可由 `cancel()` 结束。 |
| `rtl` | PX4 原生 Return 模式 / 命令 | 返航、返航高度、原生 failsafe 与最终降落由 PX4 管理。 |
| `land` | PX4 原生 Land 模式 / 命令 | PX4 负责下降、落地检测和自动解锁。 |

Offboard heartbeat 由 `Px4Ros2FlightExecutionBackend` 的确定性定时器以 10 Hz 持续
发布 `OffboardControlMode`；缓存的 `TrajectorySetpoint` 也以相同频率发布。LLM、Planner
或单次 Skill 调用不得承担周期发送责任。

## 原因

- PX4 v1.16 要求 Offboard 在切换前已收到超过一秒的生命信号，并在运行中持续收到大于
  2 Hz 的信号；信号丢失后由 PX4 根据参数进入原生 failsafe。
- PX4 明确提示起飞、降落、返航更适合使用相应自动模式，而不是 Offboard。
- `VehicleCommand` 的 `NAV_TAKEOFF` / `NAV_LAND` 参数是全局 WGS84 / AMSL 语义，而
  当前 `GoToArgs` 是局部 NED 语义。把两者混成一个“万能命令”会隐藏坐标与高度转换风险。
- 本项目当前是 Python `rclpy` + 固定 `px4_msgs` 集成；PX4 ROS 2 Control Interface 在
  v1.16 文档中仍标记为 experimental，M3 不把它引入为额外 C++ 依赖。

## ACK、成功与取消

- `VehicleCommandAck` 只证明 PX4 对命令请求的响应，不证明 Skill 的最终任务成功。
- `goto` / `hold` 的完成由状态收敛、dwell 和 timeout 判定；完整 Verifier 在 M6 交付。
- M3-3 先实现 ACK 与 `execution_id` 关联；取消时必须停止当前 Offboard 目标，并请求一个
  明确的 PX4 保持/安全模式。M4 才决定不同风险等级下是 Hold、RTL 还是 Land。

## 后果

- `Px4Ros2FlightExecutionBackend` 必须同时维护命令 ACK 关联和 Offboard 定时器。
- Backend 必须将业务侧正高度与 PX4 NED 向下轴明确转换，并记录使用的坐标参考。
- 真实 PX4/Gazebo 测试必须覆盖 Offboard 信号丢失、ACK 拒绝、超时与取消。
- M3 不引入视觉、VLA 或自主选点降落；它们若接入，只能经由后续感知事实和 Safety 边界。

## 依据

- [PX4 v1.16 Offboard Mode](https://docs.px4.io/v1.16/en/flight_modes/offboard)：Offboard
  heartbeat、NED setpoint 与原生模式建议。
- [PX4 v1.16 VehicleCommand](https://docs.px4.io/v1.16/en/msg_docs/VehicleCommand)：
  `NAV_TAKEOFF`、`NAV_LAND`、`DO_REPOSITION` 的参数语义。
- [PX4 v1.16 ROS 2 Control Interface](https://docs.px4.io/v1.16/en/ros2/px4_ros2_control_interface)：
  该库的 experimental 状态。
