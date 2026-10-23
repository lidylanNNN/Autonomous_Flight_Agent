# MX-4 结构变更单：飞行原子动作接线

状态：待确认；尚未修改业务代码、测试或运行配置。依据：[当前规格](../SPEC_DEV.md)、[MX 计划](MX.md)、[ROS-LLM 服务契约](../contracts/rosllm_services.md)、[Safety Contract](../contracts/safety_contract.md)。

## 现状与目标

`sequence_executor` 已按动作名调用 `AtomicAction` 服务，但仓库没有生产 `hold`、`rtl` 等飞行动作服务；MX-3 的成功证据来自测试动作替身。现有 `SafetyExecutionGate` 已在旧 `MissionService` 中使用，能够在 `SafetyDecision` 写入 Trace 后按批准结果调用 Backend。MX-4 在 `flight_agent_ros` 增加真实飞行服务装配，使五个飞行服务共用现有 Gate、Supervisor、PX4 Backend 和 Safety Trace。生产 Authority 仍为 `UNKNOWN`，所以正常运行只能拒绝，不能派发 PX4 命令；可信 Authority 的批准路径只用测试注入和 Backend 替身验证。

拟议生产入口为 `ros2 run flight_agent_ros flight_atomic_action_server --contract-json <MissionContract JSON 文件路径> [--trace-path <路径>]`；该命令尚未实现。这里的“冻结合同”是启动前由操作员提供、服务启动时按现有 `MissionContract` 模型校验并加载到内存的一份任务合同，不是 `agent_benchmark_sets/frozen_test` 测评集。它包含 `mission_id`、任务目标、`constraints`、完成与终止条件；“冻结”仅表示服务运行期间不接受动作请求修改这份合同，不表示已有审批或可信 Authority。服务用其 `mission_id` 作为 `expected_mission_id`、`constraints` 作为 Gate 约束。缺少或无效合同直接启动失败，不发布动作服务。`AtomicAction.Request` 不能设置或覆盖合同、Authority、任务 ID、状态 ID、执行 ID、Skill 名或超时。ROS 服务名决定 Skill，服务端读取当前 PX4 状态并创建唯一 Proposal/Execution ID。默认 Authority reader 固定返回 `UNKNOWN`，本期不增加可从 ROS 请求、参数或环境变量切换为 `AGENT_ALLOWED` 的生产开关。

仓库目前没有可直接传给该新入口的合同 JSON 文件，也没有 `ros_agent_srv` 向飞行服务建立或切换任务会话的机制。静态启动合同只为 MX-4 提供固定的可信任务 ID 与约束输入；一个服务进程只能绑定这一份合同，不能代表逐轮人工任务自动生成合同。真正的逐任务可信会话与 Authority 来源仍待后续设计和验证。

## 分批实施

1. **服务与 Gate 批次**：在 `flight_agent_ros` 建立 `FlightAtomicActionServer` 及可复用的装配函数，注册 `takeoff`、`goto`、`hold`、`rtl`、`land` 五个 `AtomicAction` 服务；更新该包的入口与依赖。每次请求将 `input` 解析为单个 JSON 对象，要求参数字段符合现有 Skill 模型；`prev_output` 仅作为上游透传事实记录，不参与飞行目标或放行决定。服务端从现有 `Px4WorldStateAdapter`/Backend 获取 `WorldState`，构造 `AtomicActionProposal`，调用 `SafetyExecutionGate.submit()`，把拒绝原因、执行状态或异常写入失败 `output`；只有 `SafetyDecision=APPROVE` 且 `SkillResult.status=SUCCEEDED` 才返回 `success=true`。保留决策与结果的机器字段，不能把 Backend 基础终态说成 M6 完整状态验证。Trace 写入失败时 Gate 不派发。一次仅处理一个在途飞行动作请求，避免并发批准后并发飞行。测试注入的 Authority、Backend 和速度源只在装配测试使用，不进入生产 CLI。
2. **固定行为参数批次**：将 `agent_comm.llm_node` 的显式 MX 固定行为改为合法参数文本，例如 `hold` 的 `{"duration_s":1.0}` 与 `rtl` 的 `{}`，并同步入口测试。默认 LLM 失败模式不变。此批只修复开发模式的动作输入，正常生产 Authority 仍拒绝首项。
3. **超时协调批次**：`behavior_executor.sequence_executor` 当前默认单项等待 30 秒，而 Skill Registry 的 `rtl` 超时是 120 秒。调高并验证调用方等待上限，使其覆盖当前最长 Skill 超时及 Gate 前置读取的余量；保留单项超时失败即停。此批只调整服务等待配置，不把调用方超时当作 Backend 已取消的证据。在途取消与真实飞行超时收口仍由后续 M4/M7 工作处理，未完成前不得开展获批的真实飞行验收。

