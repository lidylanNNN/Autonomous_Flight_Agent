# MX-4 结构变更单：飞行原子动作接线

状态：步骤 1、2、3a、3b 已实施；3c 待实施。生产入口已发布 `hold`，其余飞行动作尚未接入。

依据：[当前规格](../SPEC_DEV.md)、[MX 计划](MX.md)、[ROS-LLM 服务契约](../contracts/rosllm_services.md)、[Safety Contract](../contracts/safety_contract.md)。

## 现状

- `sequence_executor` 已按动作名调用 `AtomicAction` 服务；生产入口已发布 `hold`，其余动作尚未接入。MX-3 的动作成功证据来自测试替身。
- 旧 `MissionService` 已调用 `SafetyExecutionGate`。Gate 写入决策 Trace 后，仅对批准的命令调用 Backend。
- 生产 Authority 目前为 `UNKNOWN`；旧受控任务入口不能获批飞行。
- `ExecuteBehavior` 请求只有行为文本；`AtomicAction` 请求只有 `input` 和 `prev_output`。两者都没有任务 ID 或版本字段。
- Safety 动作提案、Gate、Supervisor 和 Schema 已移除旧任务身份；旧任务模型与 CLI 仍待步骤 6 清理。
- 旧 `mission_cli` 从 JSON 加载 `MissionContract`；新 `ros_agent_srv` 主链不使用它。
- 系统限制示例 JSON 和加载函数已接入 `flight_atomic_action_server` 入口；无效文件在发布 `hold` 前失败。
- 当前入口为 `ros2 run flight_agent_ros flight_atomic_action_server --safety-json <JSON 文件路径> [--trace-path <路径>]`，仅发布 `hold`。
- `sequence_executor` 默认单项等待 30 秒；Skill Registry 中 `rtl` 的 Backend 超时为 120 秒。
- 显式 MX 固定行为把 `hold`、`rtl` 输入写成普通词，不符合飞行 Skill 参数格式。

上游依据：

