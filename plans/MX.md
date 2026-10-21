# MX — 主流程骨架贯通

状态：进行中（MX-1、MX-2 已实施；行为序列与飞行动作尚未接入）

MX 在当前开发顺序中先于 M4 收口，再进入 M5；它不是新的任务决策循环。**调用链中的模块必须是 [SPEC_DEV.md 第 3.1、4 节](../SPEC_DEV.md)确定名称和职责的真实模块；占位只允许位于模块尚未开发的内部逻辑，不能用独立 Mock/Stub 模块顶替目标模块。**MX 可用替身验证外部 PX4/LLM 依赖，但真实飞行动作的批准和 SITL 验收仍受 M4 可信 Authority 前置条件约束。

## 入口与调用流

- **入口与触发**：目标 `agent_comm.ros_agent_srv.main()` 创建并持有 `RosEnv`、`RosAgent`；通过 `RosEnv` 读取动作库来创建 `RosAgent`，取得初始观察后等待人工输入。每轮的调用顺序见下项；`mission_cli -> MissionService` 不得冒充此入口。ROS 2 服务字段、创建者和持有关系在 MX-1 冻结。
- **真实模块调用流**：`ros_agent_srv.main` -> `RosEnv.get_obs` -> `observation_manager_node` -> `RosEnv` -> `main`（等待人工输入）-> `RosAgent.get_action` -> `llm_node` -> `RosAgent` -> `main` -> `RosEnv.step` -> `sequence_executor` -> 各 AtomicAction 服务 -> `sequence_executor` -> `RosEnv.get_obs` -> `observation_manager_node` -> `RosEnv.step` -> `main`（等待下一轮输入）。对象按顺序传递 `ObservationResponse`、`HumanInput`、行为文本/`ExecuteBehaviorRequest`、逐项 `AtomicActionRequest/Response`、`ExecuteBehaviorResponse`、新 `ObservationResponse`，最终由 `step()` 返回 `(obs, reward, done, info)`。失败即停止剩余动作，但仍返回结果并再次观察；执行失败诊断在飞行扩展中保留，不能伪称上游 `info` 已携带该诊断。模块间调用与序列遍历必须真实发生，不能直接从入口构造最终结果。

## 模块边界

- **已有模块直接调用**：`observation_manager_node` 内部从现有 `Px4WorldStateAdapter`/`WorldStateAggregator` 读取 `WorldState`，不得用手填飞行状态替代生产数据源；`flight_atomic_action_server` 内部接现有 `SafetyExecutionGate -> SafetySupervisor -> Px4Ros2FlightExecutionBackend` 和 Safety Trace。测试可替换外部 PX4 Backend，但不能跳过 Gate 来证明受控链。`RosEnv.get_atomic_action_library()` 读取动作库配置，描述生成复用现有 Skill Registry；尚缺的字段显式标记为待实现。
- **只在所属模块内部占位**：`observation_manager_node` 调用真实状态源后，可暂时把未完成的文本化部分返回为带明确未实现标记的 `ObservationResponse`；`llm_node` 暂时返回固定的行为文本或明确失败，`RosAgent` 仍真实调用它，并至少完整提取固定两步行为交给 `RosEnv` 组装 `ExecuteBehaviorRequest`；`sequence_executor` 必须真实解析/遍历并调用对应 AtomicAction 服务，不得直接返回固定行为结果；`flight_atomic_action_server` 必须真实调用 Gate/Backend，只有内部未完成的 M6 验证部分可返回 `INCONCLUSIVE`。未实现的 `resolve_poi`、拍照、识别服务若进入 MX 测试，仍按目标服务名被调用，且其内部固定返回失败 `AtomicActionResponse`，不得伪造位置、照片或结论。所有固定对象的类型、字段、取值与触发条件在 MX 实施计划中冻结并在 Trace 标识来源；不得将固定成功当作真实能力。
- **默认行为**：无真实 LLM 的正常入口不自主生成可飞行为；仅显式启用的开发/集成测试配置允许 `llm_node` 内部返回固定合法行为序列以贯通动作链。配置未启用时返回失败事实并仍更新观察，不能静默派发飞行命令。
- **Safety 边界**：生产 Authority 为 `UNKNOWN` 时，固定飞行动作也必须经 Gate 拒绝且 Backend 零调用；批准路径只可在测试显式注入可信 Authority、冻结约束和 Backend 替身后验证，并标记为替身证据。不存在凭骨架返回值绕过 Safety 的捷径。

## 子工期

按一人开发、现有 ROS 2/PX4 环境可用估算，单位为**有效开发日**，不含等待评审、环境故障或 M4 可信 Authority/SITL 收口。以下工作包按顺序交付，各有构建或测试证据；估算总计 **5.5–9 天**，不是项目完成日期。

