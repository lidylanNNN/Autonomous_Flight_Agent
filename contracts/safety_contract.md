# 飞行 Safety Contract

状态：M4-7 总仲裁与 PX4 限速校验已装配；MX-4 1b 已移除 Safety 动作提案的旧任务身份

## 现状

飞行动作服务通过 Gate 建立动作提案到 Flight Execution Backend 的确定性仲裁边界。独立 M3 调试入口仍直接调用 Backend。当前调用流为：

```text
[FlightAtomicActionServer] --动作名、参数、提案 ID + MissionConstraints--> [SafetyExecutionGate]
[SafetyExecutionGate] --限速证据读取请求--> [SafetySupervisor]
[SafetySupervisor] --读取并校验限速请求--> [Px4ParameterAdapter]
[Px4ParameterAdapter] --已校验读数或读取异常--> [SafetySupervisor]
[SafetySupervisor] --保留接收时间的读数或 None--> [SafetyExecutionGate]
[SafetyExecutionGate] --WorldState 读取请求--> [FlightExecutionBackend]
[FlightExecutionBackend] --WorldState--> [SafetyExecutionGate]
[Authority reader] --ControlAuthority--> [SafetyExecutionGate]
[SafetyExecutionGate] --AtomicActionProposal + MissionConstraints + WorldState + ControlAuthority + 限速读数--> [SafetySupervisor]
[SafetySupervisor] --SafetyDecision--> [SafetyExecutionGate]
[SafetyExecutionGate] --AtomicActionProposal + SafetyDecision--> [TraceRecorder]
[SafetyExecutionGate] --ApprovedSkillCommand(仅批准时)--> [FlightExecutionBackend]
```

M4-1 冻结输入、输出和稳定原因码；M4-2 已实现独立的 Schema、State Freshness 和
State ID 检查；M4-3 已实现 Authority、Vehicle State 和 Command Sequence 检查；
M4-4/M4-5 已实现独立围栏、包线、电池、健康与 Home 检查。
M4-7 Safety Supervisor 已按固定顺序组合独立检查器，并通过 ROS 适配器读取、
验证当前 PX4 `MPC_XY_VEL_MAX`；读数有效期为 2 秒，读取失败或超限拒绝。
直接批准路径已可在输入快照上生成批准命令。飞行动作服务使用的 `SafetyExecutionGate` 已在派发前写入 Safety Trace，
只有直接 `APPROVE` 可以派发；独立 M3 调试脚本仍能直连后端。

## V1 目标

