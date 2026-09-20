# Flight Skill Contract

状态：M3 实现基线

## 边界

V1 向 Agent 暴露的 Skill 固定为：

```text
takeoff
goto
hold
rtl
land
```

业务模块不得导入 ROS 2 或 PX4 Message Class。Mock Backend 和 PX4 Backend
都通过相同的 `FlightExecutionBackendProtocol` 与业务模块通信。

## 参数

- `TakeoffArgs.target_altitude_m`：相对本地 Home 原点的正数高度。
- `GoToArgs.north_m`：局部坐标系向北位移，单位为米。
- `GoToArgs.east_m`：局部坐标系向东位移，单位为米。
- `GoToArgs.altitude_m`：相对本地 Home 原点的正数高度。
- `GoToArgs.acceptance_radius_m`：正数水平接受半径。
- `HoldArgs.duration_s`：正数持续时间；`None` 表示保持到收到取消。
- `RTLArgs` 和 `LandArgs`：无参数。

业务 Contract 使用便于人理解的正高度。PX4 Adapter 负责转换为 NED 向下轴数值，
调用方不得把原始 NED `z` 值当作 `altitude_m` 传入。

PX4 `NAV_TAKEOFF.param7` 使用 AMSL 高度。真实 Backend 只能在
`WorldState.home_position_wgs84` 有效时，按 `Home AMSL + target_altitude_m` 构造目标；
不得把相对高度直接写入 `param7`。

所有模型禁止未知字段、NaN 和无穷数。Geofence、最大高度、Authority、命令顺序和
当前状态审批仍由 M4 Safety 负责。

## 已批准命令

`FlightExecutionBackendProtocol.execute()` 只接收 `ApprovedSkillCommand`，其中包含：

```text
execution_id
proposal_id
decision_id
skill_name
强类型 arguments
timeout_s
approved_state_id
```

由于 M4 尚未实现，M3 的脚本化测试可以直接构造该命令。生产链路只能根据已通过的
Safety Decision 创建它，原始 Proposal 不得越过 Runtime 边界。

调用方在执行开始前分配 `execution_id`，并发的 `cancel(execution_id)` 才能找到
正在执行的命令。

## 执行结果

终态只能是：

```text
SUCCEEDED
FAILED
TIMED_OUT
CANCELLED
REJECTED_BY_PX4
```

非成功结果必须提供稳定的 `failure_code`，成功结果不得携带失败码。开始与结束时间
必须带时区，并且结束时间不能早于开始时间。

`PX4 ACK == ACCEPTED` 只代表协议层接受命令，不是任务步骤最终成功的证据。M6
State Verifier 会根据实际观测到的飞行器状态独立判断。

## 超时与取消

- 每条命令都必须具有正数 Timeout。
- 超时返回 `TIMED_OUT` 和稳定失败码。
- 取消操作通过 `execution_id` 指定在途任务。
- Runtime 必须保证重复取消不会产生额外副作用。
- 在途任务成功取消后，以 `CANCELLED` 终止。
- 通信链路失效时，PX4 原生 Failsafe 仍具有最终控制权。

## Registry

Registry 暴露描述、参数 JSON Schema、所需状态元数据、Authority 元数据和默认
Timeout。`required_state` 只是供 M4 使用的声明式元数据，本身不会批准命令，也不能
替代 Safety Check。

## 兼容性规则

`MockFlightExecutionBackend` 和 `PX4Ros2FlightExecutionBackend` 必须同时满足
`FlightExecutionBackendProtocol`，且切换
Runtime 时不得修改 Skill、Safety、Verifier 或 Agent 业务代码。