- [ROS-LLM 主循环](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/agent_comm/scripts/ros_agent_srv)在每轮行为完成后才读取下一条人工输入。
- [论文 §3.3](https://arxiv.org/html/2406.19741v3)把首条输入当任务描述，把后续输入当反馈。
- 核对的主循环与 [AtomicAction 服务](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/AtomicAction.srv)没有任务会话、任务 ID 或放弃接口。

## 目标

- 在 `flight_agent_ros` 提供 `takeoff`、`goto`、`hold`、`rtl`、`land` 五个生产 `AtomicAction` 服务。
- 飞行动作经现有 Gate、Supervisor、Safety Trace 和 PX4 Backend 返回结果，不绕过批准边界。
- 有人主循环继续按 ROS-LLM 的一轮一行为方式运行。V1 不使用 `mission_id`、任务会话、任务版本或逐任务限制。
- MX-4 贯通一轮人工输入、行为执行、动作结果和再观察；本轮不实现动作取消。
- MX-4 生产路径维持拒绝态；批准路径只用测试注入与 Backend 替身验证。

## 约束

- 飞行安全限制从新的 JSON 文件加载，文件根对象使用 `MissionConstraints` 字段；启动时校验并固定在服务进程内。缺少、无法读取或校验失败时不发布飞行动作服务。
- JSON 文件不包含任务 ID、任务目标、完成条件或逐任务限制；`AtomicAction` 请求不能修改已加载的安全限制。
- LLM 行为与 `AtomicAction.input` 不能设置或放宽安全限制，也不能设置 Authority、状态 ID、执行 ID、Skill 名或超时。
- Control Authority 独立于人工文本。本期不得提供可从请求、ROS 参数或环境变量切换为 `AGENT_ALLOWED` 的生产开关。
- 删除任务 ID 检查不得削弱其他 Safety 检查：参数、状态新鲜度、状态 ID、Authority、围栏、包线和健康仍须逐项生效。
- Trace 写入失败时不得派发。Backend 基础终态不等于 M6 完整状态验证；替身测试不等于 PX4/Gazebo 端到端证据。
- `SequenceExecutor` 单项超时后停止后续动作。本轮不增加取消接口，也不以调用方超时推断 Backend 已停止。

## 拟议设计

### 飞行动作服务

- `flight_atomic_action_server.main()` 创建并持有 `FlightAtomicActionServer`、Gate、Backend 与 ROS 适配器。
- ROS 服务名决定 Skill。服务端读取当前 PX4 `WorldState`，分配唯一 Proposal/Execution ID，并串行处理在途飞行动作。
- 生产入口从 `--safety-json` 指定的新 JSON 文件加载系统限制；装配函数接收已校验的 `MissionConstraints`、Trace 路径，以及可注入的 Backend、Authority reader 和速度源。生产入口只装配真实 Backend 与 `UNKNOWN` Authority。
- 回调输入为 `AtomicAction.Request(input, prev_output)`；`input` 是单个 JSON 对象的文本，字段由现有 Skill 参数模型定义。
- `prev_output` 仅保留上游传递事实，不解析为飞行目标或授权。
- 非法 JSON、非对象或 `hold` 缺少有效时长时，服务入口直接失败；其余非法参数交由 Gate 产生含 `INVALID_SCHEMA` 的决策并写 Trace。
- 服务端用系统 `MissionConstraints` 和当前状态构造不含任务 ID 的 `AtomicActionProposal`，再调用已迁移的 `SafetyExecutionGate.submit()`。
- `SafetyDecision=APPROVE` 且 `SkillResult.status=SUCCEEDED` 时，`AtomicAction.Response.success` 才为 `true`。
- 拒绝、执行失败或异常返回失败；`output` 字段与失败码见 [飞行动作服务契约](../contracts/rosllm_services.md#mx-4-飞行动作服务契约)。

输入输出字段与失败语义以 [服务契约](../contracts/rosllm_services.md) 为准。

### 调用流

目标飞行动作调用流；有人主循环的其余边见 [当前规格第 4 节](../SPEC_DEV.md)：

```text
[安全 JSON] --MissionConstraints--> [flight_atomic_action_server.main]
[flight_atomic_action_server.main] --已校验的 MissionConstraints--> [FlightAtomicActionServer]
[sequence_executor] --AtomicActionRequest(input, prev_output)--> [FlightAtomicActionServer]
[FlightAtomicActionServer] --AtomicActionProposal + MissionConstraints + execution_id--> [SafetyExecutionGate]
[SafetyExecutionGate] --get_world_state()--> [PX4 Backend]
[PX4 Backend] --WorldState--> [SafetyExecutionGate]
[Authority reader] --ControlAuthority--> [SafetyExecutionGate]
[SafetyExecutionGate] --AtomicActionProposal + MissionConstraints + WorldState + ControlAuthority--> [SafetySupervisor]
[SafetySupervisor] --SafetyDecision + ApprovedSkillCommand(仅批准时)--> [SafetyExecutionGate]
[SafetyExecutionGate] --AtomicActionProposal + SafetyDecision--> [TraceRecorder]
[SafetyExecutionGate] --ApprovedSkillCommand(仅批准时)--> [PX4 Backend]
[PX4 Backend] --SkillResult--> [SafetyExecutionGate]
[SafetyExecutionGate] --SafetyDecision + SkillResult(拒绝时为 None)--> [FlightAtomicActionServer]
[FlightAtomicActionServer] --AtomicActionResponse(success, output)--> [sequence_executor]
```

## 实施任务

保留六个实施步骤。每个子项完成后展示本批差异和验证结果，供单独审阅；下一子项不混入当前批。

1. **迁移 Safety 契约**
   - 1a. **已完成**：核对 Proposal、Schema、Supervisor、Gate、旧调用方和 Trace 中的任务 ID 引用，冻结本批删除范围。
   - 1b. **已完成**：同一代码批次移除 Proposal、Schema、Supervisor、Gate 的任务 ID 字段与匹配参数，并同步旧调用方、Trace 回放及直接依赖该字段的测试。
2. **接入系统限制 JSON**
   - 2a. **已完成**：新建仅含 `MissionConstraints` 根字段的示例 JSON，并用模型解析测试验证字段和值。
   - 2b. **已完成**：增加独立加载函数及测试，覆盖缺少文件、读取失败、未知字段和无效限制；ROS 服务装配留到步骤 3。
3. **接入飞行动作**
   - 3a. **已完成**：在 `contracts/rosllm_services.md` 冻结飞行动作的 JSON 参数、响应字段与失败语义。
   - 3b. **已完成**：新增 `flight_atomic_action_server` 入口和装配，先只接 `hold`；从 JSON 加载限制，经真实 ROS 服务进入 Gate，生产 Authority 固定 `UNKNOWN`。
   - 3c. 调整执行身份：Gate 写入获批决策 Trace 后才分配 `execution_id`，创建绑定 `decision_id` 的命令；拒绝和 Trace 失败不分配。同步 Supervisor、Gate、飞行服务、旧调用方及契约。
   - 3d. 在同一服务端逐个增加 `takeoff`、`goto`、`rtl`、`land`；每个动作单独一批。
   - 3e. 从真实动作服务验证拒绝、Trace 和 Backend 零调用；批准与执行失败只用测试注入的 Authority 和 Backend 替身验证。
4. **修正固定行为参数**
   - 将 `agent_comm.llm_node` 显式 MX 模式中的 `hold` 输入改为 `{"duration_s":1.0}`、`rtl` 输入改为 `{}`；同步入口测试。这一步单独一批。
5. **协调执行器超时**
   - 调高 `sequence_executor` 单项等待上限，覆盖现有最长 Skill 超时及 Gate 前置读取余量；保留超时失败即停。这一步单独一批，不加入取消协议。
6. **删除旧任务模型（迁移后实施）**
   - 6a. 新主链验证通过后，删除旧 `mission_cli`、`MissionService` 和专属测试；保留 M3 独立调试入口。
   - 6b. 删除 `MissionContract`、旧任务级模型及公共导出；复核 `PlanStep` 和 `VerificationResult` 的实际使用者，保留 `MissionConstraints`。

步骤 1 至 3、6 涉及 Contract、Safety 或 ROS 2/PX4；每个相关代码批次按 [AGENT.md](../AGENT.md) 汇报范围、调用流、影响和回滚。步骤 4、5 不混入飞行动作批次。

## 验证

- 步骤 1：运行 Safety 契约、Supervisor、Gate、旧受控入口和 Trace 回归；验证非法参数、状态、Authority、围栏、包线与健康检查仍生效。
- 步骤 2：验证合法 JSON 加载；缺少、不可读、未知字段及无效限制均失败。
- 步骤 3a：核对 `.srv` 现有字段和文档中的请求、响应、失败字段一致。
- 步骤 3b：构建 ROS 包；从真实 `hold` 服务进入 Gate，`UNKNOWN` 时 Trace 记录 `AUTHORITY_NOT_GRANTED` 且 Backend 零调用；缺少有效安全 JSON 时不发布服务。
- 步骤 3c：验证批准且 Trace 成功后才分配 `execution_id`；拒绝和 Trace 失败不分配、不派发，拒绝响应的 `execution_id=null`。运行 Safety、旧调用方和 ROS 服务回归。
- 步骤 3d：每个新增动作分别验证合法参数、非法参数、拒绝响应及测试注入的批准/执行失败响应；外部 Backend 使用替身。
- 步骤 3e：验证 Trace 写入失败时不派发、执行失败返回失败响应。
- 步骤 4：验证固定 `hold` 和 `rtl` 文本符合 Skill 参数模型。
- 步骤 5：验证长于旧 30 秒的合法等待边界，以及单项超时后不派发下一项。
- 步骤 1 至 5 完成后：从生产装配路径验证一轮人工输入、动作执行、结果返回和再观察；标明替身 Backend 与固定 LLM 的边界。
- 步骤 6：逐批检查无旧入口和旧任务模型引用，运行主链与 Safety 回归。
- 每批执行 `git diff --check`，并记录本批实际调用流、未接入组件和回滚点。

## 1a 核对记录（迁移前）

- `AtomicActionProposal` 的 `mission_id`、`plan_revision`、`step_id` 都来自旧任务计划。Safety 检查不使用后两个字段；单次动作已有 `proposal_id`。1b 范围包含这三个字段。
- `check_proposal_schema()` 比较 Proposal 的 `mission_id` 与 `expected_mission_id`；`SafetySupervisor.evaluate()` 将后者加入决策 ID 哈希；`SafetyExecutionGate.submit()` 继续传递该参数。1b 范围包含这条匹配链。
- 旧 `MissionService` 是当前生产调用方。1b 范围只涉及它构造 Proposal 和调用 Gate 的方式；`MissionContract`、`MissionRequest`、`MissionPlan`、`MissionRunResult` 及旧 CLI 留到步骤 6。
- `TraceRecorder` 序列化整个 Proposal，`TraceReplay` 严格解析 Proposal。Safety 决策模型本身没有任务 ID 字段，但决策 ID 哈希会改变。仓库没有已提交的 Safety JSONL 记录。
- 直接受影响的测试位于 `tests/contract/safety/`、`tests/unit/safety/`、`tests/unit/test_trace.py`、`tests/integration/test_mission_flow.py` 和 `ros2_ws/src/flight_agent_ros/test/test_mission_cli.py`。`agent_benchmark_sets/` 与 `eval_harness/` 未发现这些字段的引用。
- 1a 基线：运行 Safety 契约、单测、Trace 与旧受控入口集成测试，`137 passed`；ROS 包的 `test_mission_cli.py` 仍须在 1b 批次验证。

## 1b 实施记录

- Proposal 保留 `proposal_id`、`skill_name`、`arguments` 和 `based_on_state_id`。Schema、Supervisor 和 Gate 不再接收或校验 `expected_mission_id`；其他 Safety 检查及先写 Trace 再派发的顺序未改。
- 当前受控生产入口仍是旧 `mission_cli`；它调用的 `MissionService` 已适配新 Gate 签名。新飞行动作服务、系统 JSON 来源和生产获批路径尚未接入。
- 决策 ID 的哈希输入已移除任务 ID，旧版外部 Safety Trace 因 Proposal 严格字段校验无法直接回放。旧 `MissionService` 的 `proposal_id` 仍含旧任务标识；该入口将在步骤 6 删除，新服务须生成独立动作 ID。
- Safety 契约、单测、Trace 与旧入口集成测试为 `136 passed`；完整核心 `tests/` 为 `172 passed`。加载 ROS 2 Jazzy 和工作区后，旧 ROS CLI 测试为 `5 passed`。验证使用替身 Backend，不构成 PX4/Gazebo 端到端证据。

## 2a 实施记录

- 新增 [`configs/flight_safety.example.json`](../configs/flight_safety.example.json)。根对象只含 `MissionConstraints` 字段；高度、速度、半径、电量、状态年龄和局部 NED 围栏数值沿用现有测试构造。
- 示例尚未由生产入口加载，也不代表获批的运行限制。2b 负责加载函数与无效文件拒绝；步骤 3 才接入 `--safety-json` 服务入口。
- 2a 验证：Safety 契约测试 `14 passed`；示例 JSON 语法检查、Ruff 检查和 `git diff --check` 通过。

## 2b 实施记录

- `load_safety_constraints(path: Path)` 读取 UTF-8 JSON，返回 `MissionConstraints`。
- 读取或校验失败时抛出含路径的 `ValueError`；没有默认回退。
- 当前没有生产调用方；服务启动和 `--safety-json` 接线留在步骤 3。
- 加载测试覆盖有效文件和五类无效输入，`6 passed`。

## 3a 实施记录

- 飞行动作沿用 `AtomicAction.srv` 字段；五个服务以服务名选择 Skill，`input` 是单个 JSON 对象文本。
- 服务响应的 `output` 定义了固定 JSON 字段，以及输入失败、Safety 拒绝、执行失败和调用异常的返回方式。
- 无取消接口的 MX-4 服务要求 `hold.duration_s` 为正数；现有 `HoldArgs` 仍允许省略或传 `null`，3b 须在服务入口拒绝。
- 本批只修改服务契约和计划；生产飞行动作服务尚未建立。
- 3a 验证：`.srv` 请求与响应字段一致；五个 JSON 示例通过现有参数模型；`git diff --check` 通过。

## 3b 实施记录

- `flight_atomic_action_server.main()` 先加载 `--safety-json`，再装配 PX4 适配器、Backend、Gate 与 `hold` ROS 服务。
- `FlightAtomicActionServer` 持有已校验限制、Backend 和 Gate；服务回调校验有限时长，生成独立动作 ID，再提交 Gate。
- 生产 Authority 固定 `UNKNOWN`。真实 ROS 服务测试中，`hold` 请求获得拒绝响应和 Safety Trace，Backend 执行次数为零。
- 无效安全 JSON 在 ROS 初始化前失败。ROS 包构建通过；飞行服务、配置加载和旧 CLI 测试共 `13 passed`。

## 3b 补充：ACK 适配器跨请求复用

- 保留命令与 ACK 话题接口；空闲时允许适配器切换到本次调用的事件循环，并重建异步锁。
- 执行中和排队中的调用共同阻止跨循环切换；完成、超时或协程取消后释放占用。
- 无节点增删，公开接口不变；生产仍由 `hold` 的 `asyncio.run()` 创建每次请求的循环。
- 适配器测试覆盖跨循环成功、超时后复用、协程取消、锁竞争和忙碌时拒绝跨循环；连同飞行服务、配置和旧 CLI 回归共 `18 passed`。
- 跨循环测试直接调用适配器并从另一线程注入 ACK，不代表 PX4/SITL 实测；迟到 ACK 匹配问题仍未解决。

## 风险

- 调用方超时后，Backend 可能仍在执行。本轮仅验证生产 `UNKNOWN` 拒绝链和替身批准链，不以此作为真实飞行放行证据。
- 生产 Authority 来源尚不可信；MX-4 不能提供获批的真实飞行证据。

## 回滚

- 移除新飞行服务入口、实现及依赖，可回到 MX-3 测试动作替身状态。
- 固定行为与超时改动分别回滚；新安全 JSON 和加载入口随飞行动作服务回滚。
