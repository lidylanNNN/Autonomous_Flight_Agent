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
- 动作完成职责见 [执行结果契约](skill_contract.md#执行结果)。当前响应仍保留固定为 `INCONCLUSIVE` 的 `verification` 占位字段；它不参与 `success` 判断，也不代表存在独立 Verification 节点。

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
| Safety 拒绝 | `false` | `null` | 返回原始决策及原因码，不执行 |
| Backend 返回非 `SUCCEEDED` 的 `SkillResult` | `false` | `null` | 返回执行状态与失败码 |
| 状态读取、Trace 写入或 Backend 调用抛出异常 | `false` | `SERVICE_ERROR` | 只返回服务已取得的字段，未知字段为 null；Trace 写入失败时不得派发 |

生产 Authority 来源及 V1 授权假设以 [SPEC 第 5 节](../SPEC_DEV.md#5-安全不变量)为准；受控 SITL 的实际调用证据见 [M5 收口审查](../plans/M5.md#4c-收口审查)。

通用服务字段说明：

- 目标服务名为 `get_observation`、`call_llm`、`execute_behavior`；进度见 [MX 计划](../plans/MX.md)。
- `Observation` 的文本和来源字段替代上游 `rosllm_msgs/Observation[]`。
- `LLM` 只保留当前主链所需的成功、回复和信息字段，暂不传请求编号与时间戳。
- V1 不处理图像观察，也不创建无实际使用者的 `rosllm_msgs` 包。
- `ExecuteBehavior` 和 `AtomicAction` 保持上游字段名称和类型。

上游字段依据：[Observation.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/Observation.srv)、[LLM.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/LLM.srv)、[ExecuteBehavior.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/ExecuteBehavior.srv)、[AtomicAction.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/AtomicAction.srv)。正式上游对照仍须绑定准确 commit。

## M6 T02b 采集有效性判据

状态：2026-11-18 用户已确认；作为采集实现约束，尚未接入生产服务。

### 现状

T02a 已验证真实 Image、CameraInfo 与 `/clock` 接通及内容对应，证据见 [M6 T02a 记录](../plans/M6.md#t02a-恢复实施图像桥接2026-11-17)。正式采集服务、有效帧缓存和下述拒绝行为尚未实现；本节作为 T03a/T03b 的输入，不代表现有桥接程序已提供这些保证。

### 目标

供操作员发起单次拍照时使用，防止把请求前缓存、过期图像或重置前数据作为本次有效采集。成功采集的保存与媒体引用语义由 T03a 定义，本节只定义候选帧是否有效。

### 约束

仅适用于 T02a 冻结的单相机、单发布者和仿真时间模式；话题、方向、QoS 仍以桥接 YAML 为准。不向 Observation 传图，不增加服务字段，不增加飞行或自动恢复行为。

### 功能要求

| 编号 | 要求 |
|---|---|
| C1 请求基线 | 接收请求时记录本地单调时间、最新有效仿真时间及此前最高图像时间戳。不得立即返回已有缓存；候选图像须在请求后接收，且源时间戳严格大于请求时的仿真时间与此前最高图像时间戳。基线未就绪时本次失败，不改用墙钟。 |
| C2 时钟有效 | 已收到非零 `/clock`，最近一次严格前进距当前本地单调时间不超过 1 秒。图像时间戳必须为正，且满足 `0 <= 最新仿真时间 - 图像时间戳 <= 0.5 秒`。图像先于对应时钟到达时可以等待，不能立即判有效。 |
| C3 接收新鲜度 | 作出候选帧有效判断时，距图像接收的本地单调时间不超过 0.5 秒。源时间戳和本地接收时间分别记录，不跨时钟相减；新收到的旧图不能只凭接收时间通过。 |
| C4 图像与参数 | Image 与 CameraInfo 的源时间戳完全相同，非空 frame_id 和宽高一致。冻结模式只接受 1280×960、rgb8、每行 3840 字节且数据长度完整的图像；相机参数值有限，K/P 的焦距为正且 K[8]=1。不借用另一时间戳的参数。 |
| C5 重置失效 | 任意一次已观察到的 `/clock` 回退立即清除图像/参数缓存，终止进行中的采集，并禁用后续采集，直到操作员在稳定的新仿真运行中重启采集进程。禁止在同一进程内自动恢复，避免遗留消息重新污染缓存。 |
| C6 有界等待 | 从请求接收起最多等待 5 秒本地单调时间，在此期限内选择最早满足 C1–C5 的图像/参数对。无图、旧图、重复帧、参数缺失/不匹配和无效像素布局均不得产生有效候选；到期明确失败，不延长截止时间，不回退到旧缓存。 |

上述数值是本项目已确认的功能默认值，不是 ROS/Gazebo 的保证或实测性能结论；后续仅能通过另行评审修改，模型输入不得覆盖这些值。

### 边界

Image/CameraInfo 不含仿真运行标识，仅靠时间戳不能识别所有重启或延迟跨运行消息。操作员重启/重置世界时必须停止旧桥接和采集进程，在新世界稳定后重新启动；不得在飞行中执行世界/时钟重置。C5 只保证对已观察到的回退作出拒绝，不宣称能检测未观察到的重启。

源 frame_id 沿用 T02a 转换结果；一致性检查不证明它符合 optical frame 约定。本节不提供目标三维位置、飞行位置关联或落盘成功保证。

### 验收

| 场景 | 预期判定及新增证据 |
|---|---|
| 正常真实消息 | 在真实 T02a 入口采集请求前后消息，以原始消息、源/接收时间与判定记录核对 C1–C4；复用已有原生像素对应证据，不重复将桥接连通性算作新增能力。 |
| 缓存与时限 | 请求前缓存、请求后才送达的旧时间戳、重复帧、未来帧、无图和参数错配均不构成有效候选；期限后失败。覆盖此前 T02a 未判断的本次采集边界。 |
| 阈值 | 核对 0.5 秒与 1 秒边界等于阈值时允许、超出时拒绝；请求基线时间戳相等时拒绝；5 秒截止时及之后不成功。 |
| 时间停止与重置 | 暂停/断流后不得无限等待；回退后缓存清空、当前请求失败，恢复发布也不自动解禁。实际重启按操作员生命周期验证，不在飞行中重置。 |

T02b 可用真实消息记录及明确标注的边界输入检查判据的可执行性；测试专用判定器通过不证明生产缓存或服务拒绝已实现。T03b 必须通过实际采集入口重复验证上述服务行为。

### 后续接入

C1–C6、时间阈值及重置后必须重启进程的限制已确认。请求参数、媒体输出、错误编码与位置来源留给 T03a，不在本批预定义。