- 飞行服务接入后，Gate 使用从新 JSON 加载的系统 `MissionConstraints`、当前 `WorldState` 和 `ControlAuthority`。
- Proposal、Safety 决策与 Trace 不使用任务 ID；保留提案、决策和执行 ID 追踪单次动作。
- 飞行动作服务启动时从 `--safety-json` 指定的新 JSON 文件读取 `MissionConstraints` 根对象；文件缺少、不可读或校验失败时不发布服务。
- V1 受控 SITL 的授权假设以 [SPEC 第 5 节](../SPEC_DEV.md#5-安全不变量)为准；默认授权仅满足 Authority 检查，不免除其他检查。
- 保留参数、状态、Authority、围栏、包线和健康检查及拒绝语义。迁移步骤见 [MX-4 结构变更单](../plans/MX-4-structure-change.md)。

## 检查前准备参数证据（2026-11-19）

限速参数的异步读取、来源和数值校验先完成，再获取检查所用的完整 WorldState 和 Authority。随后执行完整检查及末次时效复核，不在持有状态期间等待飞控参数。位置各档阈值不变；此调整减少参数等待消耗位置有效期，不修复 ROS 更新迟滞。

参数来源增加返回已校验读数的接口，复用 PX4 现有读数的 `value/received_at`；原浮点读取接口保留供调试入口使用。Supervisor 在完整检查时再次验证读数实际接收年龄不超过 2 秒、非未来且有时区，不能把准备时间冒充接收时间。读取失败、无效或过期证据均不得放行。

生产飞行动作服务把动作名、参数及提案 ID 交给 Gate；Gate 冻结输入，准备参数证据后读取状态，再用该状态 ID 创建提案。已有绑定状态 ID 的 `submit(proposal, constraints)` 仍严格核对原 ID，不重新绑定或绕过不匹配拒绝。所有围栏、电量、模式、健康及 Authority 检查均使用等待后的同一状态。

影响范围为核心 Supervisor/Gate、参数适配器及飞行动作服务调用方；ROS 服务字段、动作参数和限制来源不变。对于原先可凭状态或权限早退的请求，现在可能先发生参数读取；已知读取故障仍保留诊断，未取得批准或 Trace 失败均零派发。

实现和验证必须同时同步，避免接口失配。回滚时按本批差异整体恢复核心、参数适配及调用方，并恢复对应策略版本与测试；不能只把状态读取移回参数等待之前或单独回滚接口。用户已授权本次顺序调整，执行事实见 M6 后续记录。

## 安全约束

`MissionConstraints` 模型保存动作提案不能修改的安全限制：

- 最大高度、最大水平速度和最大任务半径；
- 最低电量；
- 新格式 WorldState 快照缓存最大允许年龄；旧格式仍为整体消息年龄；
- 局部 NED Geofence；
- 允许使用的 Skill；
- 是否要求有效 Home；

- 约束模型拒绝未知字段、重复 Skill、非法数值与无效围栏。
- [`flight_safety.example.json`](../configs/flight_safety.example.json) 可通过模型校验。其数值来自测试，未获批用于生产飞行。
- `load_safety_constraints(path: Path)` 读取显式 JSON 路径，返回 `MissionConstraints`。读取或校验失败时抛出 `ValueError`，没有默认回退。
- 当前 `flight_atomic_action_server` 已接入 `--safety-json`；入口和限制加载现状见 [SPEC_DEV](../SPEC_DEV.md#2-当前实现基线)。

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

Proposal 顶层结构仍然是严格的：额外字段、空 `proposal_id` 与空 `based_on_state_id` 会被拒绝。旧 `mission_id`、`plan_revision`、`step_id` 不再属于 Proposal。

## Safety Decision

V1 当前仲裁结果只有两类：

- `APPROVE`：所有确定性检查通过，只能携带 `CHECKS_PASSED`；
- `REJECT`：至少携带一个拒绝原因码。

Supervisor 只返回决策。Gate 成功写入决策 Trace 后，仅对 `APPROVE` 分配新的 `execution_id`，并从本次检查的提案快照创建绑定 `decision_id` 的 `ApprovedSkillCommand`，再调用 Backend。拒绝和 Trace 写入失败不分配执行 ID、不派发；调用方不得预分配执行 ID。

旧 `HUMAN_APPROVAL` 决策及 `HUMAN_APPROVAL_REQUIRED/REJECTED/TIMEOUT` 原因码已移除；包含这些旧值的 JSON 与 Trace 将校验失败，TraceReplay 保留文件及行号错误，不自动转换为当前决策。现行测评数据未引用这些旧值；历史记录保持原样，需要使用对应历史版本读取。

## 已实现检查

`check_proposal_schema()` 按固定规则检查：

- Skill 名是否存在且位于 `allowed_skills`；
- Skill 参数是否能由对应的强类型参数模型解析。

未知或未授权 Skill 返回 `SKILL_NOT_ALLOWED`。参数错误返回 `INVALID_SCHEMA`；多个错误按 `INVALID_SCHEMA -> SKILL_NOT_ALLOWED` 返回。

`check_proposal_state()` 按固定顺序检查：

1. `WorldState` 是否满足[报文分级及缓存时效](#worldstate-报文新鲜度分级)；
2. Proposal 的 `based_on_state_id` 是否等于当前 `WorldState.state_id`。

对应原因码顺序固定为 `STALE_STATE -> STATE_ID_MISMATCH`。新格式按各报文等级及快照缓存年龄检查；旧格式保持统一年龄判断。各自等于上限时仍算新鲜，新格式超出即拒绝。

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

已装配独立检查器、PX4 限速读取及五个 ROS 飞行动作服务的 Gate；旧任务入口已删除。当前生产装配与替身主链证据见 [MX-5 审计](../plans/MX-5-audit.md#实际调用流)。

ROS 侧 main 已按 [SPEC 的 V1 约定](../SPEC_DEV.md#5-安全不变量)设置默认权限；启动设置验证见 [7c 记录](../plans/M4.md#7c-实施记录)。Takeoff/Land 的真实批准执行与 PX4/Gazebo 起降已通过，证据与范围见 [7f 记录](../plans/M4.md#7f-实施记录)。M3 调试入口直连 Backend，不属于受控服务链；不能声明仓库所有路径均经过 Gate。后续实施与验证见 [M4 计划](../plans/M4.md)。

原 M4-6 Proposal 级审批模型、协议、状态机、Mock、公共导出和专属测试已按 [M4-8b-1/2](../plans/M4.md#8b-18b-2-删除范围与影响) 删除。旧审批原因码、决策枚举及三态 SafetyDecision 校验已按 [8b-3 记录](../plans/M4.md#8b-3-契约收敛) 清理。测评后的产品阶段需重新定义 Plan 级审批。

后续版本待优化：PX4 `HomePosition` 按变更发布，M4 只依据已接收的 Home
有效标志与位置引用进行检查，不主动回读 PX4 当前 Home；缓存值与飞控当前值
的一致性尚未得到证明。后续应在最终放行前按需回读并核对，读取失败、无效或
不一致时拒绝；不能机械套用周期 Topic 的 1 秒年龄上限。

## WorldState 报文新鲜度分级

状态：2026-11-18 用户确认后实施；生产聚合器与 Safety 已接入，受控两点 SITL 证据见 [M6 分级实施记录](../plans/M6.md#t05b-分级新鲜度实施与两点复验2026-11-18)。

### 现状

T05b 连续拒绝的依据见[状态过期排查](../plans/M6.md#t05b-状态过期排查2026-11-18)。变更前聚合器将最旧必需消息的年龄与统一上限比较，快照中的新鲜标志不会随缓存等待自动更新。受控 SITL 的着陆状态稳定发布周期约为 1 秒，存在到达下一次消息前被判过期的窗口。

### 目标

为 WorldState 中的周期报文显式分配等级，由等级确定时效阈值；聚合器与 Safety 使用同一判定，逐项检查，不建立或遍历“报文名到等级/阈值”的字典。

### 约束

以下数值是本次用户确认的受控 SITL 策略，不是 PX4 保证或真机安全结论。保留派发前复核、缺失必需消息拒绝、状态 ID 核对、Trace 写入失败零派发及现有健康检查。分级不替代在途安全监测。

### 等级与报文分配

等级使用 `FreshnessLevel` 枚举；阈值集中由一个 `match level` 函数返回，未知等级报错，不回退到宽松档。

| 等级 | 最大接收年龄 | 本次用途 |
|---|---:|---|
| `FAST` | 500 ms | 对位置变化敏感的状态 |
| `NORMAL` | 1000 ms | 飞控模式、解锁及电池状态 |
| `SLOW` | 1500 ms | 稳定时约 1 Hz 更新的着陆状态 |

| WorldState 的输入报文 | 分配等级 | 使用方式 |
|---|---|---|
| `VehicleLocalPosition` | `FAST` | 位置、速度及其有效性共用本报文的接收时间。 |
| `VehicleStatus` | `NORMAL` | 解锁、模式、failsafe、起飞检查等字段共用本报文的接收时间。 |
| `BatteryStatus` | `NORMAL` | 电量、连接与故障字段共同受限；不因电量变化慢而放宽故障字段。 |
| `VehicleLandDetected` | `SLOW` | 着陆状态按独立接收时间判定，给稳定发布周期留出余量。 |
| `HomePosition` | 不分配周期等级 | 保留现有 Home 有效性与参考检查；当前缓存一致性缺口仍按本契约“当前边界”处理，不在本批修复。 |
| `VehicleCommandAck` | 不分配周期等级 | WorldState 中仅为诊断摘要；命令完成仍由命令适配器的当前等待与 ACK 规则判断，不因旧 ACK 过期拒绝整份状态。 |

相机报文和 `/clock` 不属于本次 WorldState 输入，继续按[采集有效性契约](rosllm_services.md#m6-t02b-采集有效性判据)处理，不随本次分级改动。

### 对象职责与接口

WorldState 的可选 `freshness: WorldStateFreshness` 保存 `snapshot_at` 和四项 `*_received_at`；嵌套对象的五个字段均须显式提供，尚未收到报文时对应接收时间为 null。不从 `source_timestamp_us` 推算各消息年龄，也不从 `health_flags` 反推接收时间。接收时间必须带时区，缺失的必需消息判为无效。

聚合器在四个更新函数中分别保存时间，生成快照时显式调用四次共同的新鲜度判断，再用四项布尔值计算链路健康；不通过字典推导式或报文名遍历派发规则。等级由代码中的固定分配决定，报文内容、模型输入不能自行选择宽松等级。

Safety 初次检查及末次复核均依据各项接收时间和分配等级重新计算，不能只信快照生成时的健康标志。原 `received_at` 与 `state_age_ms` 保留诊断含义；不能再用最旧报文年龄与统一 1000 ms 比较来覆盖分级结果。`max_state_age_ms` 用于限制新格式快照本身的缓存年龄，按快照生成时间计算，避免缓存被无限复用。

旧数据没有逐报文时间时仍按旧格式的统一年龄规则读取和回放，不凭空补时间后套用新策略；生产聚合器只生成完整新格式。仅有部分新时间字段的状态拒绝。Trace 中 `freshness` 对象表示新规则，缺失或 null 表示旧规则；当前生产策略版本为 `mx4-flight-actions-evidence-first-v1`，同时标识先准备参数证据再取状态的顺序；此前分级策略记录沿用原版本名。旧数据不得冒充新策略通过的证据。

新增必需报文须在契约中说明消费者、旧数据的风险、发布周期依据与等级，然后显式增加时间字段、检查和边界验证；未分配等级的报文不得参与安全批准。不建立动态注册或自动分类机制。

### 实施记录

模型、聚合器、生产策略版本、回放兼容及验证已同步。Safety 两次检查通过原有 `WorldState.is_fresh()` 调用进入同一分级规则，不另建检查入口。实际修改、验证范围和未完成项统一记录在上方链接的 M6 阶段记录。

### 验证

复用已有年龄边界、缺失消息、状态 ID、派发前复核和零派发测试。新增每档等于阈值允许、超阈值拒绝，以及只有低频着陆消息超过旧 1000 ms 而其余消息有效时的新分级判定；同时验证位置超过 `FAST` 必须拒绝，最新位置不能掩盖其他必需报文失效。

验证参数等待后取得的新状态参与完整检查；读取状态期间参数证据过期仍拒绝，本地检查跨过位置期限也须由末次复核拒绝。旧记录沿用旧规则、部分新字段拒绝、非法或未知等级不回退。关键安全边界用可控错误确认测试能发现漏检并恢复。SITL 与固定集合复跑的实际范围以 M6 阶段记录为准，不以单测代替完整功能验收。

### 风险与回滚

位置档比现行统一上限严格，着陆档比现行上限宽松，均可能改变允许/拒绝结果；需记录实测间隔和拒绝原因，不能以消除报错作为唯一依据。各档边界只表达本地接收年龄，不保证源数据从采样到接收没有延迟。

回滚必须同步撤回新模型、聚合器、Safety 判定及对应测试，恢复当前统一时效策略；新格式 Trace 使用匹配版本的模型读取，保留原始证据，不将回滚后的旧代码读取失败误报为数据损坏。
