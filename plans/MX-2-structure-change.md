# MX-2 结构变更单：有人入口与观察

状态：已确认并实施；验证记录见 [MX 计划](MX.md)。依据：[AGENT.md](../AGENT.md)、[当前规格](../SPEC_DEV.md)、[MX 计划](MX.md)和[服务契约](../contracts/rosllm_services.md)。本单记录实施边界，不把 MX-2 记为完整行为闭环。

## 原因与范围

MX-1 只有可构建的服务类型；目前没有 `get_observation` 服务，也没有 `ros_agent_srv` 生产入口。MX-2 需要新增两个规格已列出的 ROS 2 包，分两批独立评审：

MX-1 已在 [`contracts/rosllm_services.md`](../contracts/rosllm_services.md) 与 `rosllm_srvs/srv/Observation.srv` 定义跨节点 Observation 契约：空请求，响应含 `success`、`observation`、`source`、`message`。本批冻结其服务端取值及失败条件，不新增字段。`Px4ParameterAdapter` 只读取 `MPC_XY_VEL_MAX` 供 Safety 的速度约束校验；它不是 `WorldState` 状态源，当前 Observation 契约也没有速度上限字段。因此 MX-2 的观察节点不创建或调用该适配器。如后续要把参数读数纳入观察，需单独明确用途、来源/新鲜度及契约变化，再按结构变更审查。

1. **MX-2a 观察服务**：新增 `observation_manager` 的包元数据、`observation_manager_node.py`、测试。节点创建并持有现有 `Px4WorldStateAdapter`，在同一 ROS 2 executor 中运行；`get_observation` 回调调用 `read_world_state()`。没有快照时返回 `success=false`、空观察和 `WORLD_STATE_UNAVAILABLE`。有快照时，暂以明确的 `MX_UNIMPLEMENTED_TEXT_SUMMARY` 文本和 `source=px4_world_state_adapter:mx_placeholder` 返回成功，并附带真实 `state_id`；不得把固定文本当成飞行事实或成功验证。真实状态来源仍是适配器订阅的 PX4 Topic 和 `WorldStateAggregator`。
2. **MX-2b 有人入口**：新增 `agent_comm` 的包元数据、动作库配置、`ros_agent_srv.py`、`llm_node.py` 和从入口驱动的集成测试。`main()` 创建并持有 `RosEnv` 和 `RosAgent`，先由 `RosEnv.get_obs()` 调用 `get_observation`，再读取终端人工输入。`RosAgent.get_action(human, obs)` 调用 `llm` 服务；本批 `llm_node` 仅返回显式失败（空回复、`info=LLM_UNAVAILABLE_MX2`），入口记录失败并再次取观察，继续等待人工输入。无行为时不调用 `ExecuteBehavior`，不派发飞行命令。

`RosEnv.get_atomic_action_library()` 从包内 YAML 读取动作名称索引，描述来自现有 Skill Registry；生成的描述把尚未实现的动作输入/输出标为 `MX_UNIMPLEMENTED`，该标记不用于 I/O 有效性校验，也不伪称可用于真实模型。`RosEnv.step()` 的执行请求和再观察属于 MX-3，本批不以空实现冒充闭环。MX-2b 可以在测试中替换服务端与终端输入，但测试必须从真实 `main()` 装配路径触发初始观察、人工输入和 Agent 调用。

## 对象、函数与数据流

| 对象 | 创建与持有 | 输入、输出与副作用 |
|---|---|---|
| `ObservationManagerNode` | `observation_manager_node.main()` 创建；持有 `Px4WorldStateAdapter`，executor 同时驱动两节点 | `Observation.Request` -> `Observation.Response`；调用真实状态读取，缺失时明确失败 |
| `RosEnv` | `ros_agent_srv.main()` 创建并持有 | `get_obs()` 发 `Observation.Request`、返回响应；`get_atomic_action_library()` 读 YAML 并返回动作描述 |
| `RosAgent` | `main()` 用动作库创建并持有 | `get_action(human, obs)` 发 `LLM.Request`；成功才返回行为文本，失败显式返回失败事实 |
| `LLMNode` | `llm_node.main()` 创建并持有 | `LLM.Request(prompt)` -> 失败响应；MX-2 不启用固定成功行为 |

生产触发为运行 `observation_manager_node`、`llm_node` 和 `ros_agent_srv`，操作员在终端输入任务。数据流：PX4 Topic -> `Px4WorldStateAdapter`/`WorldStateAggregator` -> `WorldState` -> `ObservationManagerNode` -> `Observation.Response` -> `RosEnv` -> `main()`；动作库 YAML/Skill Registry -> `RosEnv` -> `RosAgent`；终端人工输入 + 当前观察 -> `RosAgent` -> `LLMNode` -> 显式失败 -> `main()` -> 再次观察。新增节点为 `ObservationManagerNode` 和 `LLMNode`；其余新对象为入口内部协作者。`ExecuteBehavior`、原子动作、Gate 和 Backend 尚未接入该入口。

## 影响、风险、回滚与验证

- **结构影响**：新增两个 ROS 2 Python 包及 `rosllm_srvs`、`rclpy` 等运行依赖；观察包还依赖现有 `flight_agent_ros`/`px4_msgs`，入口包读取现有核心 Skill Registry。不修改 MX-1 `.srv` 字段、Safety 决策或 PX4 Backend；参数适配器继续由现有 Safety 装配使用。
- **风险**：ROS 2 服务等待与同进程状态订阅若共用单线程 executor 会卡住；观察服务和适配器由同一多线程 executor 驱动，客户端设置明确超时。状态文本暂为占位，必须保留来源字段；缺状态不得返回成功。
- **回滚**：删除两个新增包及本批测试、移除相应阶段记录即可恢复 MX-1；不需回退已有服务字段或飞行代码。
- **验证**：分别构建新包及其依赖；运行观察服务的有状态/无状态测试；从 `ros_agent_srv.main()` 装配路径测试初始观察、人工输入、LLM 失败后再观察、`ExecuteBehavior` 零调用；检查 `git diff --check`。替身服务测试仅证明生产软件调用链，不声称 PX4/Gazebo 端到端。

本结构变更按 [AGENT.md](../AGENT.md) 要求获用户确认后实施。MX-2a、MX-2b 的生产软件调用与验证记录见 [MX 计划](MX.md)。
