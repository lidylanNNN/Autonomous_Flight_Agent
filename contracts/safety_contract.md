# 飞行 Safety Contract

状态：M4-7 首批总仲裁与 PX4 限速校验已装配

## 现状

M4 已为旧受控任务入口建立动作提案到 Flight Execution Backend 的确定性仲裁边界。旧 M3 调试入口仍能直连 Backend。当前 Gate 输入为：

```text
AtomicActionProposal
+ MissionConstraints
+ expected_mission_id（旧任务入口提供）
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
State ID 检查；M4-3 已实现 Authority、Vehicle State 和 Command Sequence 检查；
M4-4/M4-5 已实现独立围栏、包线、电池、健康与 Home 检查。
M4-7 Safety Supervisor 已按固定顺序组合独立检查器，并通过 ROS 适配器读取、
验证当前 PX4 `MPC_XY_VEL_MAX`；读数有效期为 2 秒，读取失败或超限拒绝。
直接批准路径已可在输入快照上生成批准命令。当前旧 `MissionService` 使用的 `SafetyExecutionGate` 已在派发前写入 Safety Trace，
只有直接 `APPROVE` 可以派发；旧 M3 调试脚本仍能直连后端。

## V1 目标

- 飞行 Gate 接收 `AtomicActionProposal`、系统 `MissionConstraints`、`WorldState` 和 `ControlAuthority`，不接收 `expected_mission_id`。
- Proposal、Safety 决策与 Trace 不使用任务 ID；保留提案、决策和执行 ID 追踪单次动作。
- 飞行动作服务启动时从 `--safety-json` 指定的新 JSON 文件读取 `MissionConstraints` 根对象；文件缺少、不可读或校验失败时不发布服务。
- 删除任务 ID 匹配检查时，保留其他 Safety 检查和拒绝语义。迁移步骤见 [MX-4 结构变更单](../plans/MX-4-structure-change.md)。

## 安全约束

`MissionConstraints` 模型保存动作提案不能修改的安全限制：

- 最大高度、最大水平速度和最大任务半径；
- 最低电量；
- WorldState 最大允许年龄；
- 局部 NED Geofence；
- 允许使用的 Skill；
- 是否要求有效 Home；

约束模型拒绝未知字段、重复 Skill、非法数值与无效围栏。现有旧入口仍持有 `MissionContract`，从中取出 `constraints` 和 `mission_id` 交给 Gate。V1 飞行动作服务使用新 JSON 文件提供系统限制，不增加逐任务限制。该文件不包含 `MissionContract` 的任务字段。

## 坐标约定

`NedGeofence` 是局部 NED 平面中的矩形：

```text
north_min_m <= north <= north_max_m
east_min_m  <= east  <= east_max_m
```

业务侧高度仍使用相对 Home 的正数，不使用 NED 的负 `z`。M0 测评数据中的 `x/y`
沿用仿真表达，进入 Safety Fixture 时必须显式转换为 `north/east`，即 `x = north`、
`y = east`。

## Atomic Action Proposal

`AtomicActionProposal` 只是等待检查的请求外壳，不是执行命令。它故意保留原始 `skill_name`
和 `arguments`，让未知 Skill、错误字段或非法参数能够由 Safety 返回稳定的拒绝原因，
而不是在入口解析时丢失审计记录。

Proposal 顶层结构仍然是严格的：额外字段、空 ID 和负 `plan_revision` 会被拒绝。

## Safety Decision

V1 目标仲裁结果只有两类：

- `APPROVE`：所有确定性检查通过，只能携带 `CHECKS_PASSED`；
- `REJECT`：至少携带一个拒绝原因码。

只有 `APPROVE` 可以由 `SafetySupervisor.prepare_command()` 生成 `ApprovedSkillCommand`。`REJECT` 不得进入 `FlightExecutionBackendProtocol.execute()`。

X1 已移除按 Skill 名触发的 Proposal 级审批分支；当前代码仍有 `HUMAN_APPROVAL` 枚举与原因码，尚未完全迁移到上述 V1 目标契约。如出现该决策，Episode 按流程错误处理，不计自主成功。

## 已实现检查

`check_proposal_schema()` 按固定规则检查：

- Proposal 的 `mission_id` 是否匹配可信调用方提供的 `expected_mission_id`；
- Skill 名是否存在且位于 `allowed_skills`；
- Skill 参数是否能由对应的强类型参数模型解析。

未知或未授权 Skill 返回 `SKILL_NOT_ALLOWED`。当前任务 ID 不匹配或参数错误返回
`INVALID_SCHEMA`；多个错误按 `INVALID_SCHEMA -> SKILL_NOT_ALLOWED` 返回。V1 移除任务 ID 匹配分支。

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
`check_flight_envelope()` 尚无动作目标速度可检查；但 `check_controller_speed_limit()` 在 PX4 控制器限速高于 `MissionConstraints` 上限时会返回 `SPEED_ABOVE_ENVELOPE`。

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
- Authority：`AUTHORITY_NOT_GRANTED`。

`ALTITUDE_ABOVE_ENVELOPE`、`WAYPOINT_OUTSIDE_GEOFENCE` 和
`AUTHORITY_NOT_GRANTED` 与现有 Dev 测评集保持一致。

## 当前边界

已装配独立检查器、PX4 限速读取和旧任务入口的执行门。替身集成测试已覆盖旧入口经 Gate 派发、拒绝不派发与任务 ID 不匹配；ROS 入口工厂测试也覆盖 Gate 装配。但当前尚未完成：

- 隔离旧 M3 调试脚本等可直接调用后端的路径；
- 新 ROS-LLM 飞行原子动作服务与 Gate 装配；
- Safety Gate 的 PX4/Gazebo 受控链验证。

因此当前可以声明旧受控入口的总仲裁能确定性返回决定，不能声明目标生产链已经接入或所有执行路径都经过 Gate。

原 M4-6 Proposal 级审批协议、状态机和 Mock 仍在仓库，但不属于 M1–M8 设计或 V1 目标契约。X1 已移除 `human_approval_skills` 与 Supervisor 分支；相关原因码、决策枚举及原型公共导出仍待后续批次迁移。测评后的产品阶段需重新定义 Plan 级审批。

后续版本待优化：PX4 `HomePosition` 按变更发布，M4 只依据已接收的 Home
有效标志与位置引用进行检查，不主动回读 PX4 当前 Home；缓存值与飞控当前值
的一致性尚未得到证明。后续应在最终放行前按需回读并核对，读取失败、无效或
不一致时拒绝；不能机械套用周期 Topic 的 1 秒年龄上限。
