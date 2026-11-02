# MX ROS-LLM 服务契约

本文件定义 MX-1 的 ROS 2 服务字段与跨节点语义。字段的可构建定义位于 [`rosllm_srvs`](../ros2_ws/src/rosllm_srvs/)；节点装配与调用顺序由 [SPEC_DEV.md 第 4 节](../SPEC_DEV.md)定义。服务类型存在不表示节点已实现或接入生产。

| 服务 | 请求 | 响应 | 失败与来源语义 |
|---|---|---|---|
| `Observation` | 空 | `bool success`, `string observation`, `string source`, `string message` | `success=false` 表示无法取得有效观察，`observation` 必须为空且 `message` 给出原因。成功时 `source` 必须标明真实状态或固定文本的来源；固定文本不能伪装成真实状态。 |
| `LLM` | `string prompt` | `bool success`, `string response`, `string info` | `success=false` 时 `response` 必须为空，`info` 给出原因，调用方不得派发行为。显式开发配置产生的固定回复必须在 `info` 标明来源。 |
| `ExecuteBehavior` | `string behavior` | `bool success`, `string message`, `int32 info` | `success` 表示序列执行结果，`message` 是面向人的摘要。`info` 保留上游信息标志字段；MX-1 不定义其数值枚举，调用方不得把它当成飞行诊断或 Safety 决策。 |
| `AtomicAction` | `string input`, `string prev_output` | `bool success`, `string output` | 具体动作由服务名选择。`input` 是动作参数文本，`prev_output` 是上一项输出；失败时 `output` 说明原因。飞行动作的成功只能由内部 Gate 和执行结果决定。 |

## MX-4 飞行动作服务契约

### 请求

- 服务名为 `takeoff`、`goto`、`hold`、`rtl`、`land`；服务名决定 Skill，不从 `input` 读取 Skill 名。
- `input` 必须是单个 JSON 对象的文本。除下表注明外，字段和值按现有 [Skill 参数模型](../src/flight_agent/contracts/models/skill_model.py)校验；未知字段失败。
- `prev_output` 是上一个动作的原始输出文本，只保留传递事实，不解析为飞行参数或授权。
- `input` 不得指定安全限制、Authority、状态 ID、Proposal/Execution ID 或超时。

| 服务 | `input` 示例 | 参数要求 |
|---|---|---|
| `takeoff` | `{"target_altitude_m":10.0}` | `target_altitude_m`：正数，米 |
| `goto` | `{"north_m":1.0,"east_m":2.0,"altitude_m":10.0,"acceptance_radius_m":1.0}` | 四个字段必填；高度和接受半径为正数，单位米；位置使用局部 NED 坐标 |
| `hold` | `{"duration_s":1.0}` | MX-4 服务要求 `duration_s` 为正数秒；现有 `HoldArgs` 允许省略或传 `null`，服务入口须额外拒绝这两种输入 |
| `rtl` | `{}` | 无参数 |
| `land` | `{}` | 无参数 |

### 响应

- `success=true` 当且仅当 Safety 决策为 `APPROVE`，且 `SkillResult.status=SUCCEEDED`。
- `output` 始终是 JSON 对象文本，包含下表字段；未产生的 ID、状态和失败码为 `null`，未产生 Safety 决策时 `reason_codes=[]`。
- `verification` 固定为 `INCONCLUSIVE`；MX-4 不把 Backend 基础终态称为 M6 完整状态验证。

| `output` 字段 | 类型 | 含义 |
|---|---|---|
| `source` | string | 固定为 `flight_atomic_action_server` |
| `proposal_id`, `decision_id`, `execution_id` | string 或 null | 已产生的追踪 ID |
| `reason_codes` | string 数组 | Safety 决策的原始原因码 |
| `execution_status` | string 或 null | `SkillResult.status` 的原始值 |
| `failure_code` | string 或 null | `SkillResult.failure_code` 的原始值 |
| `error_code` | string 或 null | 服务入口或调用异常码 |
| `verification` | string | 固定为 `INCONCLUSIVE` |

执行 ID 的生成顺序见 [Safety 契约](safety_contract.md#safety-decision)。拒绝和 Trace 写入失败时 `execution_id=null`；正常返回的执行结果提供其执行 ID。

### 失败语义

| 条件 | `success` | `error_code` | Safety/执行字段 |
|---|---|---|---|
| JSON 无法解析、解析结果不是对象 | `false` | `INVALID_INPUT` | 无决策、无执行 |
| `hold` 缺少有效正数 `duration_s` | `false` | `INVALID_INPUT` | 无决策、无执行 |
| 其余参数违反 Skill 模型 | `false` | `null` | Gate 决策包含 `INVALID_SCHEMA` 原因码，可同时含其他检查原因；写入 Trace，不执行 |
| Safety 拒绝或要求人工批准 | `false` | `null` | 返回原始决策及原因码，不执行 |
| Backend 返回非 `SUCCEEDED` 的 `SkillResult` | `false` | `null` | 返回执行状态与失败码 |
| 状态读取、Trace 写入或 Backend 调用抛出异常 | `false` | `SERVICE_ERROR` | 只返回服务已取得的字段，未知字段为 null；Trace 写入失败时不得派发 |

生产 Authority 固定为 `UNKNOWN`；批准与执行结果只在测试注入的 Authority 和 Backend 替身下验证。实际接线与调用流见 [MX-4 变更单](../plans/MX-4-structure-change.md#调用流)。

通用服务字段说明：

- 目标服务名为 `get_observation`、`call_llm`、`execute_behavior`；进度见 [MX 计划](../plans/MX.md)。
- `Observation` 的文本和来源字段替代上游 `rosllm_msgs/Observation[]`。
- `LLM` 只保留当前主链所需的成功、回复和信息字段，暂不传请求编号与时间戳。
- V1 不处理图像观察，也不创建无实际使用者的 `rosllm_msgs` 包。
- `ExecuteBehavior` 和 `AtomicAction` 保持上游字段名称和类型。

上游字段依据：[Observation.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/Observation.srv)、[LLM.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/LLM.srv)、[ExecuteBehavior.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/ExecuteBehavior.srv)、[AtomicAction.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/AtomicAction.srv)。正式上游对照仍须绑定准确 commit。
