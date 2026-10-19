# MX — 主流程骨架贯通

状态：未开始

MX 是 M4 与 M5 之间的纵向集成阶段，不是新的任务决策循环。**调用链中的模块必须是 `SPEC_DEV.md` 第 3.1、4 节确定名称和职责的真实模块；占位只允许位于模块尚未开发的内部逻辑，不能用一个独立 Mock/Stub 模块顶替目标模块。**可用替身验证外部 PX4/LLM 依赖，但真实飞行动作的批准和 SITL 验收仍受 M4 可信 Authority 前置条件约束。MX 不以完成 LLM、视觉、M6 Verifier 或 M7-S SafetyMonitor 为前提。

## 入口与调用流

- **入口与触发**：目标 `agent_comm.ros_agent_srv.main()` 创建并持有 `RosEnv`、`RosAgent`；通过 `RosEnv` 读取动作库来创建 `RosAgent`，取得初始观察后等待人工输入。每轮由 `main()` 先调用 `RosAgent.get_action(human, obs)`，再调用 `RosEnv.step(action)`；`RosEnv` 只调用 Observation Manager 和 Behavior Executor，不调用 `RosAgent`。`mission_cli -> MissionService` 不得冒充此入口。先在实施计划中冻结 ROS 2 服务字段、创建者和持有者，再写对应骨架。
- **真实模块调用流**：`ros_agent_srv.main` -> `RosEnv.get_obs` -> `observation_manager_node` -> `RosEnv` -> `main`（等待人工输入）-> `RosAgent.get_action` -> `llm_node` -> `RosAgent` -> `main` -> `RosEnv.step` -> `sequence_executor` -> 各 AtomicAction 服务 -> `sequence_executor` -> `RosEnv.get_obs` -> `observation_manager_node` -> `RosEnv.step` -> `main`（等待下一轮输入）。对象按顺序传递 `ObservationResponse`、`HumanInput`、行为文本/`ExecuteBehaviorRequest`、逐项 `AtomicActionRequest/Response`、`ExecuteBehaviorResponse`、新 `ObservationResponse`，最终由 `step()` 返回 `(obs, reward, done, info)`。失败即停止剩余动作，但仍返回结果并再次观察；执行失败诊断在飞行扩展中保留，不能伪称上游 `info` 已携带该诊断。模块间调用与序列遍历必须真实发生，不能直接从入口构造最终结果。

## 模块边界

- **已有模块直接调用**：`observation_manager_node` 内部从现有 `Px4WorldStateAdapter`/`WorldStateAggregator` 读取 `WorldState`，不得用手填飞行状态替代生产数据源；`flight_atomic_action_server` 内部接现有 `SafetyExecutionGate -> SafetySupervisor -> Px4Ros2FlightExecutionBackend` 和 Safety Trace。测试可替换外部 PX4 Backend，但不能跳过 Gate 来证明受控链。`AtomicActionLibrary` 在其模块内部读取现有 Skill Registry；尚缺的描述字段显式标记为待实现。
- **只在所属模块内部占位**：`observation_manager_node` 调用真实状态源后，可暂时把未完成的文本化部分返回为带明确未实现标记的 `ObservationResponse`；`llm_node` 暂时返回固定的行为文本或明确失败，`RosAgent` 仍真实调用它，并至少完整提取固定两步行为交给 `RosEnv` 组装 `ExecuteBehaviorRequest`；`sequence_executor` 必须真实解析/遍历并调用对应 AtomicAction 服务，不得直接返回固定行为结果；`flight_atomic_action_server` 必须真实调用 Gate/Backend，只有内部未完成的 M6 验证部分可返回 `INCONCLUSIVE`。未实现的 `resolve_poi`、拍照、识别服务若进入 MX 测试，仍按目标服务名被调用，且其内部固定返回失败 `AtomicActionResponse`，不得伪造位置、照片或结论。所有固定对象的类型、字段、取值与触发条件在 MX 实施计划中冻结并在 Trace 标识来源；不得将固定成功当作真实能力。
- **默认行为**：无真实 LLM 的正常入口不自主生成可飞行为；仅显式启用的开发/集成测试配置允许 `llm_node` 内部返回固定合法行为序列以贯通动作链。配置未启用时返回失败事实并仍更新观察，不能静默派发飞行命令。
- **Safety 边界**：生产 Authority 为 `UNKNOWN` 时，固定飞行动作也必须经 Gate 拒绝且 Backend 零调用；批准路径只可在测试显式注入可信 Authority、冻结约束和 Backend 替身后验证，并标记为替身证据。不存在凭骨架返回值绕过 Safety 的捷径。

## 验收与后续

集成测试从目标入口而非直接调用子组件开始，证明每个目标名称模块实际被调用、对象逐节点传递；至少覆盖开发配置下固定两步行为的顺序与失败停止、Gate 拒绝不派发、替身批准后调用 Backend、再观察和等待人工反馈。逐项报告各模块内部哪些逻辑仍返回固定对象及其字段来源；替身链路不称为 PX4/Gazebo 端到端或自主任务成功。

MX 完成后才把固定行为逐步替换为 M5 的真实 LLM/行为解析和动作描述；占位对象不得长期混入正式测评。M4 的可信 Authority、Gate 装配和受控 SITL 工作可与 MX 的非飞行骨架并行，但 MX 的替身通过不能关闭 M4。

当前下一步先复核 M4 Gate 的可信 Authority 与执行边界，并按 MX 搭上游式主链骨架；之后在 M5 替换占位实现。M4 验收不能依赖尚不存在的 LLM；MX 的第一条集成测试必须走“初始观察 -> 人工输入 -> 固定行为 -> Behavior Executor -> 再观察”，不能沿用固定语句 `MissionService` 作为主链。