| 工作包 | 本批产物与验收 | 前置 | 估算 |
|---|---|---|---|
| MX-1 接口与装配 | 已完成：四个服务字段与对象创建/持有关系已冻结，接口构建通过；[结构变更单](MX-1-structure-change.md)、[服务契约](../contracts/rosllm_services.md) | 现有包结构 | 1–1.5 天 |
| MX-2 有人入口与观察 | 已完成：`ros_agent_srv.main` 创建并调用 `RosEnv`、`RosAgent`，经 `observation_manager_node` 读取 PX4 状态；文本占位显式标源，默认 LLM 明确失败；入口测试覆盖初始观察、人工输入和再观察。见[结构变更单](MX-2-structure-change.md) | MX-1 | 1–1.5 天 |
| MX-3 行为序列 | 进行中：服务名称已统一；待完成 `RosAgent` 两步提取、真实 `sequence_executor` 逐项派发、失败停止及入口再观察。见[结构变更单](MX-3-structure-change.md) | MX-2 | 1–1.5 天 |
| MX-4 飞行动作接线 | 真实 `flight_atomic_action_server` 调用现有 Gate、Supervisor、Backend/替身与 Safety Trace；覆盖 `UNKNOWN` 拒绝不派发和测试注入批准路径 | MX-1、MX-3 | 1.5–2.5 天 |
| MX-5 主链回归与审计 | 从入口运行固定两步、失败停止、状态回读和 Trace 集成测试；列出仍返回固定对象的内部步骤与未接入服务 | MX-2 至 MX-4 | 1–2 天 |

## 验收与后续

MX-5 须从目标入口证明各模块被调用、对象逐节点传递，并报告仍返回固定对象的内部步骤。替身链路不称为 PX4/Gazebo 端到端或自主任务成功，也不进入正式测评。MX 完成后，M4 继续收口可信 Authority 和受控 SITL；M5 再替换固定返回与未实现的内部逻辑。

## MX-1 验证记录

基于 `929280e` 工作树（本批修改尚未提交），在本机 ROS 2 Jazzy 环境执行 `colcon build --packages-select rosllm_srvs`，结果为 `1 package finished`。加载 `/opt/ros/jazzy/setup.bash` 与 `ros2_ws/install/setup.bash` 后，四个 `ros2 interface show rosllm_srvs/srv/<Name>` 均显示与[服务契约](../contracts/rosllm_services.md)相符的字段。此证据只覆盖接口生成；节点、服务调用和 PX4/Gazebo 均尚未验证。

## MX-2 验证记录

2026-10-19 工作树（本批修改尚未提交）：`colcon build --packages-up-to observation_manager` 构建 4 包通过；`colcon build --packages-up-to agent_comm` 构建 2 包通过。加载 ROS 2 Jazzy 和工作区，并使用项目 `.venv` 执行观察服务测试及 `ros_agent_srv.main()` 入口集成测试，各 1 项通过。入口测试使用真实 `ObservationManagerNode`、`LLMNode`、ROS 2 服务调用及测试注入的 `WorldStateAggregator` 快照，证明初始观察 -> 人工输入 -> LLM 明确失败 -> 再观察；真实 PX4 Topic、Gazebo、`ExecuteBehavior`、Safety Gate 和 Backend 未参与。固定对象为 `MX_UNIMPLEMENTED_TEXT_SUMMARY state_id=<真实 ID>`（`source=px4_world_state_adapter:mx_placeholder`）和失败 LLM 响应（`info=LLM_UNAVAILABLE_MX2`）；生成的动作描述仍将 `input`/`output` 标为 `MX_UNIMPLEMENTED`，不以此校验 I/O 有效性。

生产入口为 `ros2 run observation_manager observation_manager_node`、`ros2 run agent_comm llm_node` 和 `ros2 run agent_comm ros_agent_srv` 三个进程。运行前须加载 ROS 2 Jazzy、工作区，并让 ROS 2 的系统 Python 能导入项目核心包及 `.venv` 依赖；当前仓库的 ROS 2 启动脚本用 `.venv` site-packages 和 `src` 加入 `PYTHONPATH`。本批额外验证了系统 Python 在相同路径设置下可以导入三个已安装入口；尚无 PX4/Gazebo 启动实测。

## MX-3 名称统一批次验证记录

2026-10-21 工作树（未提交）：LLM 服务客户端与服务端统一为 `call_llm`，未来行为服务名在契约中统一为 `execute_behavior`；后者尚未创建。加载 ROS 2 Jazzy 和工作区后，以项目 `.venv`、源码路径及 `ROS_LOG_DIR=/tmp/afa_mx3_ros_logs` 运行 `ros2_ws/src/agent_comm/test/test_ros_agent_srv.py`，结果 `1 passed`。该测试经真实 `ros_agent_srv.main()`、`RosAgent` 和 `LLMNode` 的 ROS 2 服务调用，证明新名称连通及失败后再观察；序列执行和 PX4/Gazebo 未参与。
