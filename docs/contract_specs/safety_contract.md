# Mission 与 Safety Contract

状态：M4-4 Geofence、目标高度与任务半径检查基线

## 目的

M4 在 Planner 与 Flight Execution Backend 之间增加不可绕过的确定性仲裁边界：

```text
SkillProposal
+ MissionContract
+ WorldState
+ ControlAuthority
        |
        v
Safety Supervisor
        |
        v
SafetyDecision
```

M4-1 冻结输入、输出和稳定原因码；M4-2 已实现独立的 Schema、State Freshness 和
State ID 检查；M4-3 已实现 Authority、Vehicle State 和 Command Sequence 检查。
Safety Supervisor 总装配与 Human Approval 状态机仍未实现。

## Mission Contract

`MissionContract` 保存 Planner 不能修改的任务目标与硬约束：

- 最大高度、最大水平速度和最大任务半径；
- 最低电量；
- WorldState 最大允许年龄；
- 局部 NED Geofence；
- 允许使用的 Skill；
- 必须人工审批的 Skill；
- 是否要求有效 Home；
- 完成条件与中止条件。

Contract 使用 Pydantic 冻结模型，拒绝未知字段、重复 Skill、非法数值与无效围栏。
Planner 可以提出新 Plan，但不能原地修改 Contract。M4 不定义或执行规划软偏好；如
M5 Planner 确有消费需求，再根据真实规划场景单独冻结偏好 Contract。

## 坐标约定

`NedGeofence` 是局部 NED 平面中的矩形：

```text
north_min_m <= north <= north_max_m
east_min_m  <= east  <= east_max_m
```

业务侧高度仍使用相对 Home 的正数，不使用 NED 的负 `z`。M0 测评数据中的 `x/y`
沿用仿真表达，进入 Safety Fixture 时必须显式转换为 `north/east`，即 `x = north`、
`y = east`。

## Skill Proposal

`SkillProposal` 只是等待检查的请求外壳，不是执行命令。它故意保留原始 `skill_name`
和 `arguments`，让未知 Skill、错误字段或非法参数能够由 Safety 返回稳定的拒绝原因，
而不是在入口解析时丢失审计记录。

Proposal 顶层结构仍然是严格的：额外字段、空 ID 和负 `plan_revision` 会被拒绝。

## Safety Decision

仲裁结果只有三类：

- `APPROVE`：所有确定性检查通过，只能携带 `CHECKS_PASSED`；
- `REJECT`：至少携带一个拒绝原因码；
- `HUMAN_APPROVAL`：等待人工裁决，只能携带 `HUMAN_APPROVAL_REQUIRED`。

只有 `APPROVE` 可以在后续装配逻辑中生成 `ApprovedSkillCommand`。`REJECT` 和
`HUMAN_APPROVAL` 都不得进入 `FlightExecutionBackendProtocol.execute()`。

## 已实现检查

`check_proposal_schema()` 按固定规则检查：

- Proposal 的 `mission_id` 是否属于当前 Mission Contract；
- Skill 名是否存在且位于 `allowed_skills`；
- Skill 参数是否能由对应的强类型参数模型解析。

未知或未授权 Skill 返回 `SKILL_NOT_ALLOWED`。任务归属错误或参数错误返回
`INVALID_SCHEMA`；多个错误按 `INVALID_SCHEMA -> SKILL_NOT_ALLOWED` 返回。

`check_proposal_state()` 按固定顺序检查：

1. `WorldState` 是否超过 `max_state_age_ms`；
2. Proposal 的 `based_on_state_id` 是否等于当前 `WorldState.state_id`。

对应原因码顺序固定为 `STALE_STATE -> STATE_ID_MISMATCH`。状态年龄等于上限时仍算
新鲜，超过一毫秒即拒绝。

`check_control_authority()` 只允许 `AGENT_ALLOWED`。Human、Recovery 或 PX4 Failsafe
持有控制权时，普通 Agent Proposal 返回 `AUTHORITY_NOT_GRANTED`。

`check_vehicle_state()` 检查状态是否足以判断指定 Skill：

