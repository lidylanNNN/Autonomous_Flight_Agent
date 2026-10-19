# MX-1 结构变更单

状态：已获确认，实施记录见 [MX 阶段计划](MX.md)。依据：[当前总体设计](../SPEC_DEV.md)。本单记录 MX-1 接口包与装配关系的结构变更；后续节点实现仍按 MX-2 至 MX-4 分批评审。

## 原因与范围

当前 `ros2_ws/src/` 没有 `rosllm_srvs`，因此主链四个 ROS 2 服务既无可编译类型，也无稳定的客户端与服务端约定。拟新增 `ros2_ws/src/rosllm_srvs/{package.xml,CMakeLists.txt,srv/*.srv}`，使用 `ament_cmake` 和 `rosidl_generate_interfaces` 生成 Python 服务类型。MX-1 不创建空的 `agent_comm`、`observation_manager`、`behavior_executor` 或 `rosllm_msgs` 包，也不修改既有 `flight_agent_ros` 飞行入口和 Safety 行为。

## ROS 2 字段

字段与失败语义以 [ROS-LLM 服务契约](../contracts/rosllm_services.md) 为唯一权威定义。相较上游，`Observation` 返回文本与来源，`LLM` 返回扁平字段；其余两个服务保留上游字段。

## 对象创建、持有与调用

MX-1 冻结目标关系，不把接口构建当成节点已接入：

| 对象 | 创建者与持有者 | 触发与输出 |
|---|---|---|
| `RosEnv` | `agent_comm.ros_agent_srv.main()` 创建并持有 | `main` 调用 `get_atomic_action_library()` 供 Agent 构造；`get_obs()` 请求 `Observation`；`step(behavior)` 请求 `ExecuteBehavior` 后再次请求 `Observation`，返回 `(obs, reward, done, info)`。 |
| `RosAgent` | `main()` 用动作库创建并持有 | 每轮人工输入后，`get_action(human, obs)` 请求 `LLM`，输出完整行为文本或显式失败。 |
| `observation_manager_node` | 独立 ROS 2 进程创建并持有观察服务 | 收到 `Observation` 请求后读取真实状态源；未完成的文本化可返回带来源标记的固定文本。 |
| `llm_node` | 独立 ROS 2 进程创建并持有 LLM 服务 | 收到 `LLM` 请求；默认无模型时返回失败，显式开发配置才允许固定行为。 |
| `sequence_executor` | 独立 ROS 2 进程创建并持有行为服务与动作客户端 | 收到 `ExecuteBehavior` 请求后逐项调用 `AtomicAction`；首项失败即停止，返回执行结果。 |
| `flight_atomic_action_server` | 独立 ROS 2 进程创建并持有飞行动作服务与 Gate/Backend | 收到 `AtomicAction` 请求，参数解析后经 Gate 决策；拒绝时不得调用 Backend。 |

本批不新增上述运行时类或函数。输入输出与副作用仅冻结在表中；真正实现和生产接入属于后续工作包。数据流为人工输入及观察文本 -> `RosAgent`/`LLM` -> 行为文本 -> `RosEnv`/`ExecuteBehavior` -> 动作请求及前项输出 -> `AtomicAction`；观察支路为真实状态源 -> `observation_manager_node` -> `Observation` -> `RosEnv`。本批仅新增服务类型节点，无运行时数据流节点。

## 影响、风险、回滚、验证

- 影响：新增一个 ROS 2 接口包、四个 `.srv` 和接口契约；现有 ROS 包尚不声明运行时依赖，后续节点落地时再加。接口字段一旦被后续包使用，修改会要求重建所有使用者。
- 风险：文本化 `Observation` 无图像字段；V1 不含视觉搜索和图像观察。LLM 扁平字段不提供上游请求时间信息；需要时须另行评审扩展。
- 回滚：MX-1 尚无客户端，删除新增包和接口契约即可回到当前构建状态；后续已有客户端时须同步回滚依赖包。
- 验证：`colcon build --packages-select rosllm_srvs`；加载工作区后执行四次 `ros2 interface show rosllm_srvs/srv/<Name>`；核对 `package.xml` 的 ROSIDL 声明及 `git diff --check`。此验证只证明接口可构建，不证明主链运行或 PX4/Gazebo 端到端。

本单依据 [AGENT.md](../AGENT.md) 的结构变更规则获确认后实施。
