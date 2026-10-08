# Mission 与 Safety Contract

状态：M4-1 契约冻结基线

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

本节点只冻结输入、输出和稳定原因码。具体检查器与 Human Approval 状态机从 M4-2
开始实现。

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

M4-1 尚未实现：

- 检查顺序和 Safety Supervisor；
- 从已批准 Proposal 生成 `ApprovedSkillCommand`；
- Human Approval 请求、批准、拒绝和超时状态机；
- Safety Trace Event；
- Safety Boundary 的集成测试与 Gazebo 验证。

因此本节点不能声明系统已经具有运行时安全拦截能力。