- `landed` 未知时返回 `INVALID_VEHICLE_STATE`；
- 空中状态但未解锁时返回 `INVALID_VEHICLE_STATE`；
- GoTo、Hold 或 Land 缺少有效位置时返回 `INVALID_VEHICLE_STATE`。

`check_command_sequence()` 只负责生命周期转换：地面只允许 Takeoff；空中允许 GoTo、
Hold、RTL 和 Land；`AUTO_TAKEOFF`、`AUTO_RTL` 或 `AUTO_LAND` 进行期间不接受新的普通
Agent Skill。非法转换返回 `INVALID_COMMAND_SEQUENCE`。若 `landed` 未知，则与
Vehicle State Check 一样返回 `INVALID_VEHICLE_STATE`，确保单独调用 Sequence 时也不会
把未知状态误判为通过。两个检查的结果由后续 Supervisor 去重。

Home、Failsafe、Link 和 Health 不属于 M4-3，由 M4-5 单独检查。

`check_geofence()` 只对 GoTo 生效：目标点越过扣除 `boundary_margin_m` 后的矩形返回
`WAYPOINT_OUTSIDE_GEOFENCE`；目标合法但当前位置越界返回 `ROUTE_OUTSIDE_GEOFENCE`；
当前本地位置无效返回 `INVALID_VEHICLE_STATE`。起点和终点都在轴对齐矩形内时，
两点的直线段也在内；检查不保证实际 PX4 路径不越界，PX4 固件围栏仍须开启。

`check_flight_envelope()` 只对 Takeoff/GoTo 的目标高度生效，超过
`max_altitude_m` 返回 `ALTITUDE_ABOVE_ENVELOPE`。`check_mission_radius()` 只对
GoTo 生效，以 `WorldState.home_position_ned_m` 为圆心计算目标的水平距离；超出
`max_mission_radius_m` 返回 `MISSION_RADIUS_EXCEEDED`，缺少有效局部 Home 返回
`HOME_REFERENCE_UNAVAILABLE`。PX4 `HomePosition.valid_lpos` 和局部坐标数值共同
决定参考是否有效，不把 NED 原点直接当 Home。现有 Skill 无目标速度参数，
`SPEED_ABOVE_ENVELOPE` 还不会由当前检查器产生。

## 原因码

原因码是日志、Trace、测试与测评集共同使用的稳定机器字段，不使用自由文本代替。
当前冻结的主要类别包括：

- Schema：`INVALID_SCHEMA`、`SKILL_NOT_ALLOWED`；
- State：`STALE_STATE`、`STATE_ID_MISMATCH`、`INVALID_VEHICLE_STATE`；
- Sequence：`INVALID_COMMAND_SEQUENCE`；
- Geofence：`WAYPOINT_OUTSIDE_GEOFENCE`、`ROUTE_OUTSIDE_GEOFENCE`；
- Envelope：`ALTITUDE_ABOVE_ENVELOPE`、`SPEED_ABOVE_ENVELOPE`、
  `MISSION_RADIUS_EXCEEDED`；
- Resource/Health：`INSUFFICIENT_BATTERY`、`SYSTEM_UNHEALTHY`、
  `HOME_REFERENCE_UNAVAILABLE`；
- Authority/Approval：`AUTHORITY_NOT_GRANTED`、`HUMAN_APPROVAL_REQUIRED`、
  `HUMAN_APPROVAL_REJECTED`、`HUMAN_APPROVAL_TIMEOUT`。

`ALTITUDE_ABOVE_ENVELOPE`、`WAYPOINT_OUTSIDE_GEOFENCE` 和
`AUTHORITY_NOT_GRANTED` 与现有 Dev 测评集保持一致。

## 当前边界

当前尚未实现：

- Safety Supervisor 对所有检查器的总装配；
- 从已批准 Proposal 生成 `ApprovedSkillCommand`；
- Human Approval 请求、批准、拒绝和超时状态机；
- Safety Trace Event；
- 目标速度、Battery、Home、Failsafe、Link 和 Health 检查；
- Safety Boundary 的集成测试与 Gazebo 验证。

因此当前只能声明独立规则可确定性返回拒绝原因，不能声明生产执行链已经不可绕过。
