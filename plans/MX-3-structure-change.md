# MX-3 结构变更单：行为序列贯通

状态：已确认；三个代码批次已实施，入口批次验证见 [MX 计划](MX.md)。依据：[当前规格](../SPEC_DEV.md)、[MX 计划](MX.md)、[服务契约](../contracts/rosllm_services.md)。

## 原因与范围

当前 `ros_agent_srv` 在 LLM 成功时仅记录 `BEHAVIOR_EXECUTOR_NOT_CONNECTED_MX2`，没有调用行为服务；`llm_node` 始终失败；`behavior_executor` 包不存在。因此现有入口无法验证两步行为、逐项结果传递或执行后再观察。当前项目的 LLM 服务名 `llm`、拟用的行为服务名 `behavior_executor` 也与 ROS-LLM 上游的 `call_llm`、`execute_behavior` 不一致。MX-3 拟分三个可独立验证的代码批次：

1. **统一服务名**：把[服务契约](../contracts/rosllm_services.md)中的 LLM、行为执行服务名分别改为上游的 `call_llm`、`execute_behavior`。同步修改已有 `agent_comm` 的 LLM 客户端、服务端及入口测试；新行为服务直接采用 `execute_behavior`。`get_observation` 和各动作自己的服务名保持原约定。此批仅迁移名称，不改变 `.srv` 字段、LLM 默认失败行为或调用拓扑。
2. **真实序列执行器**：新增 `ros2_ws/src/behavior_executor/` ROS 2 Python 包、`sequence_executor.py`、包元数据和测试。独立进程创建 `SequenceExecutor` 节点，提供 `execute_behavior` 服务。收到行为文本后，先完整解析和校验非空 JSON Lines 序列，再按顺序执行；每项只能含 `name`（动作服务名）和 `input`（参数文本），动作名须在已有动作库白名单内。逐项向同名 `AtomicAction` 服务发送 `input` 和紧邻前项的 `output`（首项为空串），失败立即停止；成功时返回整体成功，失败、解析错误或服务不可用时返回 `success=false` 和可读 `message`。不构造固定成功结果。服务回调使用可并行推进客户端响应的 executor，设置有限等待时间，避免死锁或无限阻塞。
3. **入口与固定行为**：修改 `agent_comm/ros_agent_srv.py`、`llm_node.py` 和入口测试。`RosAgent` 从 LLM 回复的单个 `json` 代码块中取出**完整**行为文本，保留其中两行 JSON 对象，不只取首项；格式不符则显式失败。`RosEnv.step(behavior)` 调用 `execute_behavior` 服务，随后无论执行成功或失败都调用 `get_obs()`，返回 `(obs, reward, done, info)`。`main()` 只在 LLM 成功且提取有效时调用 `step()`，记录执行结果并等待下一轮人工输入。默认 LLM 仍返回 `LLM_UNAVAILABLE_MX2`；仅 ROS 参数 `mx_fixed_behavior=true` 显式启用时，返回有来源标记的固定两步 `hold`、`rtl` 行为文本。该配置仅用于开发/集成测试，不代表模型生成或可飞批准。

本批不新增或修改 `.srv` 字段，不创建飞行原子动作服务，也不改变 Safety Gate、Authority 或 Backend。MX-3 的测试动作服务只存在于测试中，用来证明生产入口与真实序列执行器的服务调用；未实现的生产动作服务不得在运行时被测试替身冒充。MX-4 再接入真实飞行动作服务。

## 对象、函数与数据流

| 对象 | 创建、持有、触发 | 输入、输出、副作用 |
|---|---|---|
| `RosAgent`（已有） | `ros_agent_srv.main()` 创建并持有；人工输入且观察成功时调用 | `get_action(human, obs)` 接收文本与 `Observation.Response`，请求 `LLM`，成功时完整提取行为文本；失败返回明确失败事实，不执行动作。 |
| `RosEnv`（已有） | `main()` 创建并持有；有效行为到达时调用 | 新增 `step(behavior)`：发送 `ExecuteBehavior.Request`，再次请求 `Observation`；返回 `(Observation.Response, reward, done, info)`。参照上游默认 `beta=0.5`，`reward=-beta`（执行成功）或 `-2*beta`（失败），`done=False`，环境 `info={}`；执行器响应的整数 `info` 与失败 `message` 仅作执行日志/诊断，不伪装为环境 `info` 或 Safety 诊断。 |
| `LLMNode`（已有） | `llm_node.main()` 创建并持有；收到 LLM 请求时调用 | 默认仍返回失败；显式开发参数下返回带固定来源标记的两步行为回复，供提取和测试。 |
| `SequenceExecutor`（新增） | `sequence_executor.main()` 创建并持有；收到 `ExecuteBehavior.Request` 时调用 | 解析行为并依次调用动作服务，返回 `ExecuteBehavior.Response`；动作结果决定继续或停止。 |

数据流：人工输入 + 观察 + 动作库 -> `RosAgent` -> `LLMNode` -> 完整两步行为文本 -> `RosEnv.step` -> `SequenceExecutor` -> 各 `AtomicAction` 服务 -> `SequenceExecutor` -> `RosEnv` -> `ObservationManagerNode` -> `main()`。新增节点为 `SequenceExecutor`；`LLMNode` 增加显式开发模式，`RosEnv` 增加执行客户端。固定文本的来源应体现在 LLM `info` 和入口日志/测试证据中；动作服务测试替身不能作为真实飞行动作的成功证据。

## 影响、风险、回滚与验证

- **结构与接口影响**：新增 `behavior_executor` ROS 2 包及其对 `rclpy`、`rosllm_srvs` 的依赖；`agent_comm` 的运行流增加 `ExecuteBehavior` 客户端。拟把[既有契约](../contracts/rosllm_services.md)中的两个服务名统一到上游 `call_llm`、`execute_behavior`，并同步迁移已有 MX-2 客户端/服务端及测试；这是服务发现名称变更，会影响使用旧名称的启动配置或外部调用方。`.srv` 字段保持不变，行为文本格式及 `step()` 返回语义在本单冻结。
- **风险**：显式固定行为若与未来飞行服务同时运行，仍必须经过 MX-4 Gate；默认参数关闭。服务在回调内等待下一服务需要多线程 executor，测试须覆盖超时与首项失败。固定行为不声称自主规划；测试动作服务不证明 PX4/Gazebo 或 Safety。
- **回滚**：删除新包，撤销 `agent_comm` 的固定行为、提取与 `step()` 调用，并将契约及 LLM 客户端/服务端名称恢复为 `llm`，可回到 MX-2；无需回退已有服务字段或飞行代码。
- **验证**：先运行 MX-2 入口回归，证明 `call_llm` 新名称的客户端/服务端连通，并检查仓库运行代码中无旧服务名；再 `colcon build --packages-up-to behavior_executor agent_comm`，运行解析/失败停止测试，以及从真实 `ros_agent_srv.main()` 装配路径触发的初始观察 -> 人工输入 -> 固定两步 -> 两次 AtomicAction 调用 -> 再观察测试；核对默认 LLM 模式无行为派发、非法行为无动作调用、空序列失败、第二行非法时首项零调用、`step()` 的上游奖励与 `info` 语义、`git diff --check`。报告替身边界及实际生产链路，不称为 SITL 验证。

依据 [AGENT.md](../AGENT.md)，新增包与跨模块调用属于结构变更；确认本单后才实施上述代码批次。
