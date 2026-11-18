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

## M6 T03a 拍照服务（草案，待确认）

### 现状

现有 `AtomicAction` 已提供 `input`、`prev_output`、`success` 和 `output`；`SequenceExecutor` 会将前一项的 `output` 原样交给下一项，并在失败时停止。`capture_photo` 尚无服务实现，也未列入动作白名单。T03b 实现可直接调用的服务，T03c 才接入 Agent；本节不将这些目标写成已有能力。

### 目标

调用方请求拍一张照片时，服务取得符合 [T02b 要求](#m6-t02b-采集有效性判据)的新图，连同相机参数、时间和实际飞机位置保存到磁盘，返回后续检测服务可读取的文件引用。

### 约束

复用 `rosllm_srvs/srv/AtomicAction`，不修改 `.srv`。沿用冻结相机和 T02b 的时间要求，不增加图像识别、三维定位、飞行指令、自动重试或 Observation 图像输入。

### 请求

服务名为 `capture_photo`，`input` 必须是 JSON 空对象文本 `"{}"`；非法 JSON、其他 JSON 类型及非空对象均失败。`prev_output` 保留执行器传递事实，拍照服务不解析它，不用上一动作的目标坐标代替实测位置。

调用方不能指定相机、文件路径、图片 ID、时间戳、新鲜度或等待时长。输出目录由操作员启动服务时设置，见下文运行方式。

### 成功响应

`success=true` 表示照片和配套 JSON 均已保存、能重新读取且内容对应。`output` 为 JSON 对象文本，字段如下；此格式同样用于失败响应。

| 字段 | 类型 | 成功时的内容 |
|---|---|---|
| `source` | string | 固定为 `capture_photo_server` |
| `photo_id` | string 或 null | 服务为本次拍照生成的 UUID，不是任务 ID |
| `image_path` | string 或 null | 已保存 PNG 的绝对路径 |
| `metadata_path` | string 或 null | 配套 JSON 的绝对路径 |
| `sha256` | string 或 null | 已保存 PNG 文件的 SHA-256，用于后续读取时核对文件 |
| `error_code` | string 或 null | 成功为 null，失败见下表 |
| `message` | string | 面向人的结果说明；程序不得靠解析此文本判断结果 |

下一项 `inspect_image` 从紧邻前项的 `prev_output` 获取这些字段，实际读取并核对图片；具体检测请求留到 T04a。引用只包含小型 JSON，不通过服务返回图片字节或模型预填的图片 ID。

### 保存内容

操作员指定的输出目录中，每次成功拍照生成独立子目录 `<photo_id>/`，包含 `image.png` 与 `metadata.json`。PNG 由选定 Image 的 RGB 像素无损编码；不裁剪、缩放或标注。

配套 JSON 保存下列信息，供操作员和后续检测程序核对来源；字段定义在本节维护，不在阶段记录重复。

| 字段 | 保存的内容 |
|---|---|
| `schema_version` | 整数 1，用于读取方识别格式 |
| `photo_id`、`image_path`、`sha256` | 与服务响应相同 |
| `image` | 源话题、`stamp_ns`、`frame_id`、`width`、`height`、`encoding`、`step` |
| `camera_info` | 源话题、原始 CameraInfo 全部字段；数组转为 JSON 数组 |
| `timing` | `clock_ns`；`request_monotonic_ns`、`image_received_monotonic_ns`、`selected_monotonic_ns`、`position_received_monotonic_ns`；`process_id` 为本次服务进程启动时生成的 UUID |
| `vehicle_position` | 下文规定的 PX4 来源、原始时间、局部位置、局部原点参考及重置次数 |

`image.stamp_ns`、CameraInfo 时间和 `timing.clock_ns` 使用 Gazebo 仿真时间；单调时间仅在同一个 `process_id` 内比较，不跨进程比较，也不与仿真时间或 PX4 源时间直接相减。图片的源时间戳是采集时间来源，不用保存时间覆盖。

### 飞机位置

拍照节点直接订阅 `/fmu/out/vehicle_local_position` 的 `px4_msgs/msg/VehicleLocalPosition`，使用 best-effort QoS。选定图片时，取此前最近收到的位置消息；其本地接收时间距选图时刻不得超过 0.5 秒，`xy_valid`、`z_valid` 必须为真，`x/y/z` 必须为有限值。缺失、无效或过期时本次失败，不继续保存。

`vehicle_position` 保存 `source_topic`、`source_type`，以及原始消息的 `timestamp`、`timestamp_sample`、`x/y/z`、`xy_valid/z_valid`、`ref_timestamp/ref_lat/ref_lon/ref_alt`、`xy_global/z_global`、`xy_reset_counter/z_reset_counter`。局部位置单位为米，采用 PX4 NED 坐标；无效的全局参考数字以 null 保存，不将其当作可用 GPS 坐标。

该记录只说明“选定图片时最近收到的飞机位置”。没有完成 PX4 与相机时间同步、曝光时刻插值或相机外参转换，不能称为精确曝光位置或目标位置。不得使用场景真值、模型输入或 `prev_output` 中的坐标填充它。

现有 `WorldState` 的 `source_timestamp_us` 是多个 Topic 的最大时间戳，并非位置消息独有的采样时间；本服务为保留原始位置时间直接订阅该 Topic，不修改现有状态聚合器或飞行 Safety。

### 保存过程和失败

服务先取得新图和对应参数，按上节要求检查位置，再生成 UUID，在输出目录下的临时子目录写入两份文件。文件关闭后重新读取，确认 PNG 可解码且像素对应，JSON 可解析且引用、时间与哈希一致；最后在同一文件系统将临时目录重命名为 `<photo_id>/`，之后才能返回成功。不覆盖已有文件，不生成通用媒体注册服务。

写入结束前若已观察到时钟回退，按 T02b 终止本次拍照，不返回可用引用。异常时尽力清理本次临时文件；清理失败写入服务日志，不把残留文件报告为成功。操作员只能将成功响应返回的引用用于后续步骤，不扫描临时目录推断成功。

T02b 的 5 秒只限制等待新图的时间。磁盘写入不承诺硬实时截止；执行器现有调用超时仍有效，超时后不自动重试。V1 没有取消协议，调用方超时不代表已取消服务端写盘；可能存在未交付的文件，不能据此继续派发下一动作。成功不承诺断电后的持久性。

失败时 `success=false`，响应中的 `photo_id/image_path/metadata_path/sha256` 均为 null，`error_code` 非空，`message` 解释原因。检查顺序为输入、服务忙、时钟/重置状态、等待图片、位置、保存；先失败的步骤决定错误。

| `error_code` | 条件 |
|---|---|
| `INVALID_INPUT` | 请求不符合上文格式 |
| `BUSY` | 另一次拍照正在等待或保存；不排队、不覆盖该请求的缓存与文件 |
| `CLOCK_UNAVAILABLE` | 请求时未取得 T02b 要求的有效时钟/基线 |
| `CLOCK_RESET` | 本进程已观察到时钟回退；按 T02b 要求等待操作员重启 |
| `FRAME_TIMEOUT` | T02b 等待期限内没有符合要求的图像和参数对 |
| `POSITION_UNAVAILABLE` | 选图时位置缺失、过期、无效或坐标非有限值 |
| `SAVE_FAILED` | 编码、写入、重新读取核对或最终重命名失败 |
| `SERVICE_ERROR` | 未归入上述条件的内部异常；不返回部分成功 |

运行中时钟停止前进但没有回退时，图像不再通过 T02b 检查；请求到期返回 `FRAME_TIMEOUT`。发生并发请求时允许第二个请求立即返回 `BUSY`，同时必须继续处理相机与时钟订阅，不能由等待中的服务回调阻塞这些消息。

### 运行方式和对象职责

T03b 拟增加入口 `ros2 run flight_agent_ros capture_photo_server --output-dir <目录>`，默认目录为启动工作目录下的 `artifacts/photos`。启动时解析成绝对路径并检查可创建、可写，失败则非零退出且不发布服务；运行中写入仍可能失败，按 `SAVE_FAILED` 返回。输出目录只由操作员配置，不由模型控制。

入口创建一个拍照节点，节点持有 Image、CameraInfo、Clock、位置订阅，有限大小的消息缓存和单次拍照状态；收到请求后调用正式业务函数检查图片并保存。不提前建立第二套任务调度器、通用媒体平台或独立校验节点。

T03b 需要在 ROS 包声明 `sensor_msgs`、`rosgraph_msgs` 和 PNG 编码依赖 `python3-pil`，复用已有 `rclpy`、`px4_msgs`、`rosllm_srvs`；不修改核心 Python 包依赖。入口显式使用仿真时间。所有业务源码及调用它的测试纳入 Git，原始图片和大体积运行日志保留在 `artifacts/`。

拟议调用过程，尚未接入：

```text
[操作员或后续执行器] --AtomicActionRequest(input="{}", prev_output=前项输出)--> [拍照节点]
[Gazebo 相机桥接] --Image、CameraInfo、Clock--> [拍照节点]
[PX4 ROS 2 输出] --VehicleLocalPosition--> [拍照节点]
[拍照节点] --PNG、配套 JSON--> [本地输出目录]
[拍照节点] --AtomicActionResponse(success, output=文件引用或错误)--> [操作员或后续执行器]
```

### 验收

| 场景 | T03b 必须提供的证据 |
|---|---|
| 真实地面拍照 | 从正式入口启动，通过 ROS 服务调用取得成功响应；重新读取 PNG 和 JSON，核对像素、源时间、相机参数、实际 PX4 位置来源、文件哈希与返回引用。不能只直接调用保存函数。 |
| 旧图、无图与参数不匹配 | 让正式业务函数和服务拒绝不符合 T02b 的数据；明确区分构造的消息输入与真实相机断流，不再用另一份测试专用判断函数代替业务代码。 |
| 暂停与重置 | 正式服务在时限内失败；已观察到回退后缓存作废、后续请求拒绝，操作员按 T02b 的重启方式恢复。替身或构造时钟只证明对应边界。 |
| 无效位置 | 缺失、过期、无效坐标不能成功；保存的坐标不能取自请求或前项输出。 |
| 保存失败与并发 | 目录不可用、写入/核对/最终重命名失败时无成功引用；并发请求不串图、不覆盖，忙时明确失败。故障注入须说明替换范围。 |
| 重复拍照 | 同一进程连续拍两张，取得各自请求后的图像、不同 UUID 和独立文件；先前文件内容保持不变。 |

T03c 才验证 Agent 能选择拍照动作；T04a/T04b 才定义并实现检测服务，T04d 才交付返航后仍能查看的逐项结果。本批不把这些能力计为拍照服务已经完成。