以上三批分别评审和验证。批次 1 涉及 ROS 2/PX4、Safety 边界与接口，按 [AGENT.md](../AGENT.md) 先确认本单；批次 2、3 分别涉及 `agent_comm` 与 `behavior_executor`，不得混入批次 1。

## 接口、对象与数据流

新增 `FlightAtomicActionServer` 由进程 `main()` 创建并持有，收到五个服务中任一请求时调用；它持有同进程装配的 Backend、Gate、冻结合同和 ROS 适配器。新增装配函数接收冻结合同、Trace 路径及可注入的 Backend、Authority reader、速度源，返回服务端所需对象；生产 `main()` 只装配真实 PX4 Backend 和 `UNKNOWN` Authority。无新增核心数据模型或 `.srv` 字段。

飞行服务回调输入为 `AtomicAction.Request(input, prev_output)`，输出为 `AtomicAction.Response(success, output)`。`input` 使用 JSON 对象的文本形式，字段由现有 `TakeoffArgs`、`GoToArgs`、`HoldArgs`、`RTLArgs`、`LandArgs` 定义；非法 JSON 或非对象直接失败，合法 JSON 对象中的参数错误交由 Gate 形成 `INVALID_SCHEMA` 决策与 Trace，不创建命令。Gate `submit()` 的签名不变。`output` 用带稳定字段的 JSON 文本承载 `source`、`proposal_id`、`decision_id`、`reason_codes`、`execution_id`、`status`、`failure_code` 与 `verification=INCONCLUSIVE` 中已知的部分；失败或缺失字段不能伪造成成功。ROS 2 服务契约的这一动作输入输出约定将在代码批次前同步写入 `contracts/rosllm_services.md`。

数据流：`sequence_executor` -> `AtomicAction` 请求 -> `FlightAtomicActionServer`（新增）-> 冻结合同 + 当前 PX4 `WorldState` -> `AtomicActionProposal` -> `SafetyExecutionGate` -> `SafetySupervisor` -> `SafetyDecision` -> `TraceRecorder`；仅批准后 -> `Px4Ros2FlightExecutionBackend` -> `SkillResult` -> `AtomicAction.Response` -> `sequence_executor`。`prev_output` 本期不解析为位置或任务授权。参数格式批次改变 `llm_node` -> `sequence_executor` -> 动作服务的 `input` 文本；超时批次只改变 `sequence_executor` 等待动作响应的时限，无节点增删。

## 风险、回滚和验证

- **授权边界**：合同由进程启动者提供，不从 LLM 或 `AtomicAction` 请求提取；生产 Authority 保持 `UNKNOWN`。本期不证明可信 Authority 来源已完成，也不声称可在 SITL 获批执行。
- **状态与并发**：Gate 在派发前重新读状态，服务端不得用 Agent 观察文本充当当前状态；服务端串行处理飞行请求。现有 `SequenceExecutor` 在调用超时后无法证明 Backend 已取消，真实飞行验收须待取消和超时行为收口。
- **回滚**：移除新 ROS 入口、服务实现和依赖即可回到 MX-3 测试动作替身状态；固定行为和执行器超时配置分别回滚，不修改现有 Gate、Backend 或 `.srv` 文件。
- **验证**：构建 `flight_agent_ros` 与依赖；从真实 ROS `AtomicAction` 服务调用进入真实 Gate，验证 `UNKNOWN` 时 Trace 有 `AUTHORITY_NOT_GRANTED` 且 Backend 零调用；测试注入冻结合同、`AGENT_ALLOWED`、有效状态、合法 PX4 限速与 Backend 替身，验证批准、命令来源、Trace 和成功/失败响应；验证合同缺失、非法参数、Trace 写入失败、执行失败及并发请求。其后分别验证固定行为参数和执行器超时配置；运行已有 MX-3 与 Safety 回归，执行 `git diff --check`。替身证据不等于 PX4/Gazebo 端到端或 M6 完整验证。
